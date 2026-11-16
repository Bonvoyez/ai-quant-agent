# scheduler.py
import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo
from ai_client import AIClient
from history_manager import HistoryManager
from market_data import MarketDataCollector
from performance_tracker import PerformanceTracker
from telegram_service import TelegramService
from research_agents import MultiAgentOrchestrator
from toss_service import TossInvestService
from settings_manager import SettingsManager
from shadow_portfolio import ShadowPortfolio
from strategy_evaluator import StrategyEvaluator
from quant_rules import (
    confirm_swing_entry_price,
    inverse_volatility_weights,
    select_long_term_core_candidate,
    select_swing_trend_candidate,
)
from rotation_strategy import run_daily_cycle as run_rotation_daily_cycle
from breakout_strategy import BreakoutStrategyRunner
from market_calendar import is_trading_day
from utils import atomic_write

logger = logging.getLogger("StockReporter")
KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")

# 중복 추천 판단 시 참조할 최근 거래일 수
RECENT_HISTORY_DAYS = 5
# 코드 레벨에서 강제로 걸러낼 "최근 재추천 금지" 기간
HARD_DEDUP_DAYS = 2
# 한 포트폴리오 내 동일 섹터 최대 허용 개수
MAX_PER_SECTOR = 1
# 섹터가 달라도 동일한 위험선호 요인에 노출되는 종목군은 하루 한 종목만 보유한다.
MAX_PER_RISK_BUCKET = 1

# AI가 빈 목록을 내거나 그 추천이 실행 규칙에서 전부 탈락했을 때만 사용하는
# 보수적 규칙 기반 대체 진입 후보. '매일 채우기'가 아니라, 검증 가능한 추세가
# 있는 날에 주문 기회를 잃지 않기 위한 단일 후보 안전망이다.
# ATR 기반 손절/목표가 계산 최소/최대 클리핑 (리스크 모드와 무관하게 적용되는 안전판)
MIN_TARGET_PCT = 0.03
MIN_STOP_PCT = 0.02
MAX_STOP_PCT = 0.05

# [신규] 대시보드의 리스크 모드 선택에 따라 ATR 배율과 기준 예산 비율이 달라집니다.
# - target_mult/stop_mult: ATR%에 곱해서 목표가/손절가 폭을 결정
# - base_budget_ratio: 연패가 없을 때(streak=0) 사용하는 기본 예산 비율
# [수정] balanced/aggressive가 0.95였던 것을 낮췄습니다. 단 3종목(변동성 큰 모멘텀주)에
# 잔고의 95%를 매일 몰아넣으면, 셋 중 하나만 손절에 걸려도 하루 손실폭이 계좌 전체
# 기준으로 매우 커집니다. 이 값 자체가 "얼마를 걸지"를 결정하는 숫자이므로 정답은 없고
# 본인의 손실 허용 범위에 맞게 조정해야 하는 값입니다 — 아래는 보수적으로 낮춘 시작값입니다.
RISK_PROFILES = {
    "conservative": {"target_mult": 1.0, "stop_mult": 0.8, "base_budget_ratio": 0.35},
    "balanced":     {"target_mult": 1.5, "stop_mult": 1.0, "base_budget_ratio": 0.55},
    "aggressive":   {"target_mult": 2.0, "stop_mult": 1.2, "base_budget_ratio": 0.75},
}

# 연패(연속 손실일) 시 base_budget_ratio에 곱해서 추가로 축소하는 배율
STREAK_MULTIPLIER = {0: 1.0, 1: 0.84, 2: 0.63}
STREAK_MULTIPLIER_MAX_STREAK = 0.42  # 3연패 이상

# [신규] 강제청산 매도가 실패했을 때 재시도 횟수/간격
FORCED_SELL_MAX_RETRIES = 3
FORCED_SELL_RETRY_DELAY_SEC = 5
# 후보 신호는 전일 완결 일봉으로 만들고, 개장 후 30분 동안의 가격·거래량 확인 후 진입한다.
ENTRY_DELAY_AFTER_OPEN_MINUTES = 30
# 0.20% 왕복비용을 하루마다 내지 않도록, 일간 신호는 최대 5거래일 보유한다.
MAX_HOLDING_TRADING_DAYS = 5


class DailyScheduler:
    def __init__(self, session):
        self.ai = AIClient(session)
        self.history = HistoryManager("history.json")
        self.tracker = PerformanceTracker("performance_cache.json")
        self.telegram = TelegramService(session)
        self.orchestrator = MultiAgentOrchestrator(self.ai)
        # [신규] 대시보드에서 저장한 설정(일시정지/목표종목수/제외티커/리스크모드)을 읽습니다.
        self.settings = SettingsManager()
        self.strategy_evaluator = StrategyEvaluator()
        self.shadow_portfolio = ShadowPortfolio()
        self.market = MarketDataCollector

        dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
        toss_key = os.getenv("TOSS_APP_KEY", "")

        from broker import MockBroker
        if not toss_key and dry_run:
            logger.info("🧪 [Scheduler] Live broker credentials not detected with dry-run enabled -> Initialized MockBroker.")
            self.broker = MockBroker(initial_cash_usd=float(os.getenv("ROTATION_CAPITAL_CAP_USD", "1000") or 1000.0))
            self.toss = self.broker
        else:
            self.toss = TossInvestService(
                session=session,
                client_id=os.getenv("TOSS_APP_KEY", ""),
                client_secret=os.getenv("TOSS_APP_SECRET", ""),
                account_seq=os.getenv("TOSS_ACCOUNT_NO", "")
            )
            self.broker = self.toss

        # [핵심 수정] active_trades를 항상 빈 리스트로 시작하지 않고,
        # 파일(active_trades.json)에서 복원합니다.
        # 기존 문제: 이 리스트가 메모리에만 있어서 프로세스가 재시작되면
        # "어제/오늘 뭘 샀는지"에 대한 기억이 통째로 사라졌습니다. 그 결과
        # - 전일 보유 종목 강제매도 블록이 스킵되고
        # - 장중 목표가/손절가 감시도 그 종목에 대해서는 멈추고
        # - 실제 계좌에는 종목이 남아있는데 봇만 그 존재를 모르는 상태가 됐습니다.
        self.active_trades_file = "active_trades.json"
        self.active_trades = self._load_active_trades()
        self._last_reconciliation_at: Optional[datetime] = None

        self.cumulative_lessons = (
            "초기 상태: 중소형주 위주의 모멘텀 트레이딩. "
            "반드시 정확히 3개의 종목을 추천할 것. "
            "직전 거래일에 추천했던 종목은 절대 중복해서 추천하지 말 것."
        )

    # ------------------------------------------------------------------
    # [신규] active_trades 영속화
    # ------------------------------------------------------------------

    def _load_active_trades(self) -> list:
        """프로세스 시작 시 이전에 저장된 보유 종목 상태를 복원합니다."""
        if os.path.exists(self.active_trades_file):
            try:
                with open(self.active_trades_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        holding = [t for t in data if t.get("status") == "HOLDING"]
                        if holding:
                            tickers = [t["ticker"] for t in holding]
                            logger.info(f"📂 이전 세션의 보유 종목을 복원했습니다: {tickers}")
                        return data
            except Exception as e:
                logger.error(f"active_trades.json 읽기 실패, 빈 상태로 시작합니다: {e}")
        return []

    async def _save_active_trades(self) -> None:
        """
        현재 active_trades 상태를 즉시 파일에 저장합니다.
        매수 직후, 상태 변경(익절/손절/강제청산) 직후 등 상태가 바뀔 때마다
        호출하여, 다음 저장 시점 전에 재시작되더라도 최대한 최신 상태를 보존합니다.
        """
        try:
            await asyncio.to_thread(atomic_write, self.active_trades_file, self.active_trades)
        except Exception:
            logger.exception("active_trades.json 저장 실패")

    # ------------------------------------------------------------------
    # 장중 감시
    # ------------------------------------------------------------------

    async def monitor_active_trades(self):
        """장중 10초 단위로 목표가/손절가 도달 여부를 실시간 감시"""
        TOSS_FEE_RATE = 0.001
        changed = False
        prices = {}

        # 감시는 10초마다 돌지만 계좌 동기화는 API 호출량을 제한해 5분마다만 한다.
        # 따라서 수동 매도는 다음 정기 매매 시점까지 기다리지 않고 반영된다.
        now = datetime.now(KST)
        if (self._last_reconciliation_at is None
                or now - self._last_reconciliation_at >= timedelta(minutes=5)):
            await self.reconcile_active_trades()
            self._last_reconciliation_at = now

        for trade in self.active_trades:
            if trade.get("status") != "HOLDING":
                continue

            ticker = trade["ticker"]
            target = trade["target_price"]
            stop = trade["stop_loss_price"]
            entry_price = trade.get("entry_price", 0)

            current_price = await self.toss.get_realtime_price(ticker)
            if not current_price:
                continue
            prices[ticker] = current_price

            if current_price >= target:
                success = await self.toss.sell_market_order(ticker)
                if success:
                    msg = f"🎯 [자동 익절 완료] {ticker}\n📈 목표가 도달: ${target:.2f} (체결가: ${current_price:.2f})"
                    await self.telegram.send_message(msg)
                    trade["status"] = "SOLD"
                    changed = True

                    if entry_price > 0:
                        net_return = ((current_price - entry_price) / entry_price - TOSS_FEE_RATE * 2) * 100
                        await self.history.update_actual_return(
                            trade.get("recommended_date", ""), ticker, net_return
                        )
                else:
                    # [신규] 매도가 실패했을 때, "API 에러라 실패한 것"과 "애초에 그 종목을
                    # 실제로 보유하고 있지 않아서 실패한 것"을 구분합니다. 후자인데도 계속
                    # 재시도만 하면 10초마다 영원히 실패하는 매도 시도가 반복됩니다 (실제로
                    # active_trades.json이 실제 계좌 상태와 어긋났을 때 발생했던 문제).
                    await self._handle_possible_phantom_position(trade, "익절")
                    changed = True

            elif current_price <= stop:
                success = await self.toss.sell_market_order(ticker)
                if success:
                    msg = f"🚨 [자동 손절 이탈] {ticker}\n📉 손절가 하회: ${stop:.2f} (체결가: ${current_price:.2f})"
                    await self.telegram.send_message(msg)
                    trade["status"] = "SOLD"
                    changed = True

                    if entry_price > 0:
                        net_return = ((current_price - entry_price) / entry_price - TOSS_FEE_RATE * 2) * 100
                        await self.history.update_actual_return(
                            trade.get("recommended_date", ""), ticker, net_return
                        )
                else:
                    await self._handle_possible_phantom_position(trade, "손절")
                    changed = True

        if changed:
            await self._save_active_trades()

        # 종목별 손절과 별도로, 당일 봇 포트폴리오 전체의 추정 손실이 설정 한도에
        # 도달하면 남은 포지션도 정리한다. 이전 구현의 max_daily_loss_pct는 다음 날
        # 신규 진입만 막았지 당일 손실을 제한하지 못했다.
        await self._enforce_intraday_loss_limit(prices)

    async def _enforce_intraday_loss_limit(self, prices: dict) -> None:
        settings = self.settings.load()
        holdings = [t for t in self.active_trades if t.get("status") == "HOLDING"]
        if not holdings:
            return

        account_reference = max(
            (float(t.get("account_reference_usd", 0)) for t in holdings), default=0.0
        )
        if account_reference <= 0:
            # 이전 버전에서 만든 포지션은 기준 계좌금액이 없어 이 새 안전장치의
            # 계산 대상에서 제외한다. 종목별 목표/손절 감시는 그대로 적용된다.
            return

        estimated_pnl = 0.0
        priced_holdings = []
        for trade in holdings:
            price = prices.get(trade.get("ticker"))
            entry = float(trade.get("entry_price", 0))
            allocated = float(trade.get("allocated_budget_usd", 0))
            if price and entry > 0 and allocated > 0:
                estimated_pnl += allocated * ((price - entry) / entry)
                priced_holdings.append((trade, price))

        loss_pct = estimated_pnl / account_reference * 100
        if not priced_holdings or loss_pct > -settings["max_daily_loss_pct"]:
            return

        report = (
            f"🛑 [당일 계좌 손실 차단] 추정 손익 {loss_pct:+.2f}%로 "
            f"설정 한도(-{settings['max_daily_loss_pct']:.2f}%)에 도달했습니다. 남은 봇 포지션을 정리합니다.\n"
        )
        changed = False
        for trade, price in priced_holdings:
            if await self.toss.sell_market_order(trade["ticker"]):
                trade["status"] = "SOLD_DAILY_LOSS_LIMIT"
                entry = float(trade["entry_price"])
                net_return = ((price - entry) / entry - 0.002) * 100
                await self.history.update_actual_return(trade.get("recommended_date", ""), trade["ticker"], net_return)
                report += f"• {trade['ticker']}: 추정 순수익 {net_return:+.2f}%\n"
                changed = True
            else:
                report += f"• {trade['ticker']}: 매도 실패, 다음 감시 주기에 재시도\n"
        if changed:
            await self._save_active_trades()
        await self.telegram.send_message(report)

    async def _handle_possible_phantom_position(self, trade: dict, context: str) -> None:
        """
        [신규] 매도 주문이 실패했을 때, 실제 매도 가능 수량을 조회해서 원인을 구분합니다.
        - 수량이 0: 애초에 봇의 기록(active_trades.json)과 실제 계좌 상태가 어긋난 것
          (수동 매도, 이전 세션의 매도 실패 후 상태 불일치 등). 이 경우 재시도해봐야
          영원히 같은 이유로 실패하므로, 추적 목록에서 제거하고 운영자에게 알립니다.
        - 수량이 0보다 큼: 실제로 보유 중인데 API 오류 등으로 매도가 실패한 것이므로,
          기존처럼 상태를 HOLDING으로 유지해 다음 주기에 재시도합니다.
        """
        ticker = trade["ticker"]
        sellable_qty = await self.toss.get_sellable_quantity(ticker)

        if sellable_qty is None:
            logger.error("❌ %s 실제 보유 수량 조회 실패. 추적 상태를 보존하고 다음 주기에 재시도합니다.", ticker)
            return

        if sellable_qty <= 0:
            logger.warning(
                f"⚠️ {ticker} 매도 실패가 반복되어 실제 보유 수량을 확인한 결과 0주입니다. "
                f"봇 기록과 실제 계좌 상태가 어긋난 것으로 판단해 추적 목록에서 제거합니다."
            )
            trade["status"] = "PHANTOM_REMOVED"
            await self.telegram.send_message(
                f"⚠️ [기록 불일치 감지] {ticker}\n"
                f"봇은 이 종목을 보유 중으로 기록하고 있었지만({context} 시도 중 발견), "
                f"실제 계좌에는 매도 가능 수량이 0입니다. 추적을 중단했습니다.\n"
                f"실제 계좌 상태를 한 번 확인해주세요 (수동 매매, 이전 세션 오류 등의 가능성)."
            )
        else:
            logger.error(f"❌ {ticker} 매도 실패 (실제 보유 {sellable_qty}주 확인됨). 다음 주기에 재시도합니다.")

    async def reconcile_active_trades(self) -> None:
        """실계좌를 기준으로 봇의 보유 기록을 정리한다.

        수동 매도는 정상적인 운영 행위이므로 오류로 취급하지 않는다. 다만 매도 체결가를
        API로 확정할 수 없으므로 성과 수익률을 추정해 history에 쓰지 않는다.
        """
        changed = False
        reconciled = []
        for trade in self.active_trades:
            if trade.get("status") != "HOLDING":
                continue
            ticker = trade.get("ticker")
            if not ticker:
                continue
            quantity = await self.toss.get_sellable_quantity(ticker)
            if quantity is None:
                logger.warning("계좌 동기화 보류: %s 수량을 조회하지 못했습니다.", ticker)
                continue
            if quantity <= 0:
                trade["status"] = "RECONCILED_EXTERNAL_EXIT"
                reconciled.append(ticker)
                changed = True

        if changed:
            await self._save_active_trades()
            await self.telegram.send_message(
                "✅ [계좌 동기화] " + ", ".join(reconciled) +
                "은(는) 실제 계좌에서 이미 청산된 것으로 확인되어 봇 추적에서 제거했습니다. "
                "수동 매도 체결가는 확인하지 못해 성과에는 자동 반영하지 않았습니다."
            )

    # ------------------------------------------------------------------
    # 코드 레벨 안전장치
    # ------------------------------------------------------------------

    def _filter_duplicate_recommendations(self, stocks_list: list, recent_tickers: list) -> list:
        filtered = [s for s in stocks_list if s.get("ticker") not in recent_tickers]
        removed = [s.get("ticker") for s in stocks_list if s.get("ticker") in recent_tickers]

        if removed:
            logger.warning(f"⚠️ 최근 {HARD_DEDUP_DAYS}거래일 내 중복 추천 감지 및 제거: {removed}")

        if not filtered:
            logger.error("❌ 필터링 후 추천 종목이 0개가 되어, 원본 리스트를 그대로 사용합니다 (검토 필요).")
            return stocks_list

        return filtered

    def _filter_sector_concentration(self, stocks_list: list, candidates_meta: dict) -> list:
        sector_count: Dict[str, int] = {}
        filtered = []
        dropped = []

        for s in stocks_list:
            ticker = s.get("ticker")
            sector = candidates_meta.get(ticker, {}).get("sector", "Unknown")
            count = sector_count.get(sector, 0)

            if count >= MAX_PER_SECTOR:
                dropped.append((ticker, sector))
                continue

            sector_count[sector] = count + 1
            filtered.append(s)

        if dropped:
            logger.warning(f"⚠️ 섹터 집중(동일 섹터 {MAX_PER_SECTOR}개 초과) 감지 및 제외: {dropped}")

        return filtered

    def _filter_risk_bucket_concentration(self, stocks_list: list, candidates_meta: dict) -> list:
        """섹터 분산 뒤에도 남는 성장주/크립토 등 공통 요인 쏠림을 제한한다."""
        bucket_count: Dict[str, int] = {}
        filtered = []
        dropped = []

        for stock in stocks_list:
            ticker = stock.get("ticker")
            bucket = candidates_meta.get(ticker, {}).get("risk_bucket", "other")
            count = bucket_count.get(bucket, 0)
            if bucket != "other" and count >= MAX_PER_RISK_BUCKET:
                dropped.append((ticker, bucket))
                continue
            bucket_count[bucket] = count + 1
            filtered.append(stock)

        if dropped:
            logger.warning("⚠️ 공통 위험요인(%s) 집중으로 제외: %s", MAX_PER_RISK_BUCKET, dropped)
        return filtered

    def _apply_atr_based_risk_levels(self, stock: dict, entry_price: float, candidates_meta: dict, risk_profile: dict) -> tuple:
        ticker = stock.get("ticker")
        atr_pct = candidates_meta.get(ticker, {}).get("atr_pct")

        if not atr_pct or atr_pct <= 0:
            target_pct = 0.05
            stop_pct = 0.03
        else:
            target_pct = max(MIN_TARGET_PCT, (atr_pct / 100) * risk_profile["target_mult"])
            stop_pct = min(MAX_STOP_PCT, max(MIN_STOP_PCT, (atr_pct / 100) * risk_profile["stop_mult"]))

        target_price = entry_price * (1 + target_pct)
        stop_loss_price = entry_price * (1 - stop_pct)
        return target_price, stop_loss_price

    def _get_budget_ratio(self, streak: int, risk_profile: dict) -> float:
        mult = STREAK_MULTIPLIER.get(streak, STREAK_MULTIPLIER_MAX_STREAK) if streak < 3 else STREAK_MULTIPLIER_MAX_STREAK
        return risk_profile["base_budget_ratio"] * mult

    def _get_last_completed_day_return(self):
        """Return the latest fully settled day's equal-weight return, if available."""
        for record in reversed(self.history.load_all_records()):
            stocks = record.get("stocks", [])
            if not stocks:
                continue
            returns = [s.get("actual_return") for s in stocks]
            if all(isinstance(value, (int, float)) for value in returns):
                return sum(returns) / len(returns)
            return None
        return None

    def _filter_tradeable_recommendations(self, stocks_list: list, candidates_meta: dict, settings: dict) -> list:
        """Enforce objective entry rules; do not trust an LLM response as an order list."""
        allowed = []
        rejected = []
        min_confidence = settings["min_confidence"]
        max_atr_pct = settings["max_atr_pct"]
        for stock in stocks_list:
            ticker = (stock.get("ticker") or "").upper()
            meta = candidates_meta.get(ticker)
            try:
                confidence = float(stock.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            atr_pct = meta.get("atr_pct") if meta else None
            if not meta:
                rejected.append(f"{ticker or 'N/A'}: 후보목록 외")
            elif confidence < min_confidence:
                rejected.append(f"{ticker}: 확신도 {confidence:.2f} < {min_confidence:.2f}")
            elif not isinstance(atr_pct, (int, float)) or atr_pct > max_atr_pct:
                rejected.append(f"{ticker}: ATR {atr_pct}% > {max_atr_pct:.2f}%")
            else:
                normalized = dict(stock)
                normalized["ticker"] = ticker
                normalized["confidence"] = confidence
                allowed.append(normalized)
        if rejected:
            logger.warning("🚫 주문 전 객관 규칙으로 후보 제외: %s", "; ".join(rejected))
        return allowed

    @staticmethod
    def _select_quality_trend_fallback(candidates_meta: dict, settings: dict) -> list:
        """검증 전략이 아직 승격 전일 때 대형 유동주 한 종목만 보완한다.

        60건의 페이퍼 표본이 쌓이기 전까지 실거래를 완전히 멈추면 전략 자체의
        실전 체결 품질을 검증할 수도 없다. 이 단계는 LLM의 확신도를 주문 근거로
        쓰지 않고, 유동성이 높은 종목의 완결 일봉 추세와 제한적인 눌림만 사용한다.
        하락장 회피, ATR 한도, 실적 제외, 개장 확인은 모두 그대로 통과해야 하므로
        매일 채우기 위한 규칙은 아니다.
        """
        selected = select_swing_trend_candidate(candidates_meta, float(settings["max_atr_pct"]))
        if not selected:
            return []
        selected["confidence"] = max(float(settings["min_confidence"]), selected["confidence"])
        return [selected]

    @staticmethod
    def _holding_age_trading_days(trade: dict, current_date: str) -> int:
        """Count completed US weekdays after entry; weekends do not consume hold time."""
        try:
            entry = date.fromisoformat(str(trade.get("recommended_date", "")))
            current = date.fromisoformat(current_date)
        except ValueError:
            return 0
        if current <= entry:
            return 0
        return sum(
            1 for offset in range(1, (current - entry).days + 1)
            if (entry + timedelta(days=offset)).weekday() < 5
        )

    async def _close_expired_swing_positions(self, current_date: str) -> None:
        """Exit only after the planned swing holding period, not every morning."""
        holdings = [trade for trade in self.active_trades if trade.get("status") == "HOLDING"]
        expired = [
            trade for trade in holdings
            if trade.get("strategy_type") != "long_term_core"
            if self._holding_age_trading_days(trade, current_date) >= MAX_HOLDING_TRADING_DAYS
        ]
        if not expired:
            return

        report = "💸 [스윙 보유기간 만료 청산]\n\n"
        remaining = [trade for trade in holdings if trade not in expired]
        for trade in expired:
            ticker = trade["ticker"]
            sold = False
            for attempt in range(FORCED_SELL_MAX_RETRIES):
                sold = await self.toss.sell_market_order(ticker)
                if sold:
                    break
                if attempt < FORCED_SELL_MAX_RETRIES - 1:
                    await asyncio.sleep(FORCED_SELL_RETRY_DELAY_SEC)
            if not sold:
                remaining.append(trade)
                report += f"• {ticker}: ⚠️ 매도 실패, 계속 추적\n"
                continue

            entry_price = float(trade.get("entry_price", 0))
            exit_price = await self.toss.get_realtime_price(ticker) or entry_price
            net_return = ((exit_price - entry_price) / entry_price - 0.002) * 100 if entry_price > 0 else 0.0
            await self.history.update_actual_return(trade.get("recommended_date", ""), ticker, net_return)
            report += f"• {ticker}: {MAX_HOLDING_TRADING_DAYS}거래일 보유 후 순수익 {net_return:+.2f}%\n"

        self.active_trades = remaining
        await self._save_active_trades()
        await self.telegram.send_message(report)

    # ------------------------------------------------------------------
    # 메인 루프
    # ------------------------------------------------------------------

    async def run(self) -> None:
        TOSS_FEE_RATE = 0.001

        logger.info("Autonomous Trading Loop Active: Scheduled at 09:30 AM + 30m NY Time on US Trading Days.")

        saved_lessons = self.history.get_lessons(default="")
        if saved_lessons:
            self.cumulative_lessons = saved_lessons
            logger.info("🧠 Loaded cumulative LLM learning lessons from storage.")

        last_record = self.history.load_last_record()
        last_run_date = last_record.get("date") or None
        if last_run_date:
            logger.info(f"📂 Resumed session. Last recorded execution date: {last_run_date}")

        # [Mission-Critical Fault Tolerance & Crash Recovery]
        # 장중 또는 장외 언제든 봇이 재시작되더라도, 디스크에 기록된 활성 포지션을 복원하여
        # 실시간 손절/트레일링 익절 감시 태스크를 즉각 재가동합니다.
        self.breakout_runner = BreakoutStrategyRunner(
            self.toss,
            self.telegram,
            ai_client=self.ai,
            settings_manager=self.settings,
        )
        if self.breakout_runner.active_positions:
            active_symbols = list(self.breakout_runner.active_positions.keys())
            logger.info(
                f"🛡️ [Crash Recovery] Detected {len(active_symbols)} open breakout position(s) from disk: "
                f"{', '.join(active_symbols)}. Resuming real-time intraday exit monitoring immediately."
            )
            asyncio.create_task(self.breakout_runner.run_intraday_exit_loop())

        while True:
            try:
                now = datetime.now(KST)
                now_ny = now.astimezone(NEW_YORK)
                today_str = now.strftime("%Y-%m-%d")
                is_trading, market_status = is_trading_day(now_ny)

                # [서머타임 및 공휴일 자동 대응]
                # 1. 서머타임(DST): ZoneInfo("America/New_York") 기준으로 09:30을 계산하므로
                #    한국 시각 기준 밤 10:30(서머타임)과 밤 11:30(겨울 표준시)을 시스템이 자동 보정합니다.
                # 2. 미국 공휴일(Holidays): market_calendar를 통해 주말 및 공식 10대 휴장일(신정, MLK,
                #    프레지던츠데이, 굿프라이데이, 메모리얼데이, 준틴스, 독립기념일, 노동절, 추수감사절,
                #    크리스마스 등)을 자동 감지하여 휴장일에는 트리거하지 않고 안전하게 대기합니다.
                trigger_dt = (now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
                              + timedelta(minutes=ENTRY_DELAY_AFTER_OPEN_MINUTES))
                should_trigger = (
                    is_trading
                    and now_ny >= trigger_dt
                    and last_run_date != today_str
                )

                if should_trigger:
                    user_settings = self.settings.load()
                    strategy_mode = str(user_settings.get("strategy_mode") or os.getenv("STRATEGY_MODE", "rotation")).lower()
                    if strategy_mode == "rotation":
                        try:
                            await run_rotation_daily_cycle(self.toss, self.telegram)
                        except Exception as rotation_error:
                            logger.exception("로테이션 사이클 실패")
                            await self.telegram.send_message(f"[로테이션 오류] {rotation_error}")
                        last_run_date = today_str
                        continue
                    elif strategy_mode == "breakout":
                        try:
                            logger.info("🚀 [Breakout Strategy] Pre-market screening & autonomous execution triggered.")
                            runner = self.breakout_runner
                            # candidate features will be gathered via pre-market data
                            candidates = await self.market.get_breakout_candidates_premarket() if hasattr(self.market, "get_breakout_candidates_premarket") else []
                            if candidates:
                                await runner.screen_and_enter(candidates)
                                asyncio.create_task(runner.run_intraday_exit_loop())
                        except Exception as breakout_error:
                            logger.exception("브레이크아웃 사이클 실패")
                            await self.telegram.send_message(f"[브레이크아웃 오류] {breakout_error}")
                        last_run_date = today_str
                        continue
                    # 주문 판단 전에 실계좌를 한 번 기준점으로 삼는다. 수동 매도 뒤에
                    # 오래된 로컬 기록 때문에 재매도를 시도하는 일을 막는다.
                    await self.reconcile_active_trades()
                    # [신규] 대시보드에서 저장한 설정을 매 사이클 시작 시점에 새로 읽습니다.
                    # 즉 대시보드에서 값을 바꾸면 다음 사이클부터 바로 반영됩니다.
                    user_settings = self.settings.load()
                    # 전일 보유 종목을 아래에서 먼저 정산한 후에 성과 평가를 다시 한다.
                    # 그래야 방금 확정된 수익률까지 다음 신규 매수의 위험 한도에 반영된다.
                    settings = dict(user_settings)

                    logger.info("미국 정규장 오픈! 스윙 포지션 만기 확인 및 신규 신호 평가를 시작합니다.")
                    # 이전에는 다음 날 아침마다 전량 매도해 0.20% 왕복비용을 반복 지불했다.
                    # 이제 손절/익절 외에는 최대 5거래일만 보유하고 만기 때 정산한다.
                    await self._close_expired_swing_positions(today_str)

                    # 실거래와 별도로 고정 규칙 전략들을 같은 청산 시각·수수료 가정으로
                    # 정산한다. 이 데이터는 전략 승격 판단에만 쓰이며 주문을 만들지 않는다.
                    paper_settled = await self.shadow_portfolio.settle_open_positions(self.toss, today_str)
                    if paper_settled:
                        summary = ", ".join(
                            f"{trade['strategy']} {trade['ticker']} {trade['return_pct']:+.2f}%"
                            for trade in paper_settled
                        )
                        await self.telegram.send_message(f"🧪 [페이퍼 전략 정산] {summary}")

                    strategy_evaluation = self.strategy_evaluator.evaluate(self.history.load_all_records())
                    settings = self.strategy_evaluator.apply_safety_governor(user_settings, strategy_evaluation)
                    risk_profile = RISK_PROFILES.get(settings["risk_mode"], RISK_PROFILES["balanced"])
                    logger.info(
                        "⚙️ 이번 사이클 설정: 사용자=%s | 검증정책=%s | 실제적용=%s",
                        user_settings, strategy_evaluation["policy"], settings,
                    )

                    # [신규] 대시보드에서 "일시정지"를 켜둔 경우, 기존 보유 종목 매도(위에서
                    # 이미 처리됨)까지만 하고 신규 매수는 건너뜁니다. AI 분석 자체도
                    # 호출하지 않아 불필요한 API 비용도 아낍니다.
                    if settings["paused"]:
                        await self.telegram.send_message(
                            "⏸ [일시정지] 대시보드 설정에 따라 신규 매수를 건너뜁니다. 기존 보유 종목 매도만 수행했습니다."
                        )
                        last_run_date = today_str
                        continue

                    # 2. 새로운 종목 분석 시작
                    history_context = self.history.get_recent_history_text(days=RECENT_HISTORY_DAYS)
                    recent_tickers_hard = self.history.get_recent_tickers(days=HARD_DEDUP_DAYS)

                    await self.telegram.send_message("🤖 AI 퀀트가 최근 추천 이력과 오늘 시장 데이터를 함께 분석 중입니다...")

                    report = await self.orchestrator.execute_pipeline(
                        history_context=history_context,
                        cumulative_lessons=self.cumulative_lessons,
                        settings=settings,
                    )

                    # AI의 역할은 주문 추천이 아니라, 동일한 입력 데이터를 두고 만든
                    # 반증 가능한 리서치 메모다. 규칙 엔진의 실행 권한과 섞지 않는다.
                    research_note = str(report.get("research_note", "")).strip()
                    invalidation = str(report.get("invalidation_condition", "")).strip()
                    if research_note or invalidation:
                        await self.telegram.send_message(
                            "🧠 [AI 리서치 메모 · 주문 비권한]\n"
                            + (research_note or "요약 없음")
                            + (f"\n무효화 조건: {invalidation}" if invalidation else "")
                        )

                    candidates_meta = report.get("_candidates_meta", {})
                    # 시장 국면은 뉴스 요약이나 LLM의 BULL/BEAR 라벨이 아니라
                    # 완결된 SPY/VIX 데이터로 결정한다. 모델은 연구와 설명에만 쓰며
                    # 주문 허가권은 갖지 않는다.
                    regime = await MarketDataCollector.get_market_regime()
                    if not regime.get("trade_allowed"):
                        await self.telegram.send_message(
                            "🧭 [장기 코어 적립] 단기 시장 필터는 미통과지만, 개별주 매매 대신 "
                            "소액 SPY/QQQ 적립 후보만 평가합니다. "
                            f"({regime.get('reason', '시장 조건 미통과')})"
                        )

                    # 매일 세 개의 고정 전략을 같은 조건에서 가상 진입시킨다. 아직은
                    # 실계좌 주문과 분리되어 있어, 성과가 좋아 보여도 표본 전에는 승격되지 않는다.
                    paper_opened = await self.shadow_portfolio.open_positions(candidates_meta, self.toss, today_str)
                    if paper_opened:
                        logger.info("🧪 페이퍼 전략 진입: %s", [
                            f"{trade['strategy']}:{trade['ticker']}" for trade in paper_opened
                        ])
                    open_tickers = {
                        trade.get("ticker") for trade in self.active_trades
                        if trade.get("status") == "HOLDING"
                    }
                    target_count = max(0, settings["max_positions"] - len(open_tickers))

                    # 기존 페이퍼 전략은 '다음 날 청산' 성과를 측정한다. 이제 실거래는
                    # 최대 5거래일을 보유하므로, 서로 다른 보유기간의 점수를 섞어
                    # 승격시키지 않는다. 실거래 후보는 새 저회전 규칙 하나로 고정한다.
                    # 장기 코어 ETF는 단기 모멘텀·AI 서사를 요구하지 않는다. 단기
                    # 전략을 원할 때만 기존 추세 선별 규칙으로 바꿀 수 있다.
                    stocks_list = [candidate] if (candidate := select_long_term_core_candidate(candidates_meta)) else []
                    fallback_ticker = stocks_list[0]["ticker"] if stocks_list else None
                    if fallback_ticker:
                        await self.telegram.send_message(
                            f"🧭 [장기 코어 적립] {fallback_ticker} 한 종목을 "
                            "분할 매수 후보에 넣습니다. 비정상 갭·손실 한도는 그대로 적용됩니다."
                        )
                    else:
                        await self.telegram.send_message(
                                "🧭 [장기 코어 적립] SPY/QQQ 가격 데이터가 없어 주문을 만들지 않습니다."
                        )

                    if target_count == 0:
                        await self.telegram.send_message(
                            f"📦 [보유 한도 도달] 스윙 포지션 {len(open_tickers)}/{settings['max_positions']}개 보유 중이라 신규 매수는 생략합니다."
                        )
                        await self.history.append_or_update_history(today_str, [], {})
                        last_run_date = today_str
                        continue

                    if len(stocks_list) > target_count:
                        stocks_list = stocks_list[:target_count]

                    stocks_list = self._filter_duplicate_recommendations(stocks_list, recent_tickers_hard)
                    stocks_list = self._filter_sector_concentration(stocks_list, candidates_meta)
                    stocks_list = self._filter_risk_bucket_concentration(stocks_list, candidates_meta)

                    # AI 프롬프트를 우회한 티커, 낮은 확신도, 과도한 변동성 종목은 주문하지
                    # 않습니다. LLM의 설명은 참고자료일 뿐 매수 권한이 아닙니다.
                    stocks_list = self._filter_tradeable_recommendations(stocks_list, candidates_meta, settings)
                    stocks_list = [
                        stock for stock in stocks_list
                        if stock.get("ticker") not in open_tickers
                    ]

                    # [신규] 블랙리스트 방어적 필터링. research_agents.py의 프롬프트에서
                    # 이미 제외를 지시하지만, AI가 지시를 놓칠 경우를 대비한 마지막 안전장치입니다.
                    excluded_set = {t.upper() for t in settings["excluded_tickers"]}
                    before_blacklist = len(stocks_list)
                    stocks_list = [s for s in stocks_list if (s.get("ticker") or "").upper() not in excluded_set]
                    if len(stocks_list) < before_blacklist:
                        logger.warning("⚠️ AI가 블랙리스트 종목을 추천하여 코드 레벨에서 제거했습니다.")

                    # 최대 종목 수는 상한이다. 위의 단일 대체 후보 외에는 빈 자리를
                    # 자동으로 채우지 않으며, 신호가 없을 때 현금도 정상 포지션이다.

                    if not stocks_list:
                        await self.telegram.send_message("💡 주문 규칙을 통과한 후보가 없어 오늘은 현금을 유지합니다.")
                        await self.history.append_or_update_history(today_str, [], {})
                        last_run_date = today_str
                        continue

                    total_balance_usd = await self.toss.get_balance()
                    if total_balance_usd <= 10:
                        await self.telegram.send_message("❌ [매수 실패] 계좌 잔고 부족.")
                        await self.history.append_or_update_history(today_str, [], {})
                        last_run_date = today_str
                        continue

                    losing_streak = self.history.get_consecutive_losing_streak()
                    # 과거의 마지막 손실 거래를 매일 "직전 거래일"로 해석해 신규
                    # 주문을 영구 차단하던 로직을 제거한다. 실제 당일 손실 한도는
                    # monitor_active_trades()의 _enforce_intraday_loss_limit()에서
                    # 보유 포지션 가격으로 계속 집행되며, 종목별 손절도 유지된다.
                    # 장기 코어 적립은 과거 단일 거래의 결과만으로 중단하지 않는다.

                    last_run_date = today_str

                    # 대시보드의 금액 상한과 리스크 모드/연패 축소 중 더 보수적인 값을 사용합니다.
                    budget_ratio = min(self._get_budget_ratio(losing_streak, risk_profile), settings["max_budget_pct"] / 100)
                    if losing_streak > 0:
                        logger.info(f"📉 최근 {losing_streak}거래일 연속 손실 감지. 사용 예산 비율을 {budget_ratio*100:.0f}%로 축소합니다.")

                    usable_budget = total_balance_usd * budget_ratio
                    weights = inverse_volatility_weights(stocks_list, candidates_meta)

                    stocks_msg = ""
                    executed_stocks = []
                    skipped_stocks = []
                    for i, (s, weight) in enumerate(zip(stocks_list, weights), 1):
                        ticker = s.get("ticker", "N/A")
                        budget_for_stock = usable_budget * weight

                        entry_price = await self.toss.get_realtime_price(ticker)
                        if not entry_price or entry_price <= 0:
                            logger.warning(f"⚠️ {ticker} 현재가 조회 실패로 매수를 건너뜁니다.")
                            continue

                        price_confirmed, entry_reason = confirm_swing_entry_price(
                            candidates_meta.get(ticker, {}), entry_price
                        )
                        opening = {"confirmed": price_confirmed, "reason": entry_reason}
                        if not price_confirmed:
                            skipped_stocks.append(f"{ticker} ({entry_reason})")
                            logger.info("⏭️ %s 매수 보류: %s", ticker, entry_reason)
                            continue

                        target, stop = self._apply_atr_based_risk_levels(s, entry_price, candidates_meta, risk_profile)

                        buy_success = await self.toss.buy_market_order(ticker, budget_usd=budget_for_stock)

                        if buy_success:
                            executed_stock = dict(s)
                            executed_stock["strategy_metadata"] = self.strategy_evaluator.build_trade_metadata(
                                candidates_meta.get(ticker, {}), opening, strategy_evaluation
                            )
                            self.active_trades.append({
                                "ticker": ticker,
                                "entry_price": entry_price,
                                "target_price": target,
                                "stop_loss_price": stop,
                                "status": "HOLDING",
                                "recommended_date": today_str,
                                "allocated_budget_usd": budget_for_stock,
                                "account_reference_usd": total_balance_usd,
                                "strategy_policy": strategy_evaluation["policy"],
                                "strategy_type": s.get("strategy_type", "swing_trend"),
                            })
                            # [핵심 수정] 종목 하나 살 때마다 즉시 파일에 저장합니다.
                            # 3종목을 다 사고 나서 한 번에 저장하면, 1~2번째 종목만
                            # 산 상태에서 재시작될 경우 그 매수 사실 자체를 잃어버립니다.
                            await self._save_active_trades()
                            executed_stocks.append(executed_stock)
                            # 주문이 실제로 받아들여진 종목만 이력에 남긴다. 오프닝
                            # 확인/주문 실패 종목이 미정산 거래로 쌓이는 것을 방지한다.
                            await self.history.append_or_update_history(today_str, executed_stocks, {})

                            stocks_msg += (
                                f"📌 {i}. {ticker} - 할당금액: ${budget_for_stock:.2f} "
                                f"(비중 {weight*100:.0f}%) | 목표 ${target:.2f} / 손절 ${stop:.2f}\n"
                            )

                    if not executed_stocks:
                        await self.history.append_or_update_history(today_str, [], {})
                    if skipped_stocks:
                        stocks_msg += f"⏭️ 가격 갭 확인 미통과: {', '.join(skipped_stocks)}\n"

                    streak_note = f"\n📉 연속 손실 {losing_streak}일 감지 → 예산 {budget_ratio*100:.0f}% 사용" if losing_streak > 0 else ""
                    final_msg = f"🔥 [저회전 추세 자동매매] \n\n{stocks_msg}{streak_note}\n💡 최대 {MAX_HOLDING_TRADING_DAYS}거래일 보유하며 10초 주기로 익/손절을 감시합니다."
                    await self.telegram.send_message(final_msg)

                if self.active_trades:
                    await self.monitor_active_trades()

                await asyncio.sleep(10)

            except Exception as e:
                logger.exception("스케줄러 실행 중 예외 발생")
                await asyncio.sleep(10)

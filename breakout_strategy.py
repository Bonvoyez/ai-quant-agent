"""
breakout_strategy.py: Snowball Nanocap Runner Execution Module

Coordinates:
1. Daily Top 1 candidate selection using derived_formula.py
2. Execution via pluggable BaseBroker (MockBroker, TossInvestService, etc.)
3. Intraday trailing stop & tight stop-loss via ExitManager
4. Atomic persistence to active_trades.json & breakout_positions.json for recovery
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from derived_formula import calculate_breakout_score, should_enter_breakout_trade
from exit_manager import ExitManager
from broker import MockBroker
from utils import atomic_write
from trade_logger import trade_logger

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")
POSITIONS_FILE = "breakout_positions.json"


@dataclass
class ActivePosition:
    ticker: str
    entry_price: float
    highest_price: float
    entry_time: datetime
    current_price: float = 0.0
    exit_manager: ExitManager = field(default_factory=ExitManager)
    status: str = "OPEN"

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "entry_price": self.entry_price,
            "highest_price": self.highest_price,
            "current_price": self.current_price if self.current_price > 0 else self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "status": self.status,
            "stop_loss_pct": self.exit_manager.stop_loss_pct,
            "trailing_step_pct": self.exit_manager.trailing_step_pct,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ActivePosition":
        mgr = ExitManager(
            stop_loss_pct=float(data.get("stop_loss_pct", -3.0)),
            trailing_step_pct=float(data.get("trailing_step_pct", 2.0)),
        )
        try:
            etime = datetime.fromisoformat(data["entry_time"])
        except Exception:
            etime = datetime.now(NEW_YORK)

        return cls(
            ticker=data["ticker"],
            entry_price=float(data["entry_price"]),
            highest_price=float(data.get("highest_price", data["entry_price"])),
            entry_time=etime,
            current_price=float(data.get("current_price", data.get("entry_price", 0.0))),
            exit_manager=mgr,
            status=data.get("status", "OPEN"),
        )


class BreakoutStrategyRunner:
    def __init__(
        self,
        broker,
        telegram_service=None,
        ai_client=None,
        settings_manager=None,
        score_threshold: float = 15.0,
        stop_loss_pct: float = -3.0,
        trailing_step_pct: float = 2.0,
        max_positions: int = 1,
        storage_file: str = POSITIONS_FILE,
    ):
        self.broker = broker
        # Backward compatibility alias
        self.toss = broker
        self.telegram = telegram_service
        self.ai = ai_client
        self.settings = settings_manager
        self.score_threshold = score_threshold
        self.stop_loss_pct = stop_loss_pct
        self.trailing_step_pct = trailing_step_pct
        self.max_positions = max_positions
        self.storage_file = storage_file
        self.active_positions: Dict[str, ActivePosition] = {}
        self._monitoring_active = False

        # Load persisted positions on startup
        self._load_positions()

    def _load_positions(self):
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        pos = ActivePosition.from_dict(item)
                        self.active_positions[pos.ticker] = pos
                logger.info(f"💾 [Breakout Strategy] Loaded {len(self.active_positions)} active positions from disk.")
            except Exception as e:
                logger.error(f"Failed to load {self.storage_file}: {e}")

    def _persist_positions(self):
        try:
            records = [pos.to_dict() for pos in self.active_positions.values()]
            atomic_write(self.storage_file, records)
        except Exception as e:
            logger.error(f"Failed to persist {self.storage_file}: {e}")

    async def flush_stale_positions(self) -> List[str]:
        """
        Pre-flight Safeguard:
        If any position from a prior day remains (due to previous-day broker timeout, network blip, or halt),
        force-liquidate it immediately BEFORE screening and buying so cash is 100% recovered for today's pick.
        """
        now_ny = datetime.now(NEW_YORK)
        today = now_ny.date()
        flushed = []
        for ticker, pos in list(self.active_positions.items()):
            if pos.entry_time.date() < today:
                logger.warning(
                    f"🚨 [Stale Overnight Position] {ticker} from {pos.entry_time.date()} held into {today}. "
                    f"Force-liquidating immediately to release capital..."
                )
                try:
                    current_price = await self.broker.get_realtime_price(ticker)
                except Exception:
                    current_price = pos.current_price or pos.entry_price

                if not current_price or current_price <= 0:
                    current_price = pos.current_price or pos.entry_price

                dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
                if dry_run and not isinstance(self.broker, MockBroker):
                    logger.info(f"🧪 [DRY RUN] Force-liquidated stale position {ticker} at ${current_price:.2f}")
                    sell_success = True
                else:
                    sell_success = await self.broker.sell_market_order(ticker)

                if sell_success:
                    del self.active_positions[ticker]
                    self._persist_positions()
                    trade_logger.record_trade_exit(
                        ticker=ticker,
                        entry_price=pos.entry_price,
                        exit_price=current_price,
                        highest_price=pos.highest_price,
                        exit_reason="STALE_OVERNIGHT_FORCE_LIQUIDATION",
                        entry_time=pos.entry_time.isoformat(),
                        exit_time=now_ny.isoformat(),
                    )
                    flushed.append(ticker)
                    if self.telegram:
                        curr_ret = ((current_price - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price > 0 else 0.0
                        await self.telegram.send_message(
                            f"🧹 [Pre-Market Capital Flush]\n"
                            f"• Ticker: {ticker}\n"
                            f"• Reason: Previous day leftover liquidated to free 100% cash for today's trade.\n"
                            f"• PnL: {curr_ret:+.2f}%\n"
                            f"• Capital 100% ready for new runner."
                        )

        if flushed:
            # Short sleep to allow broker balance update
            await asyncio.sleep(2.0)
        return flushed

    async def screen_and_enter(self, candidate_features: List[Dict]) -> List[str]:
        # Pre-flight safeguard: Always flush any stale positions from previous days before screening or buying
        await self.flush_stale_positions()

        user_settings = self.settings.load() if self.settings else {}
        ai_mode = str(user_settings.get("ai_mode") or os.getenv("AI_MODE", "deep")).lower()

        valid_candidates = []
        for feat in candidate_features:
            ticker = feat.get("ticker", "UNKNOWN")
            if should_enter_breakout_trade(feat, threshold=self.score_threshold):
                score = calculate_breakout_score(feat)
                valid_candidates.append({"score": score, "ticker": ticker, "feat": feat})

        valid_candidates.sort(key=lambda x: x["score"], reverse=True)
        if not valid_candidates:
            logger.info("ℹ️ [Breakout Strategy] No candidates passed mathematical threshold today.")
            all_items = [{"score": calculate_breakout_score(f), "ticker": f.get("ticker", "UNKNOWN"), "feat": f} for f in candidate_features]
            trade_logger.record_screening_cycle(
                date_str=datetime.now(NEW_YORK).strftime("%Y-%m-%d"),
                total_universe_count=len(candidate_features),
                all_candidates=all_items,
                approved_candidates=[],
                entered_ticker=None,
                ai_mode=ai_mode,
            )
            return []

        logger.info(f"🧠 [Breakout Strategy] Active AI Intelligence Mode: [{ai_mode.upper()}] (Candidates: {len(valid_candidates)})")

        approved_candidates = []
        # Multi-Tier AI Intelligence Pipeline
        if ai_mode in ("standard", "deep") and self.ai and self.ai.is_configured:
            audit_limit = 5 if ai_mode == "deep" else 2
            audit_pool = valid_candidates[:audit_limit]
            logger.info(f"🔍 [Deep Pro AI Audit] Performing live SEC EDGAR & catalyst audit on top {len(audit_pool)} candidate(s)...")

            for item in audit_pool:
                ticker = item["ticker"]
                audit_res = await self.ai.deep_audit_candidate(ticker, item["feat"])
                item["ai_audit"] = audit_res
                item["ai_summary"] = audit_res.get("summary", "")

                if audit_res.get("is_safe", True):
                    logger.info(f"✅ [AI Audit Passed] {ticker} - Recommendation: APPROVED (Fact Score: {audit_res.get('fact_score', 0.5):.2f})")
                    approved_candidates.append(item)
                else:
                    flags = ", ".join(audit_res.get("risk_flags", [])) or "Dilution/Offering alert"
                    logger.warning(f"🚨 [AI Sentinel REJECT] {ticker} rejected! Toxic risk detected: {flags}. Bypassing trap.")

            # If all audited candidates were rejected by AI, fallback or stop safely
            if not approved_candidates and valid_candidates:
                logger.warning("⚠️ All top audited candidates flagged as toxic by AI. Capital strictly protected from traps.")
        elif ai_mode == "lite" and self.ai and self.ai.is_configured:
            top_item = valid_candidates[0]
            audit_res = await self.ai.deep_audit_candidate(top_item["ticker"], top_item["feat"])
            top_item["ai_audit"] = audit_res
            top_item["ai_summary"] = audit_res.get("summary", "")
            if audit_res.get("is_safe", True):
                approved_candidates.append(top_item)
            else:
                logger.warning(f"🚨 [AI Lite REJECT] {top_item['ticker']} flagged unsafe. Skipping.")
        else:
            # AI Off or unconfigured -> Pure quantitative mathematical rankings
            approved_candidates = valid_candidates

        top_picks = approved_candidates[: self.max_positions]
        if not top_picks:
            logger.info("ℹ️ [Breakout Strategy] 0 candidates approved for execution today. Recording screening audit to disk.")
            if self.telegram:
                rejected_names = [item["ticker"] for item in valid_candidates if item.get("ai_audit") and not item["ai_audit"].get("is_safe", True)]
                rej_str = f" ({', '.join(rejected_names)})" if rejected_names else ""
                try:
                    await self.telegram.send_message(
                        f"🛡️ [AI Sentinel Capital Protection]\n"
                        f"All evaluated momentum candidates{rej_str} were rejected by AI due to active toxic risk/dilution.\n"
                        f"• Capital 100% safely preserved in cash (0 trades executed today)."
                    )
                except Exception as e:
                    logger.debug(f"Failed to send rejection notice: {e}")

        # Send Telegram Morning Intelligence Briefing
        if top_picks and self.telegram and self.ai and ai_mode in ("standard", "deep") and self.ai.is_configured:
            try:
                top_item = top_picks[0]
                runners_up = approved_candidates[1:3]
                briefing = await self.ai.generate_morning_briefing(top_item, runners_up)
                header = "👑 [AI QUANT INTELLIGENCE: DEEP PRO MORNING REPORT]\n"
                await self.telegram.send_message(f"{header}{briefing}")
            except Exception as tg_err:
                logger.debug(f"Failed to send AI briefing: {tg_err}")

        entered_tickers = []
        for pick in top_picks:
            score = pick["score"]
            ticker = pick["ticker"]
            if ticker in self.active_positions:
                continue

            logger.info(f"🎯 [Snowball Top Pick] {ticker} - Formula Score: {score:.2f}")

            dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
            cap_env = float(os.getenv("ROTATION_CAPITAL_CAP_USD", "0") or 0)

            balance = await self.broker.get_balance()
            if dry_run and balance < 10.0:
                # Provide standard paper trading simulated capital in dry-run mode
                balance = cap_env if cap_env > 0 else 500.0

            if balance < 10.0:
                logger.warning(f"⚠️ Insufficient balance (${balance:.2f}) to enter {ticker}")
                continue

            effective_balance = min(balance, cap_env) if cap_env > 0 else balance
            available_slots = max(1, self.max_positions - len(self.active_positions))
            budget = effective_balance / available_slots
            current_price = await self.broker.get_realtime_price(ticker)
            if not current_price or current_price <= 0:
                logger.warning(f"⚠️ Could not fetch price for {ticker}")
                continue

            if dry_run and not isinstance(self.broker, MockBroker):
                logger.info(f"🧪 [DRY RUN] Simulated BUY executed: BUY {ticker} (${budget:.2f} @ ${current_price:.2f})")
                buy_success = True
            else:
                buy_success = await self.broker.buy_market_order(ticker, budget)

            if buy_success:
                pos = ActivePosition(
                    ticker=ticker,
                    entry_price=current_price,
                    highest_price=current_price,
                    entry_time=datetime.now(NEW_YORK),
                    exit_manager=ExitManager(
                        stop_loss_pct=self.stop_loss_pct,
                        trailing_step_pct=self.trailing_step_pct,
                    ),
                )
                self.active_positions[ticker] = pos
                self._persist_positions()
                entered_tickers.append(ticker)

                msg = (
                    f"🚀 [Snowball Entry Executed]\n"
                    f"• Ticker: {ticker}\n"
                    f"• Formula Score: {score:.1f}\n"
                    f"• AI Mode: {ai_mode.upper()}\n"
                    f"• Entry Price: ${current_price:.2f}\n"
                    f"• Budget Allocated: ${budget:.2f} USD\n"
                    f"• Hard Stop: ${current_price * (1.0 + self.stop_loss_pct / 100.0):.2f} ({self.stop_loss_pct:.1f}%)\n"
                    f"• Dynamic Trailing: Peak -{self.trailing_step_pct:.1f}%"
                )
                logger.info(msg)
                if self.telegram:
                    await self.telegram.send_message(msg)

        # Record complete screening funnel and near-misses for local ML training & dashboard
        entered_symbol = entered_tickers[0] if entered_tickers else None
        all_items = []
        for feat in candidate_features:
            t = feat.get("ticker", "UNKNOWN")
            sc = calculate_breakout_score(feat)
            found_vc = next((vc for vc in valid_candidates if vc["ticker"] == t), None)
            if found_vc:
                all_items.append(found_vc)
            else:
                all_items.append({"score": sc, "ticker": t, "feat": feat})

        trade_logger.record_screening_cycle(
            date_str=datetime.now(NEW_YORK).strftime("%Y-%m-%d"),
            total_universe_count=len(candidate_features),
            all_candidates=all_items,
            approved_candidates=approved_candidates,
            entered_ticker=entered_symbol,
            ai_mode=ai_mode,
        )

        return entered_tickers

    async def run_intraday_exit_loop(self, poll_interval_seconds: int = 60):
        self._monitoring_active = True
        logger.info("📡 [Snowball Strategy] Intraday trailing exit loop started.")

        try:
            while self._monitoring_active and self.active_positions:
                for ticker, pos in list(self.active_positions.items()):
                    try:
                        current_price = await self.broker.get_realtime_price(ticker)
                    except Exception as pe:
                        logger.debug(f"Price fetch failed for {ticker}: {pe}")
                        current_price = None

                    # If live quote fails (e.g. market closed), fallback to last known price so EOD exit can still trigger
                    if not current_price or current_price <= 0:
                        current_price = pos.current_price if pos.current_price > 0 else pos.entry_price

                    if current_price > 0:
                        pos.current_price = current_price
                        if current_price > pos.highest_price:
                            pos.highest_price = current_price
                        self._persist_positions()

                    action, ratio, reason = pos.exit_manager.evaluate_exit(
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        highest_price_since_entry=pos.highest_price,
                    )

                    # Pure Day-Trading EOD Rule:
                    # 1. Any time on or after 14:50 EST on entry date (Toss Securities enforces that fractional-share orders
                    #    can only be placed up to 1 hour before market close; exiting at 14:50 EST guarantees fill before 15:00 cutoff)
                    # 2. ANY subsequent calendar day (leftover must be cleared immediately)
                    now_ny = datetime.now(NEW_YORK)
                    is_after_market_close = (
                        pos.entry_time.date() == now_ny.date()
                        and (now_ny.hour > 14 or (now_ny.hour == 14 and now_ny.minute >= 50))
                    )
                    is_subsequent_day = (pos.entry_time.date() < now_ny.date())

                    if action != "SELL" and (is_after_market_close or is_subsequent_day):
                        action = "SELL"
                        ratio = 1.0
                        reason = "EOD_MARKET_CLOSE_EXIT (Pure Day-Trading Liquidation)"

                    if action == "SELL":
                        curr_ret = ((current_price - pos.entry_price) / pos.entry_price) * 100.0
                        logger.info(f"🚨 [Exit Triggered] {ticker} - Reason: {reason} | Return: {curr_ret:.2f}%")
                        dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
                        if dry_run and not isinstance(self.broker, MockBroker):
                            logger.info(f"🧪 [DRY RUN] Simulated SELL executed: SELL {ticker} ({curr_ret:+.2f}%)")
                            sell_success = True
                        else:
                            sell_success = await self.broker.sell_market_order(ticker)

                        if sell_success:
                            del self.active_positions[ticker]
                            self._persist_positions()
                            trade_logger.record_trade_exit(
                                ticker=ticker,
                                entry_price=pos.entry_price,
                                exit_price=current_price,
                                highest_price=pos.highest_price,
                                exit_reason=reason,
                                entry_time=pos.entry_time.isoformat(),
                                exit_time=datetime.now(NEW_YORK).isoformat(),
                            )
                            msg = (
                                f"🏁 [Snowball Exit Executed]\n"
                                f"• Ticker: {ticker}\n"
                                f"• Reason: {reason}\n"
                                f"• Exit Price: ${current_price:.2f}\n"
                                f"• PnL Return: {curr_ret:+.2f}%"
                            )
                            logger.info(msg)
                            if self.telegram:
                                await self.telegram.send_message(msg)

                await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("🛑 [Snowball Strategy] Exit loop cancelled.")
        except Exception as loop_err:
            logger.exception(f"Unexpected error in intraday exit loop: {loop_err}")
        finally:
            self._monitoring_active = False

    def stop_monitoring(self):
        self._monitoring_active = False

# market_data.py
import asyncio
import yfinance as yf
import pandas as pd
import logging
from collections import defaultdict
from datetime import datetime, time
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo
from config import SECTOR_ETF_MAPPING

logger = logging.getLogger("StockReporter")

# 예시 후보 유니버스. 실제 운영 시 config.py로 옮겨 관리 권장.
DEFAULT_CANDIDATE_UNIVERSE: List[str] = [
    # 기존 유니버스는 고베타 성장주 위주라, 시장을 주도하는 대형 우량주가 올라도
    # 주문 가능한 후보가 전혀 남지 않는 날이 잦았다. 유동성이 풍부하고 변동성이
    # 상대적으로 낮은 리더를 포함해 장기 추세·단기 모멘텀 모두를 포착한다.
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "BRK-B", "JPM", "V",
    "MA", "LLY", "COST", "WMT", "NFLX", "ORCL", "IBM", "GE", "CAT", "XOM",
    "AMD", "SMCI", "PLTR", "COIN", "MSTR", "SOFI", "AFRM", "RIVN", "LCID",
    "TSLA", "MRVL", "ARM", "DKNG", "U", "RBLX", "SNOW", "NET", "CRWD", "DDOG",
    "MU", "ON", "ASML", "IONQ", "RGTI", "CLSK", "MARA", "RIOT", "APP",
    "SHOP", "ROKU", "UPST", "ENPH", "FSLR", "CELH", "ELF", "CVNA", "DUOL", "HOOD",
    # 소액 계좌의 기본 후보군: 단일 종목 이벤트 위험을 낮추는 광범위/섹터 ETF.
    "SPY", "QQQ", "IWM", "DIA", "XLK", "SOXX", "XLF", "XLV", "XLE", "XLP", "XLI", "XLY",
]

# 다음 거래일 기준 이 일수 이내에 실적발표가 있으면 후보에서 제외
EARNINGS_EXCLUSION_DAYS = 2

# "추격매수" 방지용 과열 판정 기준
# change_1d_pct(당일 등락률)가 이 값을 넘으면, 이미 그날 많이 오른 상태이므로
# 매수 시점(다음날 오전) 기준으로는 고점에 가까울 위험이 크다고 보고 페널티를 줍니다.
# [수정] 8.0 -> 5.0. history.json에 남은 실제 추천 사례(ENPH +10%, AKBA +16% 등 이미
# 장전 급등한 종목을 "매수세가 이어질 것"이라며 추천)를 보면, 기존 임계값(8%)과 페널티
# 배율(1.5)로는 이미 크게 오른 종목을 걸러내기에 부족했습니다. 장전/당일 큰 갭업은
# 되돌림(mean reversion) 위험이 크다는 것이 잘 알려진 패턴이라, 임계값을 낮추고
# 페널티를 강화했습니다.
OVEREXTENSION_THRESHOLD_PCT = 5.0
# 임계값을 초과한 만큼(%p)에 곱해서 모멘텀 스코어에서 차감할 배율
OVEREXTENSION_PENALTY_MULTIPLIER = 2.5

# [신규] 페널티만으로는 걸러지지 않는 극단적인 당일 급등 종목(장전 급등 뉴스/테마성
# 단타 수급이 이미 다 들어온 상태일 가능성이 높음)은 점수 감산이 아니라 후보 자체에서
# 완전히 제외합니다. 이 값 이상 오른 종목은 "내일도 더 오를 모멘텀"이 아니라
# "오늘 이미 대부분의 상승분을 다 써버렸을 가능성이 높은" 종목으로 취급합니다.
HARD_EXCLUDE_1D_PCT = 15.0
NEW_YORK = ZoneInfo("America/New_York")

# GICS 섹터가 달라도 같은 위험선호 장세에서 함께 움직이는 종목군이다. 업종 분산만
# 믿고 AI 반도체와 고베타 앱/소프트웨어를 동시에 담는 것을 막기 위한 실행용 분류다.
RISK_BUCKETS = {
    "crypto_beta": {"COIN", "MSTR", "CLSK", "MARA", "RIOT", "HOOD"},
    "high_beta_tech": {
        "NVDA", "AMD", "SMCI", "PLTR", "MRVL", "ARM", "MU", "ON", "ASML", "AVGO",
        "IONQ", "RGTI", "SNOW", "NET", "CRWD", "DDOG", "APP", "SHOP", "ROKU", "U", "RBLX",
    },
    "cyclical_growth": {"RIVN", "LCID", "TSLA", "DKNG", "CVNA", "UPST"},
    "clean_energy": {"ENPH", "FSLR"},
    "consumer_growth": {"CELH", "ELF", "DUOL"},
}


def _risk_bucket(ticker: str) -> str:
    for bucket, tickers in RISK_BUCKETS.items():
        if ticker in tickers:
            return bucket
    return "other"


def _momentum_score(c: Dict[str, Any]) -> float:
    """
    모멘텀 스코어 = 1일 변동률 + 5일 변동률*0.5 + 상대거래량*2 - 과열 페널티

    change_1d_pct(당일 등락률)가 클수록 무조건 스코어가 높아지면, "오늘 이미
    많이 오른 종목"이 항상 상위로 뽑힙니다. 이런 종목은 매수 시점(다음날 개장)에는
    이미 고점권일 가능성이 높아 되돌림(mean reversion)에 손절이 잘 걸립니다.
    OVEREXTENSION_THRESHOLD_PCT를 넘는 초과분에는 페널티를 줘서, "적당히 상승
    중인" 종목이 "과열된" 종목보다 우선되도록 합니다.
    """
    score = c["change_1d_pct"] + c["change_5d_pct"] * 0.5 + c["relative_volume"] * 2

    if c["change_1d_pct"] > OVEREXTENSION_THRESHOLD_PCT:
        overextension = c["change_1d_pct"] - OVEREXTENSION_THRESHOLD_PCT
        score -= overextension * OVEREXTENSION_PENALTY_MULTIPLIER

    return score


# [120개 핵심 나노캡/숏스퀴즈 유니버스]
# 실제 머신러닝 학습(+30%~+200% 폭등 패턴 학습) 대상 중 현재 거래 중인 대표 액티브 러너 풀
NANOCAP_CORE_UNIVERSE = [
    "KOSS", "UPXI", "NEGG", "QBTS", "RGTI", "RCAT", "BBAI", "SOUN", "CGEN", "ROOT",
    "CIFR", "WOLF", "WKHS", "SPCE", "CAR", "AEHR", "ALT", "BYND", "INDI", "KROS",
    "CLOV", "MARA", "RIOT", "HUT", "CAN", "BTBT", "WULF", "IREN", "APLD", "CORZ",
    "CLSK", "ANY", "BTCS", "EBON", "HIVE", "ASTS", "RKLB", "LUNR", "MNTS", "RDW",
    "BKSY", "PL", "SPIR", "XPEV", "NIO", "LI", "BLBD", "GP", "SNDL", "TLRY",
    "CGC", "CRON", "ACB", "OGI", "VFF", "MAPS", "GRWG", "AMC", "GME", "CLNE",
    "OPEN", "CVNA", "UPST", "AFRM", "HOOD", "COIN", "SOFI", "MQ", "DAVE", "BMBL",
    "GRPN", "YELP", "ANGI", "Z", "FTHM", "COMP", "GOSS", "VKTX", "MDGL", "ARVN",
    "CRSP", "BEAM", "NTLA", "PRTA", "ADTX", "OCGN", "ATOS", "CTXR", "TNXP", "IBIO",
    "VXRT", "AXSM", "KOD", "ANAB", "EOLS", "TARS", "IMVT", "APVO", "GLSI", "POLA",
    "CBAT", "KNDI", "NNDM", "SSYS", "DDD", "XONE", "PRNT", "MASS", "IONQ", "ARQQ",
    "QUBT", "SMCI", "HIMS", "PLTR", "AI", "DNA", "ENVX", "QS", "SLDP", "AMPX",
    "MVST", "CHPT", "EVGO", "BLNK", "BE", "PLUG", "FCEL", "BLDP", "HYSR", "AMTX",
    "GPRE", "GEVO", "TAL", "EDU", "GOTU", "YQ", "DAO", "COE", "LX", "FINV",
    "XYF", "JFU", "TIGR", "FUTU", "LU", "NOAH", "MEGL", "HKD", "TOP", "HUDI",
    "AMTD", "JZ", "GDC", "LICN", "WIMI", "PETZ", "ZJYL", "MLGO", "LPA", "MNDR", "SMFL", "SXTC", "EJH",
]
# Remove duplicates while preserving order
NANOCAP_CORE_UNIVERSE = list(dict.fromkeys(NANOCAP_CORE_UNIVERSE))


class MarketDataCollector:
    @staticmethod
    async def get_realtime_price(ticker: str) -> Optional[float]:
        """Fetch live price for ticker via yfinance fast_info / 1m intraday bar."""
        try:
            loop = asyncio.get_running_loop()
            def _fetch():
                t = yf.Ticker(ticker)
                fi = getattr(t, "fast_info", None)
                if fi:
                    price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                    if price and price > 0:
                        return float(price)
                hist = t.history(period="1d", interval="1m")
                if not hist.empty and "Close" in hist:
                    return float(hist["Close"].iloc[-1])
                return None
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.debug(f"Failed to fetch live price for {ticker}: {e}")
            return None

    @staticmethod
    async def get_market_regime() -> Dict[str, Any]:
        """Return a mechanical, end-of-day market risk gate.

        This deliberately does *not* ask the LLM whether the market is bullish.
        A long-only short-horizon equity strategy has no reason to be deployed
        when the broad market is below both its intermediate trend and its
        short-term trend, or when volatility is unusually high.  All inputs are
        completed daily bars, so the decision cannot look into the current bar.
        """
        loop = asyncio.get_event_loop()

        def _load() -> Dict[str, Any]:
            try:
                spy = yf.Ticker("SPY").history(period="6mo", auto_adjust=True)
                vix = yf.Ticker("^VIX").history(period="1mo", auto_adjust=True)
                if spy.empty or len(spy) < 55 or vix.empty:
                    return {"trade_allowed": False, "regime": "UNKNOWN", "reason": "시장 데이터 부족"}

                # Drop an in-progress US session bar: the signal is strictly
                # based on data known before the order window.
                last_index = spy.index[-1]
                last_date = (last_index.tz_convert(NEW_YORK).date()
                             if last_index.tz is not None else last_index.date())
                if last_date >= datetime.now(NEW_YORK).date():
                    spy = spy.iloc[:-1]
                if len(spy) < 55:
                    return {"trade_allowed": False, "regime": "UNKNOWN", "reason": "완결 시장 데이터 부족"}

                close = spy["Close"]
                last_close = float(close.iloc[-1])
                sma20 = float(close.iloc[-20:].mean())
                sma50 = float(close.iloc[-50:].mean())
                vix_close = float(vix["Close"].iloc[-1])
                # Annualised realised volatility from completed SPY returns.
                realized_vol = float(close.pct_change().iloc[-20:].std() * (252 ** 0.5) * 100)
                trend_ok = last_close > sma20 and sma20 > sma50
                volatility_ok = vix_close < 25.0 and realized_vol < 30.0
                allowed = trend_ok and volatility_ok
                if not trend_ok:
                    reason = "SPY가 20일/50일 상승 추세 조건 미통과"
                elif not volatility_ok:
                    reason = "VIX 또는 실현변동성 과다"
                else:
                    reason = "SPY 추세·변동성 조건 통과"
                return {
                    "trade_allowed": allowed,
                    "regime": "RISK_ON" if allowed else "RISK_OFF",
                    "reason": reason,
                    "spy_close": round(last_close, 2),
                    "spy_sma20": round(sma20, 2),
                    "spy_sma50": round(sma50, 2),
                    "vix": round(vix_close, 2),
                    "realized_vol_20d_pct": round(realized_vol, 2),
                }
            except Exception as exc:
                logger.warning("시장 국면 계산 실패: %s", exc)
                return {"trade_allowed": False, "regime": "UNKNOWN", "reason": "시장 국면 계산 실패"}

        return await loop.run_in_executor(None, _load)

    @staticmethod
    async def get_opening_confirmation(ticker: str) -> Dict[str, Any]:
        """개장 초반의 노이즈를 통과한 뒤에만 매수하도록 5분봉을 확인한다.

        신호 자체는 전일에 완결된 일봉으로 만들고, 실제 주문 직전에는 첫 25분
        고점 돌파 또는 VWAP 지지와 평소 수준의 초반 거래량을 확인한다.
        데이터가 불충분하면 안전하게 매수를 허용하지 않는다.
        """
        loop = asyncio.get_event_loop()

        def _check_sync() -> Dict[str, Any]:
            try:
                df = yf.Ticker(ticker).history(period="5d", interval="5m", prepost=False)
                if df.empty or len(df) < 6:
                    return {"confirmed": False, "reason": "5분봉 데이터 부족"}

                if df.index.tz is not None:
                    local_index = df.index.tz_convert(NEW_YORK)
                else:
                    local_index = df.index.tz_localize(NEW_YORK)
                now_ny = datetime.now(NEW_YORK)
                session = df.copy()
                session["_ny_time"] = local_index.time
                session["_ny_date"] = local_index.date
                regular = session[
                    (session["_ny_time"] >= time(9, 30))
                    & (session["_ny_time"] < time(10, 0))
                ]
                today = regular[regular["_ny_date"] == now_ny.date()]
                if len(today) < 6:
                    return {"confirmed": False, "reason": "개장 후 30분 데이터 부족"}

                prior_volumes = []
                for day, bars in regular[regular["_ny_date"] < now_ny.date()].groupby("_ny_date"):
                    if len(bars) >= 6:
                        prior_volumes.append(float(bars["Volume"].sum()))
                if not prior_volumes:
                    return {"confirmed": False, "reason": "비교할 과거 오프닝 거래량 부족"}

                opening_high = float(today["High"].iloc[:5].max())
                current_price = float(today["Close"].iloc[-1])
                opening_volume = float(today["Volume"].sum())
                average_volume = sum(prior_volumes) / len(prior_volumes)
                breakout = current_price > opening_high
                volume_confirmed = opening_volume >= average_volume * 0.8

                # 돌파만 허용하면, 전일 추세가 좋고 개장 뒤 안정적으로 가격을
                # 지키는 대형주까지 전부 탈락한다. 초반 25분 VWAP 근처를 지키는
                # 종목은 "돌파 실패"가 아니라 건강한 눌림/보합 진입일 수 있으므로
                # 같은 거래량 확인 아래 대체 진입 패턴으로 인정한다.
                first_window = today.iloc[:5]
                first_volume = float(first_window["Volume"].sum())
                if first_volume > 0:
                    opening_vwap = float((first_window["Close"] * first_window["Volume"]).sum() / first_volume)
                else:
                    opening_vwap = float(first_window["Close"].mean())
                holding_open = current_price >= opening_vwap * 0.997
                entry_confirmed = breakout or holding_open

                if not entry_confirmed or not volume_confirmed:
                    reasons = []
                    if not entry_confirmed:
                        reasons.append("초반 VWAP 하회·고점 미돌파")
                    if not volume_confirmed:
                        reasons.append("오프닝 거래량 부족")
                    return {"confirmed": False, "reason": ", ".join(reasons)}

                return {
                    "confirmed": True,
                    "reason": "오프닝 돌파·거래량 확인" if breakout else "오프닝 VWAP 지지·거래량 확인",
                    "price": current_price,
                    "opening_high": opening_high,
                    "opening_vwap": opening_vwap,
                    "volume_ratio": round(opening_volume / average_volume, 2),
                }
            except Exception as e:
                logger.debug("%s 오프닝 확인 실패: %s", ticker, e)
                return {"confirmed": False, "reason": "오프닝 데이터 조회 실패"}

        return await loop.run_in_executor(None, _check_sync)

    @staticmethod
    async def fetch_realtime_metrics() -> Dict[str, Any]:
        """VIX, 국채금리, 주요 ETF 데이터를 비동기 Gather로 수집하여 실제 데이터 제공"""
        metrics = {}
        try:
            loop = asyncio.get_event_loop()

            async def get_close(ticker: str):
                try:
                    df = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).history(period="5d"))
                    if not df.empty:
                        return df['Close'].iloc[-1]
                except Exception:
                    pass
                return "N/A"

            vix_task = get_close("^VIX")
            tnx_task = get_close("^TNX")
            oil_task = get_close("CL=F")
            gold_task = get_close("GC=F")
            dxy_task = get_close("DX-Y.NYB")

            vix, tnx, oil, gold, dxy = await asyncio.gather(vix_task, tnx_task, oil_task, gold_task, dxy_task)

            metrics["VIX"] = vix
            metrics["10Y_Yield"] = tnx
            metrics["WTI_Oil"] = oil
            metrics["Gold"] = gold
            metrics["Dollar_Index"] = dxy

            etfs = {}
            etf_tasks = [get_close(etf) for etf in SECTOR_ETF_MAPPING.values()]
            etf_results = await asyncio.gather(*etf_tasks)

            for name, val in zip(SECTOR_ETF_MAPPING.keys(), etf_results):
                etfs[name] = val
            metrics["Sector_ETFs"] = etfs

        except Exception as e:
            logger.warning(f"실시간 매크로/수급 데이터 수집 실패: {e}")
            metrics["error"] = str(e)
        return metrics

    @staticmethod
    async def fetch_stock_candidates(
        universe: List[str] = None, top_n: int = 15, excluded_tickers: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        후보 유니버스 종목들의 모멘텀/변동성/섹터/실적발표 임박 여부를 계산합니다.
        - ATR(변동성) 계산 -> 손절/목표가를 종목별로 다르게 설정하는 데 사용
        - 섹터(sector) 태깅 -> 3종목이 동일 섹터로 쏠리는 것 방지
        - 임박한 실적발표(EARNINGS_EXCLUSION_DAYS 이내) 종목은 코드 레벨에서 사전 제외
        - 당일 과열(추격매수 위험) 여부 태깅 -> AI 프롬프트에서도 참고 가능
        - [신규] excluded_tickers(대시보드 블랙리스트)는 아예 데이터 수집 대상에서 제외
        """
        universe = universe or DEFAULT_CANDIDATE_UNIVERSE
        if excluded_tickers:
            excluded_set = {t.upper() for t in excluded_tickers}
            universe = [t for t in universe if t.upper() not in excluded_set]

        loop = asyncio.get_event_loop()

        def _get_stock_metrics_sync(ticker: str):
            try:
                tk = yf.Ticker(ticker)
                df = tk.history(period="6mo")
                if df.empty or len(df) < 55:
                    return None

                # 미장이 열린 동안 yfinance 일봉의 마지막 행은 아직 끝나지 않은
                # 당일 봉이다. 개장 직후의 몇 분짜리 가격/거래량을 "1일 모멘텀"으로
                # 잘못 해석하지 않도록 항상 직전의 완결된 거래일까지만 사용한다.
                last_index = df.index[-1]
                if last_index.tz is not None:
                    last_bar_date = last_index.tz_convert(NEW_YORK).date()
                else:
                    last_bar_date = last_index.date()
                if last_bar_date >= datetime.now(NEW_YORK).date():
                    df = df.iloc[:-1]
                if len(df) < 20:
                    return None

                last_close = float(df['Close'].iloc[-1])
                prev_close = float(df['Close'].iloc[-2])
                close_5d_ago = float(df['Close'].iloc[-6])
                close_20d_ago = float(df['Close'].iloc[-21])

                change_1d = (last_close - prev_close) / prev_close * 100
                change_5d = (last_close - close_5d_ago) / close_5d_ago * 100
                change_20d = (last_close - close_20d_ago) / close_20d_ago * 100
                sma20 = float(df['Close'].iloc[-20:].mean())
                sma50 = float(df['Close'].iloc[-50:].mean())

                avg_volume_20d = float(df['Volume'].iloc[-20:].mean())
                today_volume = float(df['Volume'].iloc[-1])
                rel_volume = (today_volume / avg_volume_20d) if avg_volume_20d > 0 else 0

                # --- ATR(14) 기반 변동성 계산 ---
                high = df['High']
                low = df['Low']
                close = df['Close']
                prev_close_series = close.shift(1)
                tr = pd.concat([
                    (high - low),
                    (high - prev_close_series).abs(),
                    (low - prev_close_series).abs(),
                ], axis=1).max(axis=1)
                atr14 = float(tr.iloc[-14:].mean())
                atr_pct = round((atr14 / last_close * 100), 2) if last_close > 0 else 3.0

                # --- 섹터 태깅 ---
                sector = "Unknown"
                try:
                    info = tk.info
                    sector = info.get("sector") or "Unknown"
                except Exception:
                    pass

                # --- 임박한 실적발표 여부 ---
                earnings_soon = False
                try:
                    edates = tk.get_earnings_dates(limit=6)
                    if edates is not None and not edates.empty:
                        idx = edates.index
                        now_ts = pd.Timestamp.now(tz=idx.tz) if idx.tz is not None else pd.Timestamp.now()
                        upcoming = idx[idx >= now_ts]
                        if len(upcoming) > 0:
                            days_until = (upcoming.min() - now_ts).days
                            if 0 <= days_until <= EARNINGS_EXCLUSION_DAYS:
                                earnings_soon = True
                except Exception:
                    pass

                return {
                    "ticker": ticker,
                    "price": round(last_close, 2),
                    "change_1d_pct": round(change_1d, 2),
                    "change_5d_pct": round(change_5d, 2),
                    "change_20d_pct": round(change_20d, 2),
                    "sma20": round(sma20, 2),
                    "sma50": round(sma50, 2),
                    "relative_volume": round(rel_volume, 2),
                    "atr_pct": atr_pct,
                    "sector": sector,
                    "risk_bucket": _risk_bucket(ticker),
                    "earnings_soon": earnings_soon,
                    "overextended": change_1d > OVEREXTENSION_THRESHOLD_PCT,
                }
            except Exception as e:
                logger.debug(f"{ticker} 후보 데이터 수집 실패: {e}")
                return None

        tasks = [loop.run_in_executor(None, _get_stock_metrics_sync, t) for t in universe]
        results = await asyncio.gather(*tasks)
        candidates = [r for r in results if r is not None]

        # 실적발표 임박 종목 코드 레벨 사전 제외 (프롬프트 지시에만 의존하지 않음)
        excluded = [c["ticker"] for c in candidates if c["earnings_soon"]]
        if excluded:
            logger.info(f"📅 실적발표 임박으로 후보에서 제외: {excluded}")
        candidates = [c for c in candidates if not c["earnings_soon"]]

        # [신규] 당일 이미 과도하게 급등한(추격매수 위험이 매우 큰) 종목은 모멘텀 스코어
        # 페널티만으로 부족하다고 보고 후보 자체에서 제외합니다.
        chased = [c["ticker"] for c in candidates if c["change_1d_pct"] > HARD_EXCLUDE_1D_PCT]
        if chased:
            logger.info(f"🚫 당일 급등폭 과도(>{HARD_EXCLUDE_1D_PCT}%)로 추격매수 위험 판단, 후보에서 제외: {chased}")
        candidates = [c for c in candidates if c["change_1d_pct"] <= HARD_EXCLUDE_1D_PCT]

        candidates.sort(key=_momentum_score, reverse=True)

        overextended_count = sum(1 for c in candidates[:top_n] if c["overextended"])
        if overextended_count:
            logger.info(f"⚠️ 상위 후보 중 {overextended_count}개 종목이 당일 과열(추격매수 위험) 상태입니다 (페널티 적용됨).")

        # 섹터 다양화: 상위 후보 목록 자체가 특정 섹터에 쏠리지 않도록
        by_sector = defaultdict(list)
        for c in candidates:
            by_sector[c["sector"]].append(c)

        diversified: List[Dict[str, Any]] = []
        used_tickers = set()

        sector_leaders = [lst[0] for lst in by_sector.values()]
        sector_leaders.sort(key=_momentum_score, reverse=True)
        for c in sector_leaders:
            if len(diversified) >= top_n:
                break
            diversified.append(c)
            used_tickers.add(c["ticker"])

        for c in candidates:
            if len(diversified) >= top_n:
                break
            if c["ticker"] not in used_tickers:
                diversified.append(c)
                used_tickers.add(c["ticker"])

        diversified.sort(key=_momentum_score, reverse=True)
        return diversified[:top_n]

    @classmethod
    async def get_breakout_candidates_premarket(cls, universe: List[str] = None) -> List[Dict[str, Any]]:
        """
        Pre-market & Open Breakout Feature Extractor:
        Extracts real-time Gap, RVOL, ATR, and Bollinger Width across the 160-ticker nanocap runner universe.
        Uses high-throughput batch downloading for sub-3-second scanning of all 160 momentum tickers.
        """
        if universe is None:
            universe = list(NANOCAP_CORE_UNIVERSE)

        loop = asyncio.get_running_loop()

        def _fetch_all_candidates_batch(tickers: List[str]) -> List[Dict[str, Any]]:
            candidates = []
            try:
                # Fast vectorized batch download
                df_all = yf.download(
                    tickers,
                    period="1mo",
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    auto_adjust=False,
                )

                if df_all.empty:
                    return []

                is_multi = isinstance(df_all.columns, pd.MultiIndex)

                for ticker in tickers:
                    try:
                        if is_multi:
                            if ticker not in df_all.columns.levels[0]:
                                continue
                            hist = df_all[ticker].dropna(how="all")
                        else:
                            hist = df_all.dropna(how="all")

                        if hist.empty or len(hist) < 15:
                            continue

                        prev_close = float(hist["Close"].iloc[-2])
                        prev_vol = float(hist["Volume"].iloc[-2])
                        avg_vol = float(hist["Volume"].iloc[-20:].mean()) if len(hist) >= 20 else float(hist["Volume"].mean())

                        rvol_prev = (prev_vol / avg_vol) if avg_vol > 0 else 1.0

                        current_price = float(hist["Close"].iloc[-1])
                        open_price = float(hist["Open"].iloc[-1]) if "Open" in hist.columns else current_price

                        gap_open_pct = ((open_price - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0

                        high_low = hist["High"] - hist["Low"]
                        high_close = (hist["High"] - hist["Close"].shift()).abs()
                        low_close = (hist["Low"] - hist["Close"].shift()).abs()
                        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                        atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean())
                        atr_pct = (atr / prev_close) * 100.0 if prev_close > 0 else 5.0

                        closes = hist["Close"]
                        sma20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else float(closes.mean())
                        std20 = float(closes.rolling(20).std().iloc[-1]) if len(closes) >= 20 else float(closes.std())
                        bb_width = ((std20 * 4.0) / sma20) * 100.0 if sma20 > 0 else 30.0

                        candidates.append({
                            "ticker": ticker,
                            "gap_open_pct": gap_open_pct,
                            "rvol_prev": rvol_prev,
                            "atr_pct": atr_pct,
                            "bb_width": bb_width,
                            "fact_score": 0.50,
                            "is_toxic_material": 0.0,
                            "current_price": current_price,
                            "open_price": open_price,
                        })
                    except Exception as e:
                        logger.debug(f"[Batch Scanner] Error parsing {ticker}: {e}")
                        continue
            except Exception as e:
                logger.warning(f"[Batch Scanner] Batch download failed, falling back: {e}")
                return []

            return candidates

        results = await loop.run_in_executor(None, _fetch_all_candidates_batch, universe)
        return results

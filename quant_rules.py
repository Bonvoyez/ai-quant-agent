"""Pure, deterministic portfolio rules shared by the trading loop and tests."""

from typing import Any, Dict, List


CORE_LIQUID_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "BRK-B", "JPM", "V",
    "MA", "LLY", "COST", "WMT", "NFLX", "ORCL", "IBM", "GE", "CAT", "XOM",
}

# Small accounts using market orders should favour instruments with deep
# liquidity and diversification.  These ETFs make the strategy less dependent
# on an LLM finding a single winning company or on an individual earnings gap.
LIQUID_ETF_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "XLK", "SOXX", "XLF", "XLV", "XLE", "XLP", "XLI", "XLY",
}
LONG_TERM_CORE_TICKERS = ("SPY", "QQQ")


def inverse_volatility_weights(stocks_list: List[Dict[str, Any]], candidates_meta: Dict[str, Dict[str, Any]]) -> List[float]:
    """Size from observed ATR, never from an LLM's self-reported confidence."""
    raw_weights = []
    for stock in stocks_list:
        atr_pct = candidates_meta.get(stock.get("ticker"), {}).get("atr_pct")
        # Missing volatility data should receive the smallest possible
        # allocation; it must not silently become an equal-sized bet.
        atr = float(atr_pct) if isinstance(atr_pct, (int, float)) and atr_pct > 0 else 10.0
        raw_weights.append(1.0 / atr)
    total = sum(raw_weights) or 1.0
    return [weight / total for weight in raw_weights]


def select_swing_trend_candidate(
    candidates_meta: Dict[str, Dict[str, Any]], max_atr_pct: float
) -> Dict[str, Any] | None:
    """Pick one liquid, non-chasing multi-day trend candidate without an LLM.

    ETF rotation is preferred when it has a qualifying signal; otherwise the
    rule may select a large, liquid stock.  A mild five-day pullback is allowed
    only within a 20/50-day uptrend.  The caller still applies market-regime,
    position-sizing, and loss-limit gates before placing an order.
    """
    spy = candidates_meta.get("SPY", {})
    spy_change_20d = spy.get("change_20d_pct")
    if not isinstance(spy_change_20d, (int, float)):
        return None

    eligible_etfs = []
    eligible_stocks = []
    for ticker, meta in candidates_meta.items():
        if ticker not in CORE_LIQUID_TICKERS and ticker not in LIQUID_ETF_TICKERS:
            continue
        one_day = meta.get("change_1d_pct")
        five_day = meta.get("change_5d_pct")
        twenty_day = meta.get("change_20d_pct")
        atr_pct = meta.get("atr_pct")
        sma20 = meta.get("sma20")
        sma50 = meta.get("sma50")
        price = meta.get("price")
        if not all(isinstance(value, (int, float)) for value in (
            one_day, five_day, twenty_day, atr_pct, sma20, sma50, price
        )):
            continue
        if not (price > sma20 > sma50):
            continue
        # Avoid chasing a gap, but allow a controlled pullback inside a trend.
        if not (-3.0 <= five_day <= 6.0 and -2.0 <= one_day <= 3.0 and atr_pct <= max_atr_pct):
            continue
        relative_strength = twenty_day - spy_change_20d
        if ticker != "SPY" and relative_strength < 0:
            continue

        score = relative_strength + twenty_day * 0.35 + five_day * 0.30 - atr_pct * 0.40
        target = eligible_etfs if ticker in LIQUID_ETF_TICKERS else eligible_stocks
        target.append((score, ticker, meta, relative_strength))

    # Prefer a sector or broad-market ETF, preventing a small account from
    # becoming a one-company earnings bet.  Stocks remain a fallback.
    eligible = eligible_etfs or eligible_stocks
    if not eligible:
        return None
    _, ticker, meta, relative_strength = max(eligible, key=lambda item: item[0])
    return {
        "ticker": ticker,
        "confidence": 0.70,
        "reason": (
            "[저회전 추세 진입] "
            f"{'유동성 ETF' if ticker in LIQUID_ETF_TICKERS else '대형 유동주'}의 20/50일 상승 추세와 "
            f"SPY 대비 20일 상대강도를 충족 (20일 {meta['change_20d_pct']:+.2f}%, "
            f"상대강도 {relative_strength:+.2f}%p, 5일 {meta['change_5d_pct']:+.2f}%, "
            f"ATR {meta['atr_pct']:.2f}%)"
        ),
    }


def confirm_swing_entry_price(candidate: Dict[str, Any], entry_price: float) -> tuple[bool, str]:
    """Reject only abnormal opening gaps; do not depend on fragile 5-minute data."""
    prior_close = candidate.get("price")
    atr_pct = candidate.get("atr_pct")
    if not isinstance(prior_close, (int, float)) or prior_close <= 0:
        return False, "전일 종가 데이터 부족"
    if not isinstance(atr_pct, (int, float)) or atr_pct <= 0:
        return False, "ATR 데이터 부족"
    if not isinstance(entry_price, (int, float)) or entry_price <= 0:
        return False, "실시간 가격 조회 실패"

    gap_pct = (entry_price / prior_close - 1) * 100
    max_gap = min(3.0, atr_pct * 0.75)
    max_drop = atr_pct * 1.25
    if gap_pct > max_gap:
        return False, f"개장 갭상승 {gap_pct:+.2f}% > 허용 {max_gap:.2f}%"
    if gap_pct < -max_drop:
        return False, f"개장 급락 {gap_pct:+.2f}% < 허용 -{max_drop:.2f}%"
    return True, f"개장 갭 {gap_pct:+.2f}% 범위 통과"


def select_long_term_core_candidate(candidates_meta: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    """Select a diversified US equity core without requiring a short-term signal."""
    for ticker in LONG_TERM_CORE_TICKERS:
        meta = candidates_meta.get(ticker, {})
        if all(isinstance(meta.get(key), (int, float)) and meta[key] > 0 for key in ("price", "atr_pct")):
            return {
                "ticker": ticker,
                "confidence": 0.70,
                "strategy_type": "long_term_core",
                "reason": "[장기 코어 적립] 개별 종목·뉴스 예측 대신 미국 대형주 ETF를 소액 분할 매수",
            }
    return None

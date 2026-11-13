"""독립 전략을 페이퍼로 동시에 검증하고, 통과 전략만 실거래 후보로 승격한다."""

import asyncio
import json
import os
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils import atomic_write

ROUND_TRIP_COST_PCT = 0.20
# A few weeks of one-trade-per-day observations are not enough to distinguish
# an edge from noise.  Promotion is intentionally slow: the strategy remains
# paper-only until it has enough *net-of-cost* independent observations.
MIN_PROMOTION_TRADES = 60
MIN_MEAN_LOWER_BOUND_PCT = 0.05
MIN_PROFIT_FACTOR = 1.15


def _score_momentum(candidate: Dict[str, Any]) -> float:
    return candidate["change_5d_pct"] + candidate["change_1d_pct"] * 0.4 - candidate["atr_pct"] * 0.35


def _score_pullback(candidate: Dict[str, Any]) -> float:
    return candidate["change_5d_pct"] - abs(candidate["change_1d_pct"]) * 0.3 - candidate["atr_pct"] * 0.3


def _score_low_vol(candidate: Dict[str, Any]) -> float:
    return candidate["change_5d_pct"] - candidate["atr_pct"] * 0.8


class ShadowPortfolio:
    """각 전략이 같은 시점·동일 비용 가정에서 경쟁하도록 거래를 기록한다."""

    def __init__(self, positions_file: str = "paper_trades.json", history_file: str = "paper_history.json",
                 leaderboard_file: str = "strategy_leaderboard.json"):
        # atomic_write는 문자열 경로를 사용하므로 Path를 넘겨도 안전하게 정규화한다.
        self.positions_file = str(positions_file)
        self.history_file = str(history_file)
        self.leaderboard_file = str(leaderboard_file)
        self.lock = asyncio.Lock()

    @staticmethod
    def select_candidates(candidates: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """각 규칙의 고정된 조건으로 하루 최대 한 종목을 선택한다."""
        values = [c for c in candidates.values() if all(isinstance(c.get(k), (int, float)) for k in (
            "change_1d_pct", "change_5d_pct", "atr_pct", "relative_volume"
        ))]

        rules = {
            "momentum_breakout": (
                lambda c: 0 <= c["change_1d_pct"] <= 5 and c["change_5d_pct"] >= 3 and c["atr_pct"] <= 5,
                _score_momentum,
            ),
            "pullback_trend": (
                lambda c: -3 <= c["change_1d_pct"] <= 1.5 and c["change_5d_pct"] >= 3 and c["atr_pct"] <= 4.5,
                _score_pullback,
            ),
            "low_vol_trend": (
                lambda c: abs(c["change_1d_pct"]) <= 3 and c["change_5d_pct"] >= 1 and c["atr_pct"] <= 3.5,
                _score_low_vol,
            ),
        }
        selected = {}
        for name, (eligible, score) in rules.items():
            matches = [c for c in values if eligible(c)]
            if matches:
                selected[name] = max(matches, key=score)
        return selected

    @staticmethod
    def _load_list(path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, path: str, payload: Any) -> None:
        atomic_write(path, payload)

    @staticmethod
    def _max_drawdown_pct(returns: List[float]) -> float:
        equity = peak = 1.0
        drawdown = 0.0
        for value in returns:
            equity *= 1 + value / 100
            peak = max(peak, equity)
            drawdown = min(drawdown, (equity / peak - 1) * 100)
        return round(drawdown, 2)

    @staticmethod
    def _mean_lower_bound_pct(returns: List[float]) -> float:
        """Conservative 95% lower confidence bound for mean trade return.

        The normal critical value is slightly permissive for small samples, but
        this gate only runs at n >= 60; adding it is still far safer than
        promoting a rule merely because its observed average is positive.
        """
        count = len(returns)
        if count < 2:
            return float("-inf")
        mean = sum(returns) / count
        variance = sum((value - mean) ** 2 for value in returns) / (count - 1)
        return mean - 1.96 * math.sqrt(variance / count)

    @staticmethod
    def _profit_factor(returns: List[float]) -> float:
        gains = sum(value for value in returns if value > 0)
        losses = abs(sum(value for value in returns if value < 0))
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    def refresh_leaderboard(self) -> List[Dict[str, Any]]:
        history = self._load_list(self.history_file)
        grouped: Dict[str, List[float]] = {}
        for trade in history:
            result = trade.get("return_pct")
            strategy = trade.get("strategy")
            if strategy and isinstance(result, (int, float)):
                grouped.setdefault(strategy, []).append(float(result))

        leaderboard = []
        for strategy, returns in grouped.items():
            count = len(returns)
            average = sum(returns) / count
            wins = sum(value > 0 for value in returns) / count * 100
            lower_bound = self._mean_lower_bound_pct(returns)
            profit_factor = self._profit_factor(returns)
            drawdown = self._max_drawdown_pct(returns)
            leaderboard.append({
                "strategy": strategy,
                "trade_count": count,
                "mean_return_pct": round(average, 2),
                "win_rate_pct": round(wins, 1),
                "max_drawdown_pct": drawdown,
                "mean_return_95pct_lower_bound": round(lower_bound, 3),
                "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else None,
                "eligible_for_promotion": (
                    count >= MIN_PROMOTION_TRADES
                    and lower_bound > MIN_MEAN_LOWER_BOUND_PCT
                    and wins >= 52.0
                    and profit_factor >= MIN_PROFIT_FACTOR
                    and drawdown >= -8.0
                ),
            })
        leaderboard.sort(key=lambda row: (row["eligible_for_promotion"], row["mean_return_pct"]), reverse=True)
        self._save(self.leaderboard_file, leaderboard)
        return leaderboard

    def get_promoted_strategy(self) -> Optional[str]:
        for row in self.refresh_leaderboard():
            if row["eligible_for_promotion"]:
                return str(row["strategy"])
        return None

    async def settle_open_positions(self, price_service: Any, exit_date: str) -> List[Dict[str, Any]]:
        """전일 페이퍼 포지션을 실제 같은 시각의 호가로 청산해 전략별 결과를 기록한다."""
        async with self.lock:
            positions = self._load_list(self.positions_file)
            history = self._load_list(self.history_file)
            settled = []
            remaining = []
            for position in positions:
                if position.get("status") != "HOLDING":
                    continue
                price = await price_service.get_realtime_price(position["ticker"])
                entry = position.get("entry_price")
                if not isinstance(price, (int, float)) or not isinstance(entry, (int, float)) or entry <= 0:
                    remaining.append(position)
                    continue
                result = round(((price - entry) / entry * 100) - ROUND_TRIP_COST_PCT, 2)
                closed = {
                    **position,
                    "status": "CLOSED",
                    "exit_date": exit_date,
                    "exit_price": price,
                    "return_pct": result,
                }
                history.append(closed)
                settled.append(closed)

            self._save(self.positions_file, remaining)
            # 유효 표본은 장기 보존하되 파일이 끝없이 커지지 않게 최근 2,000건만 유지한다.
            self._save(self.history_file, history[-2000:])
            self.refresh_leaderboard()
            return settled

    async def open_positions(self, candidates: Dict[str, Dict[str, Any]], price_service: Any, entry_date: str) -> List[Dict[str, Any]]:
        """세 전략의 동일 시점 가상 진입을 기록한다. 이미 같은 날짜에 만들었으면 중복하지 않는다."""
        async with self.lock:
            positions = self._load_list(self.positions_file)
            if any(p.get("entry_date") == entry_date for p in positions):
                return []

            opened = []
            for strategy, candidate in self.select_candidates(candidates).items():
                price = await price_service.get_realtime_price(candidate["ticker"])
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                opened.append({
                    "strategy": strategy,
                    "ticker": candidate["ticker"],
                    "entry_date": entry_date,
                    "entry_price": price,
                    "status": "HOLDING",
                    "features": {
                        key: candidate.get(key)
                        for key in ("change_1d_pct", "change_5d_pct", "relative_volume", "atr_pct", "sector", "risk_bucket")
                    },
                })
            self._save(self.positions_file, positions + opened)
            return opened

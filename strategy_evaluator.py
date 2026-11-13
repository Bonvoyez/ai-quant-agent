"""실현 거래 성과를 바탕으로 다음 사이클의 위험을 검증·조절한다.

이 모듈은 LLM의 자기평가(confidence)를 신뢰하지 않는다. 실제로 청산된 거래만
집계하며, 표본이 부족하면 설정을 바꾸지 않고, 성과가 나쁘면 사용자 설정보다
더 보수적으로만 제한한다. 따라서 검증되지 않은 전략이 자동으로 위험을 키우는
일은 없다.
"""

import json
from datetime import datetime
from typing import Any, Dict, List

from utils import atomic_write


MIN_SETTLED_TRADES = 30
EVALUATION_WINDOW = 20


class StrategyEvaluator:
    def __init__(self, state_file: str = "strategy_state.json"):
        self.state_file = state_file

    @staticmethod
    def _settled_returns(records: List[Dict[str, Any]]) -> List[float]:
        return [
            float(stock["actual_return"])
            for record in records
            for stock in record.get("stocks", [])
            if isinstance(stock.get("actual_return"), (int, float))
        ]

    @staticmethod
    def _max_drawdown_pct(returns: List[float]) -> float:
        equity = peak = 1.0
        max_drawdown = 0.0
        for value in returns:
            equity *= 1 + value / 100
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, (equity / peak - 1) * 100)
        return round(max_drawdown, 2)

    def evaluate(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        all_returns = self._settled_returns(records)
        recent = all_returns[-EVALUATION_WINDOW:]
        sample_size = len(all_returns)
        recent_size = len(recent)
        mean_return = sum(recent) / recent_size if recent_size else 0.0
        win_rate = (sum(value > 0 for value in recent) / recent_size * 100) if recent_size else 0.0
        max_drawdown = self._max_drawdown_pct(recent)

        # 승률 또는 평균수익이 나쁘면 위험만 줄인다. 반대로 성과가 좋아도 사용자
        # 설정을 넘겨 위험을 키우지 않는다. 승격은 별도 검증 정책이 생길 때까지 금지한다.
        if sample_size < MIN_SETTLED_TRADES:
            policy = "LEARNING"
            budget_multiplier = 1.0
            reason = f"완결 거래 {sample_size}/{MIN_SETTLED_TRADES}건: 자동 조정 보류"
        elif mean_return <= 0 or win_rate < 45:
            policy = "DEFENSIVE"
            budget_multiplier = 0.50
            reason = "최근 실현성과가 음수 또는 승률 45% 미만: 위험예산 절반으로 축소"
        elif mean_return < 0.20 or win_rate < 50 or max_drawdown <= -5.0:
            policy = "CAUTIOUS"
            budget_multiplier = 0.75
            reason = "최근 성과가 검증 기준 미달: 위험예산 25% 축소"
        else:
            policy = "VALIDATING"
            budget_multiplier = 1.0
            reason = "최근 성과가 최소 기준 충족: 사용자 설정 한도 내에서 계속 검증"

        evaluation = {
            "policy": policy,
            "budget_multiplier": budget_multiplier,
            "settled_trade_count": sample_size,
            "window_trade_count": recent_size,
            "window_mean_return_pct": round(mean_return, 2),
            "window_win_rate_pct": round(win_rate, 1),
            "window_max_drawdown_pct": max_drawdown,
            "reason": reason,
            "evaluated_at": datetime.now().isoformat(),
        }
        atomic_write(self.state_file, evaluation)
        return evaluation

    @staticmethod
    def apply_safety_governor(user_settings: Dict[str, Any], evaluation: Dict[str, Any]) -> Dict[str, Any]:
        """사용자 설정은 상한이다. 성과 부진 때에만 더 보수적인 값으로 제한한다."""
        effective = dict(user_settings)
        policy = evaluation["policy"]
        multiplier = float(evaluation["budget_multiplier"])
        effective["max_budget_pct"] = round(user_settings["max_budget_pct"] * multiplier, 2)

        if policy == "DEFENSIVE":
            effective["max_atr_pct"] = min(float(user_settings["max_atr_pct"]), 3.5)
            effective["max_daily_loss_pct"] = min(float(user_settings["max_daily_loss_pct"]), 1.0)
        elif policy == "CAUTIOUS":
            effective["max_atr_pct"] = min(float(user_settings["max_atr_pct"]), 4.0)
            effective["max_daily_loss_pct"] = min(float(user_settings["max_daily_loss_pct"]), 2.0)

        return effective

    @staticmethod
    def build_trade_metadata(candidate: Dict[str, Any], opening: Dict[str, Any], evaluation: Dict[str, Any]) -> Dict[str, Any]:
        """다음 평가에서 원인을 추적할 수 있도록 진입 시점의 객관 데이터를 보존한다."""
        return {
            "policy": evaluation["policy"],
            "candidate": {
                "change_1d_pct": candidate.get("change_1d_pct"),
                "change_5d_pct": candidate.get("change_5d_pct"),
                "relative_volume": candidate.get("relative_volume"),
                "atr_pct": candidate.get("atr_pct"),
                "sector": candidate.get("sector"),
                "risk_bucket": candidate.get("risk_bucket"),
            },
            "opening_confirmation": {
                "opening_high": opening.get("opening_high"),
                "volume_ratio": opening.get("volume_ratio"),
            },
        }

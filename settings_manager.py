# settings_manager.py
import asyncio
import json
import logging
import os
from typing import Any, Dict
from utils import atomic_write

logger = logging.getLogger("StockReporter")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "paused": False,
    "max_positions": 3,
    # ``max_positions`` is a ceiling, not a promise to buy this many names.
    # In a weak market holding cash is a valid outcome.
    "max_budget_pct": 35,
    "min_confidence": 0.65,
    "max_atr_pct": 5.0,
    "max_daily_loss_pct": 3.0,
    "excluded_tickers": [],
    "risk_mode": "balanced",  # conservative | balanced | aggressive
}

VALID_RISK_MODES = {"conservative", "balanced", "aggressive"}


class SettingsManager:
    """
    대시보드(dashboard.html)에서 사용자가 바꾼 값을 settings.json에 저장하고,
    scheduler.py가 매 사이클 시작 시 이 값을 읽어 실제 매매 행동(예산 비율,
    ATR 손절/목표가 배율, 종목 수, 블랙리스트)과 AI 프롬프트 내용을 조정합니다.
    """

    def __init__(self, settings_file: str = "settings.json"):
        self.settings_file = settings_file
        self.lock = asyncio.Lock()

    def load(self) -> Dict[str, Any]:
        """설정을 동기적으로 읽습니다 (scheduler.py의 매 사이클 시작 시점에 호출)."""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = dict(DEFAULT_SETTINGS)
                merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
                return self._validate(merged)
            except Exception as e:
                logger.error(f"settings.json 읽기 실패, 기본값 사용: {e}")
        return dict(DEFAULT_SETTINGS)

    def _validate(self, s: Dict[str, Any]) -> Dict[str, Any]:
        try:
            s["max_positions"] = max(1, min(5, int(s.get("max_positions", 3))))
        except Exception:
            s["max_positions"] = 3

        for key, default, low, high in (
            ("max_budget_pct", 35, 5, 75),
            ("min_confidence", 0.65, 0.50, 0.95),
            ("max_atr_pct", 5.0, 1.0, 12.0),
            ("max_daily_loss_pct", 3.0, 0.5, 10.0),
        ):
            try:
                s[key] = max(low, min(high, float(s.get(key, default))))
            except (TypeError, ValueError):
                s[key] = default

        if s.get("risk_mode") not in VALID_RISK_MODES:
            s["risk_mode"] = "balanced"

        if not isinstance(s.get("excluded_tickers"), list):
            s["excluded_tickers"] = []
        s["excluded_tickers"] = sorted({str(t).strip().upper() for t in s["excluded_tickers"] if str(t).strip()})

        s["paused"] = bool(s.get("paused", False))
        return s

    async def save(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        validated = self._validate({**DEFAULT_SETTINGS, **settings})
        async with self.lock:
            await asyncio.to_thread(atomic_write, self.settings_file, validated)
        logger.info(f"⚙️ 대시보드 설정 저장: {validated}")
        return validated

# performance_tracker.py
import asyncio
import os
import json
from typing import Any, Dict
from utils import atomic_write
import logging

logger = logging.getLogger("StockReporter")

class PerformanceTracker:
    def __init__(self, cache_file: str = "performance_cache.json"):
        self.cache_file = cache_file
        self.lock = asyncio.Lock()

    def load_cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    async def _async_save(self, data: Dict[str, Any]) -> None:
        def save_atomic():
            atomic_write(self.cache_file, data)
        async with self.lock:
            # RuntimeWarning(코루틴 await 누락) 경고를 해결하기 위해 일반 함수로 매핑
            await asyncio.to_thread(save_atomic)
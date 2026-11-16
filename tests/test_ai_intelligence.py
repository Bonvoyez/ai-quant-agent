# tests/test_ai_intelligence.py
import pytest
from ai_client import AIClient
from breakout_strategy import BreakoutStrategyRunner
from broker import MockBroker
from settings_manager import SettingsManager


class MockAIClient:
    def __init__(self, is_safe=True):
        self.is_configured = True
        self.is_safe = is_safe
        self.audited = []

    async def deep_audit_candidate(self, ticker, candidate_info):
        self.audited.append(ticker)
        return {
            "is_safe": self.is_safe,
            "recommendation": "APPROVED" if self.is_safe else "REJECTED",
            "fact_score": 0.85 if self.is_safe else 0.1,
            "summary": "Mock audit summary",
            "risk_flags": [] if self.is_safe else ["S-3 Offering Detected"],
        }

    async def generate_morning_briefing(self, top_candidate, runners_up):
        return f"• 🚀 Catalyst: {top_candidate.get('ticker')}\n• 🛡️ SEC: Clean\n• 🎯 Plan: Long"


@pytest.mark.asyncio
async def test_ai_deep_mode_approves_safe_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("ROTATION_DRY_RUN", "1")
    settings_file = str(tmp_path / "settings.json")
    positions_file = str(tmp_path / "positions.json")

    mgr = SettingsManager(settings_file=settings_file)
    await mgr.save({"ai_mode": "deep", "strategy_mode": "breakout"})

    ai = MockAIClient(is_safe=True)
    broker = MockBroker(500.0)
    runner = BreakoutStrategyRunner(
        broker=broker,
        ai_client=ai,
        settings_manager=mgr,
        storage_file=positions_file,
    )

    candidates = [
        {
            "ticker": "UPXI",
            "gap_open_pct": 2.0,
            "rvol_prev": 2.5,
            "atr_pct": 12.0,
            "bb_width": 45.0,
            "fact_score": 0.8,
            "is_toxic_material": 0.0,
            "current_price": 5.0,
        }
    ]

    entered = await runner.screen_and_enter(candidates)
    assert "UPXI" in entered
    assert "UPXI" in ai.audited


@pytest.mark.asyncio
async def test_ai_deep_mode_rejects_toxic_dilution(tmp_path, monkeypatch):
    monkeypatch.setenv("ROTATION_DRY_RUN", "1")
    settings_file = str(tmp_path / "settings.json")
    positions_file = str(tmp_path / "positions.json")

    mgr = SettingsManager(settings_file=settings_file)
    await mgr.save({"ai_mode": "deep", "strategy_mode": "breakout"})

    # AI flags candidate as toxic
    ai = MockAIClient(is_safe=False)
    broker = MockBroker(500.0)
    runner = BreakoutStrategyRunner(
        broker=broker,
        ai_client=ai,
        settings_manager=mgr,
        storage_file=positions_file,
    )

    candidates = [
        {
            "ticker": "TRAP",
            "gap_open_pct": 2.0,
            "rvol_prev": 2.5,
            "atr_pct": 12.0,
            "bb_width": 45.0,
            "fact_score": 0.8,
            "is_toxic_material": 0.0,
            "current_price": 5.0,
        }
    ]

    entered = await runner.screen_and_enter(candidates)
    # Toxic candidate should be rejected, protecting capital
    assert len(entered) == 0
    assert "TRAP" in ai.audited

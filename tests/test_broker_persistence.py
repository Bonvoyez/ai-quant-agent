import pytest
import os
from broker import MockBroker
from breakout_strategy import BreakoutStrategyRunner, ActivePosition


@pytest.mark.asyncio
async def test_mock_broker_paper_trading(tmp_path):
    storage_file = str(tmp_path / "test_positions.json")
    broker = MockBroker(initial_cash_usd=500.0)
    broker.set_mock_price("UPXI", 5.0)

    runner = BreakoutStrategyRunner(broker, score_threshold=15.0, storage_file=storage_file)

    candidate = {
        "ticker": "UPXI",
        "fact_score": 0.8,
        "atr_pct": 12.0,
        "rvol_prev": 2.5,
        "gap_open_pct": 2.0,
        "bb_width": 45.0,
        "is_toxic_material": 0.0,
    }

    entered = await runner.screen_and_enter([candidate])
    assert "UPXI" in entered
    assert broker.positions["UPXI"] > 0
    assert os.path.exists(storage_file)

    # Test position recovery after process restart
    runner2 = BreakoutStrategyRunner(broker, score_threshold=15.0, storage_file=storage_file)
    assert "UPXI" in runner2.active_positions
    assert runner2.active_positions["UPXI"].entry_price == 5.0

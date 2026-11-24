import pytest
from breakout_strategy import BreakoutStrategyRunner
from broker import BaseBroker


class DummyBroker(BaseBroker):
    def __init__(self):
        self.balance = 500.0
        self.prices = {"UPXI": 5.0, "AAPL": 150.0}
        self.orders = []

    async def get_balance(self):
        return self.balance

    async def get_realtime_price(self, ticker):
        return self.prices.get(ticker, 5.0)

    async def buy_market_order(self, ticker, budget_usd=0.0):
        self.orders.append(("BUY", ticker, budget_usd))
        return True

    async def sell_market_order(self, ticker):
        self.orders.append(("SELL", ticker))
        return True

    async def get_sellable_quantity(self, ticker: str) -> float:
        return 100.0


class DummyTelegramService:
    def __init__(self):
        self.messages = []

    async def send_message(self, text):
        self.messages.append(text)


@pytest.mark.asyncio
async def test_snowball_runner_entry_and_trailing(monkeypatch, tmp_path):
    monkeypatch.setenv("ROTATION_DRY_RUN", "0")
    storage_file = str(tmp_path / "test_positions.json")
    broker = DummyBroker()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(broker, tg, score_threshold=15.0, max_positions=1, storage_file=storage_file)

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
    assert "UPXI" in runner.active_positions
    assert len(broker.orders) == 1
    assert broker.orders[0][0] == "BUY"

    pos = runner.active_positions["UPXI"]
    assert pos.entry_price == 5.0

    # 1. Price drops to -3.1% -> STOP LOSS
    broker.prices["UPXI"] = 4.84
    action, ratio, reason = pos.exit_manager.evaluate_exit(
        entry_price=5.0,
        current_price=4.84,
        highest_price_since_entry=5.0,
    )
    assert action == "SELL"
    assert "STOP_LOSS" in reason

    # 2. Price surges to +30% ($6.50)
    action, ratio, reason = pos.exit_manager.evaluate_exit(
        entry_price=5.0,
        current_price=6.50,
        highest_price_since_entry=6.50,
    )
    assert action == "HOLD"

    # 3. Pulls back -2.1% from peak ($6.36) -> Trailing Stop triggered
    action, ratio, reason = pos.exit_manager.evaluate_exit(
        entry_price=5.0,
        current_price=6.36,
        highest_price_since_entry=6.50,
    )
    assert action == "SELL"
    assert "TRAILING_STOP" in reason


@pytest.mark.asyncio
async def test_snowball_eod_market_close_exit(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, date
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("ROTATION_DRY_RUN", "0")
    storage_file = str(tmp_path / "test_eod_positions.json")
    broker = DummyBroker()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(broker, tg, score_threshold=15.0, max_positions=1, storage_file=storage_file)

    candidate = {
        "ticker": "UPXI",
        "fact_score": 0.8,
        "atr_pct": 12.0,
        "rvol_prev": 2.5,
        "gap_open_pct": 2.0,
        "bb_width": 45.0,
        "is_toxic_material": 0.0,
    }
    await runner.screen_and_enter([candidate])
    assert "UPXI" in runner.active_positions

    # Simulate position carried over from previous day
    old_time = datetime(2026, 11, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    runner.active_positions["UPXI"].entry_time = old_time

    # Run one pass of exit loop
    exit_task = asyncio.create_task(runner.run_intraday_exit_loop(poll_interval_seconds=1))
    await asyncio.sleep(0.1)
    runner.stop_monitoring()
    await exit_task

    # Should have liquidated due to leftover / pure day-trade EOD rule
    assert "UPXI" not in runner.active_positions
    assert any(o[0] == "SELL" and o[1] == "UPXI" for o in broker.orders)


@pytest.mark.asyncio
async def test_flush_stale_positions_before_new_screening(monkeypatch, tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from breakout_strategy import ActivePosition
    from exit_manager import ExitManager

    monkeypatch.setenv("ROTATION_DRY_RUN", "0")
    storage_file = str(tmp_path / "test_stale_flush.json")
    broker = DummyBroker()
    broker.prices["OLD"] = 10.0
    broker.prices["NEW"] = 5.0
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(broker, tg, score_threshold=15.0, max_positions=1, storage_file=storage_file)

    # Manually inject a stale position from yesterday
    old_time = datetime(2026, 11, 10, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    runner.active_positions["OLD"] = ActivePosition(
        ticker="OLD",
        entry_price=10.0,
        highest_price=10.0,
        entry_time=old_time,
        exit_manager=ExitManager(),
    )
    runner._persist_positions()

    # Now screen and enter today's new candidate
    new_candidate = {
        "ticker": "NEW",
        "fact_score": 0.8,
        "atr_pct": 12.0,
        "rvol_prev": 2.5,
        "gap_open_pct": 2.0,
        "bb_width": 45.0,
        "is_toxic_material": 0.0,
    }

    entered = await runner.screen_and_enter([new_candidate])

    # 1. Stale position OLD must be sold
    assert "OLD" not in runner.active_positions
    assert any(o[0] == "SELL" and o[1] == "OLD" for o in broker.orders)

    # 2. Today's candidate NEW must be successfully entered
    assert "NEW" in entered
    assert "NEW" in runner.active_positions
    assert any(o[0] == "BUY" and o[1] == "NEW" for o in broker.orders)


@pytest.mark.asyncio
async def test_screening_history_recorded_when_ai_rejects_all(monkeypatch, tmp_path):
    import json
    import trade_logger

    class DummyToxicAI:
        is_configured = True
        async def deep_audit_candidate(self, ticker, feat):
            return {
                "is_safe": False,
                "recommendation": "REJECTED",
                "risk_flags": ["Active 48h offering trap"],
                "summary": "Toxic dilution detected."
            }

    monkeypatch.setenv("ROTATION_DRY_RUN", "1")
    monkeypatch.setenv("AI_MODE", "deep")
    storage_file = str(tmp_path / "test_positions_ai_rej.json")
    broker = DummyBroker()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(
        broker,
        tg,
        ai_client=DummyToxicAI(),
        score_threshold=15.0,
        max_positions=1,
        storage_file=storage_file
    )

    candidate = {
        "ticker": "TRAP",
        "fact_score": 0.8,
        "atr_pct": 12.0,
        "rvol_prev": 2.5,
        "gap_open_pct": 2.0,
        "bb_width": 45.0,
        "is_toxic_material": 0.0,
    }

    entered = await runner.screen_and_enter([candidate])

    # 1. Must return empty list because AI rejected TRAP
    assert entered == []
    assert len(runner.active_positions) == 0

    # 2. Telegram warning must be sent
    assert any("AI Sentinel Capital Protection" in m for m in tg.messages)

    # 3. screening_history.json MUST exist and have recorded the cycle
    screening_file = trade_logger.SCREENING_FILE
    assert screening_file.exists()
    with open(screening_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) >= 1
        last_rec = data[-1]
        assert last_rec["entered_ticker"] is None
        assert last_rec["counts"]["ai_rejected_traps"] == 1
        assert last_rec["counts"]["ai_approved"] == 0


@pytest.mark.asyncio
async def test_breakout_eod_exit_at_14_50_est(monkeypatch, tmp_path):
    """Verify that open positions are liquidated at 14:50 EST to respect Toss 1-hour pre-close rule."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from breakout_strategy import ActivePosition
    from exit_manager import ExitManager

    NEW_YORK = ZoneInfo("America/New_York")
    storage_file = str(tmp_path / "test_positions_eod.json")
    broker = DummyBroker()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(broker, tg, storage_file=storage_file)

    # 1. Position entered today at 10:00 EST
    today_ny = datetime.now(NEW_YORK)
    pos = ActivePosition(
        ticker="HKD",
        entry_price=1.00,
        highest_price=1.02,
        entry_time=today_ny.replace(hour=10, minute=0, second=0),
        exit_manager=ExitManager(stop_loss_pct=-3.0),
    )
    runner.active_positions["HKD"] = pos

    # Mock current NY time to 14:51 EST
    mock_now = today_ny.replace(hour=14, minute=51, second=0)

    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return mock_now

    monkeypatch.setattr("breakout_strategy.datetime", MockDatetime)
    monkeypatch.setenv("ROTATION_DRY_RUN", "0")

    await runner.run_intraday_exit_loop(poll_interval_seconds=1)

    # Position must have been sold via EOD exit
    assert "HKD" not in runner.active_positions
    assert len(broker.orders) == 1
    assert broker.orders[0][0] == "SELL"
    assert broker.orders[0][1] == "HKD"


import pytest
from breakout_strategy import BreakoutStrategyRunner


class DummyTossService:
    def __init__(self):
        self.balance = 500.0
        self.prices = {"UPXI": 5.0, "AAPL": 150.0}
        self.orders = []

    async def get_balance(self):
        return self.balance

    async def get_realtime_price(self, ticker):
        return self.prices.get(ticker, 5.0)

    async def buy_market_order(self, ticker, budget):
        self.orders.append(("BUY", ticker, budget))
        return True

    async def sell_market_order(self, ticker):
        self.orders.append(("SELL", ticker))
        return True


class DummyTelegramService:
    def __init__(self):
        self.messages = []

    async def send_message(self, text):
        self.messages.append(text)


@pytest.mark.asyncio
async def test_snowball_runner_entry_and_trailing(monkeypatch):
    monkeypatch.setenv("ROTATION_DRY_RUN", "0")
    toss = DummyTossService()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(toss, tg, score_threshold=15.0, max_positions=1)

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
    assert len(toss.orders) == 1
    assert toss.orders[0][0] == "BUY"

    pos = runner.active_positions["UPXI"]
    assert pos.entry_price == 5.0

    # 1. Price drops to -3.1% -> STOP LOSS
    toss.prices["UPXI"] = 4.84
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

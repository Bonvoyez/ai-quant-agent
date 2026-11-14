import pytest
from breakout_strategy import BreakoutStrategyRunner
from exit_manager import ExitManager


class DummyTossService:
    def __init__(self):
        self.balance = 500.0
        self.prices = {"AAPL": 150.0, "TSLA": 200.0}
        self.orders = []

    async def get_balance(self):
        return self.balance

    async def get_realtime_price(self, ticker):
        return self.prices.get(ticker, 100.0)

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
async def test_breakout_strategy_enter_and_trailing_exit():
    toss = DummyTossService()
    tg = DummyTelegramService()
    runner = BreakoutStrategyRunner(toss, tg, score_threshold=450.0)

    # High score test candidate
    high_score_candidate = {
        'ticker': 'AAPL',
        'rvol_prev': 0.99,
        'gap_open_pct': -1.55,
        'condensation_prev': 0.169,
        'bb_width': 48.07,
        'atr_pct': 4.75,
        'm5_dominance': 93.83,
        'date_numerology': 9.0,
        'ticker_gematria': 9.0,
        'resonance_score': 1.0,
        'moon_phase': 0.95,
        'is_full_moon': 0.0,
        'is_new_moon': 1.0,
        'moon_fullness': 0.091,
        'is_mercury_retrograde': 0.0,
        'zodiac_sign': 10.0,
        'celestial_harmony': 0.727,
        'fact_score': 0.0,
        'rvol_x_condensation': 0.822,
        'fact_x_resonance': 0.0,
        'fact_x_moon': 0.0,
        'celestial_x_rvol': 0.72,
        'm5_dom_per_gap': 36.65,
        'bb_squeeze_rvol': 0.02,
        'is_toxic_material': 0.0,
    }

    entered = await runner.screen_and_enter([high_score_candidate])
    assert "AAPL" in entered
    assert "AAPL" in runner.active_positions
    assert len(toss.orders) == 1
    assert toss.orders[0][0] == "BUY"

    pos = runner.active_positions["AAPL"]
    assert pos.entry_price == 150.0

    # 2. Simulate price surging to +10% (165.0)
    toss.prices["AAPL"] = 165.0
    action, ratio, reason = pos.exit_manager.evaluate_exit(
        entry_price=150.0,
        current_price=165.0,
        highest_price_since_entry=165.0,
    )
    assert action == "HOLD"

    # 3. Simulate price pulling back -2.0% from peak (165.0 * 0.98 = 161.7)
    # Trailing threshold is -1.5%, so this should trigger SELL
    action, ratio, reason = pos.exit_manager.evaluate_exit(
        entry_price=150.0,
        current_price=161.7,
        highest_price_since_entry=165.0,
    )
    assert action == "SELL"
    assert "TRAILING_STOP" in reason

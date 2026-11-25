import numpy as np
import pandas as pd
import pytest

import rotation_strategy as rs


class DummyToss:
    def __init__(self, balance=343.0):
        self.balance = balance
        self.positions = {}
        self.prices = {"TQQQ": 50.0, "SOXL": 30.0, "NVDA": 140.0, "PLTR": 60.0, "TSLA": 320.0}
        self.orders = []

    async def get_balance(self):
        return self.balance

    async def get_sellable_quantity(self, ticker):
        return self.positions.get(ticker, 0.0)

    async def get_realtime_price(self, ticker):
        return self.prices.get(ticker, 100.0)

    async def buy_market_order(self, ticker, budget_usd):
        px = self.prices[ticker]
        qty = budget_usd / px
        self.positions[ticker] = qty
        self.balance -= budget_usd
        self.orders.append(("BUY", ticker, budget_usd))
        return True

    async def sell_market_order(self, ticker):
        qty = self.positions.pop(ticker, 0.0)
        self.balance += qty * self.prices[ticker]
        self.orders.append(("SELL", ticker))
        return True


class DummyTG:
    def __init__(self):
        self.messages = []

    async def send_message(self, text):
        self.messages.append(text)


def _make_prices(n=70, slopes=None):
    slopes = slopes or {"TQQQ": 0.4, "SOXL": 0.8, "NVDA": 0.2, "PLTR": 0.1, "TSLA": -0.3}
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    data = {}
    for tk, sl in slopes.items():
        base = 100.0
        series = [base * ((1 + sl / 100.0) ** i) for i in range(n)]
        data[tk] = series
    return pd.DataFrame(data, index=dates)


def test_compute_leader_signals_and_ranking():
    prices = _make_prices(70, {"TQQQ": 0.3, "SOXL": 0.6, "NVDA": 0.2, "PLTR": 0.1, "TSLA": -0.4})
    sig = rs.compute_leader_signals(prices)
    assert sig["SOXL"]["eligible"] is True
    assert sig["TSLA"]["eligible"] is False
    top, _ = rs.select_top_leader(sig)
    assert top == "SOXL"

    # When all are in downtrend -> None (100% Cash)
    bear_prices = _make_prices(70, {tk: -0.5 for tk in rs.SIGNAL_UNIVERSE})
    bear_sig = rs.compute_leader_signals(bear_prices)
    top_bear, _ = rs.select_top_leader(bear_sig)
    assert top_bear is None


@pytest.mark.asyncio
async def test_rotation_buy_and_crash_guard_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("ROTATION_DRY_RUN", "0")
    state_file = str(tmp_path / "rot_state.json")
    toss = DummyToss(balance=343.0)
    tg = DummyTG()

    bull_df = _make_prices(70, {"TQQQ": 0.3, "SOXL": 0.6, "NVDA": 0.2, "PLTR": 0.1, "TSLA": -0.4})

    def fake_download(*args, **kwargs):
        return bull_df

    monkeypatch.setattr(rs.yf, "download", fake_download)
    await rs.run_daily_cycle(toss, tg, state_file=state_file)

    assert len(toss.orders) == 1
    assert toss.orders[0][0] == "BUY"
    assert toss.orders[0][1] == "SOXL"
    st = rs._load_state(state_file)
    assert st["active_ticker"] == "SOXL"

    # Now SOXL crashes below SMA50 -> Crash Guard sells 100%
    crash_df = bull_df.copy()
    crash_df.iloc[-1, crash_df.columns.get_loc("SOXL")] = 50.0  # plunge below SMA50

    def fake_download_crash(*args, **kwargs):
        return crash_df

    monkeypatch.setattr(rs.yf, "download", fake_download_crash)
    await rs.run_daily_cycle(toss, tg, state_file=state_file)

    assert ("SELL", "SOXL") in toss.orders
    # Rotates to next strongest leader (TQQQ)
    assert any(o[0] == "BUY" and o[1] == "TQQQ" for o in toss.orders)

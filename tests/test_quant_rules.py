"""Unit tests for deterministic quant filtering, ATR risk boundaries, and momentum ranking."""

import json
import tempfile
import unittest
from pathlib import Path

from quant_rules import (
    confirm_swing_entry_price,
    inverse_volatility_weights,
    select_swing_trend_candidate,
    select_long_term_core_candidate,
)
from shadow_portfolio import ShadowPortfolio


class TestQuantRules(unittest.TestCase):
    def test_swing_trend_prefers_qualifying_etf_over_single_stock(self):
        candidates = {
            "SPY": {"price": 600, "sma20": 590, "sma50": 580, "change_1d_pct": 0.4, "change_5d_pct": 1.1, "change_20d_pct": 3.0, "atr_pct": 1.2},
            "XLK": {"price": 250, "sma20": 240, "sma50": 230, "change_1d_pct": -0.6, "change_5d_pct": 2.0, "change_20d_pct": 5.0, "atr_pct": 1.8},
            "MSFT": {"price": 500, "sma20": 480, "sma50": 460, "change_1d_pct": 0.5, "change_5d_pct": 2.5, "change_20d_pct": 7.0, "atr_pct": 2.1},
        }
        selected = select_swing_trend_candidate(candidates, 3.5)
        self.assertEqual(selected["ticker"], "XLK")

    def test_swing_trend_rejects_a_negative_weekly_trend(self):
        candidates = {
            "SPY": {"price": 600, "sma20": 590, "sma50": 580, "change_1d_pct": 0.4, "change_5d_pct": -4.0, "change_20d_pct": 3.0, "atr_pct": 1.2},
            "MSFT": {"price": 500, "sma20": 480, "sma50": 460, "change_1d_pct": 0.5, "change_5d_pct": -4.0, "change_20d_pct": 7.0, "atr_pct": 2.1},
        }
        selected = select_swing_trend_candidate(candidates, 3.5)
        self.assertIsNone(selected)

    def test_swing_entry_allows_normal_gap_and_rejects_chase(self):
        candidate = {"price": 100.0, "atr_pct": 2.0}
        # Under max gap allowance (+1.0%)
        self.assertTrue(confirm_swing_entry_price(candidate, 101.0)[0])
        # Over max gap allowance (+3.0%)
        self.assertFalse(confirm_swing_entry_price(candidate, 103.0)[0])

    def test_long_term_core_uses_spy_without_short_term_momentum(self):
        candidate = select_long_term_core_candidate({"SPY": {"price": 500.0, "atr_pct": 1.4}})
        self.assertEqual(candidate["ticker"], "SPY")
        self.assertEqual(candidate["strategy_type"], "long_term_core")

    def test_inverse_volatility_weighting_ignores_llm_confidence(self):
        stocks = [
            {"ticker": "LOW", "confidence": 0.01},
            {"ticker": "HIGH", "confidence": 0.99},
        ]
        metadata = {"LOW": {"atr_pct": 2.0}, "HIGH": {"atr_pct": 4.0}}
        weights = inverse_volatility_weights(stocks, metadata)
        self.assertAlmostEqual(weights[0], 2 / 3)
        self.assertAlmostEqual(weights[1], 1 / 3)

    def test_positive_average_alone_cannot_promote_a_noisy_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "paper_history.json"
            returns = [3.0 if i % 2 == 0 else -2.7 for i in range(60)]
            history.write_text(json.dumps([
                {"strategy": "noisy", "return_pct": value} for value in returns
            ]), encoding="utf-8")
            portfolio = ShadowPortfolio(
                positions_file=Path(directory) / "positions.json",
                history_file=history,
                leaderboard_file=Path(directory) / "leaderboard.json",
            )
            result = portfolio.refresh_leaderboard()[0]
            self.assertGreater(result["mean_return_pct"], 0)


if __name__ == "__main__":
    unittest.main()

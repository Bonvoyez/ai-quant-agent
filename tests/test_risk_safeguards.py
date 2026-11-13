"""Unit tests for risk safeguards, parameter boundaries, and circuit breakers."""

import json
import tempfile
import unittest
from pathlib import Path

from settings_manager import SettingsManager, DEFAULT_SETTINGS


class TestRiskSafeguards(unittest.TestCase):
    def test_default_settings_boundaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "settings.json"
            mgr = SettingsManager(str(settings_file))
            loaded = mgr.load()

            self.assertFalse(loaded["paused"])
            self.assertEqual(loaded["max_positions"], DEFAULT_SETTINGS["max_positions"])
            self.assertEqual(loaded["risk_mode"], "balanced")
            self.assertLessEqual(loaded["max_daily_loss_pct"], 10.0)

    def test_settings_validation_clamps_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "settings.json"
            # Write invalid / extreme values that should be sanitized
            settings_file.write_text(json.dumps({
                "max_positions": 999,  # exceeds reasonable limit
                "max_budget_pct": -50,  # negative budget
                "risk_mode": "invalid_mode",
            }))

            mgr = SettingsManager(str(settings_file))
            loaded = mgr.load()

            # Invalid risk_mode must fall back to "balanced"
            self.assertEqual(loaded["risk_mode"], "balanced")
            # max_positions clamped to 10
            self.assertLessEqual(loaded["max_positions"], 10)
            # max_budget_pct clamped to >= 5.0
            self.assertGreaterEqual(loaded["max_budget_pct"], 5.0)

    def test_blacklist_ticker_exclusion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "settings.json"
            settings_file.write_text(json.dumps({
                "excluded_tickers": ["TSLA", "NVDA"],
            }))

            mgr = SettingsManager(str(settings_file))
            loaded = mgr.load()
            self.assertIn("TSLA", loaded["excluded_tickers"])
            self.assertIn("NVDA", loaded["excluded_tickers"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for stateless LLM response parsing and schema validation."""

import unittest
from ai_client import AIClient


class TestSchemaValidation(unittest.TestCase):
    def setUp(self):
        # AIClient initialized with None session since network is mocked/not needed
        self.client = AIClient(session=None)

    def test_parse_clean_json_object(self):
        raw = '{"market_regime_hypothesis": "RISK_ON", "evidence": ["SPY above 50d SMA"], "research_note": "Bullish"}'
        parsed = self.client.parse_json_response(raw)
        self.assertEqual(parsed["market_regime_hypothesis"], "RISK_ON")
        self.assertEqual(len(parsed["evidence"]), 1)
        self.assertEqual(parsed["research_note"], "Bullish")

    def test_parse_json_wrapped_in_markdown_codeblock(self):
        raw = """
Here is the requested analysis:
```json
{
  "market_regime_hypothesis": "MIXED",
  "evidence": ["VIX elevated at 22", "Tech leading"],
  "counter_evidence": ["Yield curve flattening"],
  "invalidation_condition": "VIX spikes > 25",
  "research_note": "Proceed with caution."
}
```
Thank you.
"""
        parsed = self.client.parse_json_response(raw)
        self.assertEqual(parsed["market_regime_hypothesis"], "MIXED")
        self.assertEqual(len(parsed["evidence"]), 2)
        self.assertEqual(len(parsed["counter_evidence"]), 1)

    def test_parse_json_missing_object_raises_error(self):
        raw = "I apologize, but I cannot evaluate the market right now."
        with self.assertRaises(ValueError):
            self.client.parse_json_response(raw)

    def test_parse_nested_json(self):
        raw = 'Some text {"regime": "RISK_OFF", "metrics": {"vix": 28.5, "breadth": -0.4}} and trailing text'
        parsed = self.client.parse_json_response(raw)
        self.assertEqual(parsed["regime"], "RISK_OFF")
        self.assertEqual(parsed["metrics"]["vix"], 28.5)


if __name__ == "__main__":
    unittest.main()

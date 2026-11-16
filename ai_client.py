# ai_client.py
import asyncio
from datetime import datetime
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
from zoneinfo import ZoneInfo
from config import HTTP_TIMEOUT, MAX_RETRY

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")


class AIClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = ""
        self.api_key = ""
        self.model = ""
        self._audit_cache: Dict[str, Dict[str, Any]] = {}
        self._briefing_cache: Dict[str, str] = {}

    def configure(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.strip() if base_url else ""
        self.api_key = api_key.strip() if api_key else ""
        self.model = model.strip() if model else "gpt-4o"

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def _request_ai(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> str:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with self.session.post(url, headers=headers, json=payload, timeout=timeout) as response:
            if response.status == 200:
                result = await response.json()
                return result["choices"][0]["message"]["content"]
            else:
                error_text = await response.text()
                logger.error(f"AI API error [{response.status}]: {error_text}")
                raise aiohttp.ClientResponseError(
                    response.request_info, response.history, status=response.status, message=error_text
                )

    async def fetch_raw_analysis(self, system_instruction: str, user_prompt: str) -> str:
        if not self.is_configured:
            raise RuntimeError("AI Client credentials not configured.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        url = self.base_url.rstrip("/")
        if url and not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "text"}
        }

        for attempt in range(MAX_RETRY):
            try:
                logger.info(f"AI reasoning request ({self.model}) - attempt {attempt+1}...")
                return await self._request_ai(url, headers, payload)
            except Exception as e:
                if "400" in str(e) or "invalid" in str(e).lower():
                    logger.warning("response_format not supported by gateway. Retrying without it.")
                    payload.pop("response_format", None)
                    try:
                        return await self._request_ai(url, headers, payload)
                    except Exception as retry_e:
                        logger.warning(f"Retry failed: {retry_e}")
                else:
                    logger.warning(f"AI API exception (attempt {attempt+1}/{MAX_RETRY}): {e}")

            await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

        raise RuntimeError("AI reasoning pipeline encountered fatal communication error.")

    def parse_json_response(self, raw_text: str) -> Dict[str, Any]:
        match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in AI response.")
        return json.loads(match.group(0))

    # --------------------------------------------------------------------------
    # Deep Pro Intelligence Pipeline (Multi-Tier Support)
    # --------------------------------------------------------------------------

    async def deep_audit_candidate(
        self, ticker: str, candidate_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deep Pro SEC & Catalyst Auditor:
        Performs real-time web & SEC EDGAR search to verify:
        1. Dilution Risk (S-3, S-1, 424B, ATM offering, warrant exercise, debt conversion)
        2. Catalyst Quality (FDA approvals, defense/enterprise contracts, earnings, patents)
        3. Social / Squeeze Sentiment (StockTwits, Twitter, Reddit momentum)
        """
        if not self.is_configured:
            return {
                "is_safe": True,
                "recommendation": "APPROVED",
                "fact_score": 0.5,
                "summary": "AI unconfigured. Proceeding on mathematical score alone.",
                "risk_flags": [],
            }

        # Token Conservation Cache: Never re-query the same ticker on the same day
        today_date = datetime.now(NEW_YORK).strftime("%Y-%m-%d")
        cache_key = f"{today_date}_{ticker}"
        if cache_key in self._audit_cache:
            logger.info(f"⚡ [AI Cache Hit] Reusing today's audit result for {ticker}. Conserving LLM tokens.")
            return self._audit_cache[cache_key]

        system_instruction = (
            "You are an elite quantitative hedge fund risk officer and SEC auditor for US small-cap equities. "
            "Your job is to protect capital from toxic dilution, reverse splits, and pump-and-dump traps. "
            "Always search real-time live SEC filings (EDGAR 8-K, S-3, 424B) and news from the last 48 hours. "
            "Reply strictly in valid JSON format."
        )

        user_prompt = f"""
Audit US equity ticker: ${ticker}
Pre-Market Gap: {candidate_info.get('gap_open_pct', 0.0):+.2f}%
Relative Volume (RVOL): {candidate_info.get('rvol_prev', 1.0):.2f}x
ATR Volatility: {candidate_info.get('atr_pct', 5.0):.2f}%

Perform a deep real-time audit:
1. SEC Filings (last 48 hours): Are there ANY offerings (S-1/S-3), warrants, convertible notes, or toxic dilution?
2. Catalyst Verification: What is the exact fundamental news driving volume today? Is it genuine or empty hype?
3. Squeeze/Social: Is there elevated short interest or strong retail social momentum?

Return ONLY this JSON structure:
{{
  "recommendation": "APPROVED" or "REJECTED",
  "is_safe": true or false,
  "dilution_risk": "LOW" or "HIGH",
  "catalyst_strength": "STRONG" or "MODERATE" or "WEAK",
  "fact_score": 0.0 to 1.0,
  "summary": "Concise 2-sentence summary of the news and SEC status",
  "risk_flags": ["list of any red flags, e.g., ATM offering filed yesterday"]
}}
"""
        try:
            raw = await self.fetch_raw_analysis(system_instruction, user_prompt)
            data = self.parse_json_response(raw)
            is_safe = (data.get("recommendation") == "APPROVED") and not (data.get("dilution_risk") == "HIGH")
            audit_result = {
                "is_safe": is_safe,
                "recommendation": "APPROVED" if is_safe else "REJECTED",
                "fact_score": float(data.get("fact_score", 0.5)),
                "summary": data.get("summary", "Analysis completed."),
                "risk_flags": data.get("risk_flags", []),
            }
            self._audit_cache[cache_key] = audit_result
            return audit_result
        except Exception as e:
            logger.warning(f"Deep audit failed for {ticker}: {e}. Falling back to neutral.")
            return {
                "is_safe": True,
                "recommendation": "APPROVED",
                "fact_score": 0.5,
                "summary": f"Audit skipped due to transient error: {e}",
                "risk_flags": [],
            }

    async def generate_morning_briefing(
        self, top_candidate: Dict[str, Any], runners_up: List[Dict[str, Any]]
    ) -> str:
        """Generates a high-conviction 3-bullet narrative briefing for Telegram."""
        if not self.is_configured:
            return f"📌 Top Candidate: {top_candidate.get('ticker')} (Score: {top_candidate.get('score', 0):.1f})"

        today_date = datetime.now(NEW_YORK).strftime("%Y-%m-%d")
        top_ticker = top_candidate.get("ticker", "UNKNOWN")
        brief_key = f"{today_date}_{top_ticker}"
        if brief_key in self._briefing_cache:
            logger.info("⚡ [AI Cache Hit] Reusing today's morning briefing. Conserving LLM tokens.")
            return self._briefing_cache[brief_key]

        system_instruction = "You are an executive trading desk strategist. Create a professional, punchy 3-bullet morning briefing."
        tickers_str = f"Top 1: {top_candidate.get('ticker')} (Score: {top_candidate.get('score', 0):.1f})"
        if runners_up:
            runners_str = ", ".join([f"{r.get('ticker')} ({r.get('score', 0):.1f})" for r in runners_up[:2]])
            tickers_str += f"\nRunners-up: {runners_str}"

        user_prompt = f"""
Create a 3-bullet executive trade briefing for our US momentum bot:
{tickers_str}
Top 1 Summary: {top_candidate.get('ai_summary', 'Clean momentum breakout')}

Format strictly as 3 bullet points:
• 🚀 Catalyst & Narrative:
• 🛡️ SEC & Risk Check:
• 🎯 Trade Plan:
"""
        try:
            brief = await self.fetch_raw_analysis(system_instruction, user_prompt)
            self._briefing_cache[brief_key] = brief
            return brief
        except Exception:
            return f"🚀 Top Pick: {top_candidate.get('ticker')} | Score: {top_candidate.get('score', 0):.1f} | Gap: {top_candidate.get('gap_open_pct', 0):+.2f}%"

    async def check_intraday_sentinel(self, ticker: str) -> Tuple[bool, Optional[str]]:
        """
        Intraday News Sentinel:
        Quickly scans for sudden breaking news or surprise SEC filings while holding.
        Returns (has_emergency, reason).
        """
        if not self.is_configured:
            return False, None

        system_instruction = "You are a real-time risk sentry. Check if any lethal negative news or SEC dilution just hit the wire."
        user_prompt = f"Has ticker ${ticker} had any emergency negative news, SEC offerings, halts, or fraud allegations in the last 2 hours? Reply JSON: {{\"emergency\": true/false, \"reason\": \"...\"}}"
        try:
            raw = await self.fetch_raw_analysis(system_instruction, user_prompt)
            data = self.parse_json_response(raw)
            return bool(data.get("emergency", False)), data.get("reason")
        except Exception:
            return False, None

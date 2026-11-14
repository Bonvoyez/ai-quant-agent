"""
breakout_strategy.py: Real-time Breakout Screening & Trailing Stop Execution Module

Coordinates:
1. Pre-market / Open screening using derived_formula.py (Score >= 450)
2. Execution via TossInvestService (buy_market_order)
3. Dynamic intraday monitoring via ExitManager (tight trailing stop: -1.5%, stop-loss: -3.0%)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from derived_formula import calculate_breakout_score, should_enter_breakout_trade
from exit_manager import ExitManager

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class ActivePosition:
    ticker: str
    entry_price: float
    highest_price: float
    entry_time: datetime
    exit_manager: ExitManager = field(default_factory=ExitManager)
    status: str = "OPEN"


class BreakoutStrategyRunner:
    def __init__(
        self,
        toss_service,
        telegram_service=None,
        score_threshold: float = 450.0,
        stop_loss_pct: float = -3.0,
        trailing_step_pct: float = 1.5,
    ):
        self.toss = toss_service
        self.telegram = telegram_service
        self.score_threshold = score_threshold
        self.stop_loss_pct = stop_loss_pct
        self.trailing_step_pct = trailing_step_pct
        self.active_positions: Dict[str, ActivePosition] = {}
        self._monitoring_active = False

    async def screen_and_enter(self, candidate_features: List[Dict]) -> List[str]:
        """
        Evaluate candidate feature dicts with derived_formula and execute buy orders.
        """
        entered_tickers = []
        for feat in candidate_features:
            ticker = feat.get("ticker", "UNKNOWN")
            should_enter = should_enter_breakout_trade(feat, threshold=self.score_threshold)
            score = calculate_breakout_score(feat)

            if should_enter:
                logger.info(f"🎯 [Breakout Candidate] {ticker} - Formula Score: {score:.2f} >= {self.score_threshold}")
                
                # Check buying power
                balance = await self.toss.get_balance()
                if balance < 10.0:
                    logger.warning(f"⚠️ Insufficient balance (${balance:.2f}) to enter {ticker}")
                    continue

                # Allocate up to $100 or available cash
                budget = min(balance, 100.0)
                current_price = await self.toss.get_realtime_price(ticker)
                if not current_price or current_price <= 0:
                    logger.warning(f"⚠️ Could not fetch price for {ticker}")
                    continue

                buy_success = await self.toss.buy_market_order(ticker, budget)
                if buy_success:
                    pos = ActivePosition(
                        ticker=ticker,
                        entry_price=current_price,
                        highest_price=current_price,
                        entry_time=datetime.now(NEW_YORK),
                        exit_manager=ExitManager(
                            stop_loss_pct=self.stop_loss_pct,
                            trailing_step_pct=self.trailing_step_pct,
                        ),
                    )
                    self.active_positions[ticker] = pos
                    entered_tickers.append(ticker)

                    msg = (
                        f"🚀 [Breakout Buy Executed]\n"
                        f"• Ticker: {ticker}\n"
                        f"• Formula Score: {score:.1f}\n"
                        f"• Entry Price: ${current_price:.2f}\n"
                        f"• Order Budget: ${budget:.2f}"
                    )
                    logger.info(msg)
                    if self.telegram:
                        await self.telegram.send_message(msg)

        return entered_tickers

    async def run_intraday_exit_loop(self, poll_interval_seconds: int = 60):
        """
        Intraday tracking loop checking trailing stops every poll interval.
        """
        self._monitoring_active = True
        logger.info("📡 [Breakout Strategy] Intraday trailing exit loop started.")

        try:
            while self._monitoring_active and self.active_positions:
                for ticker, pos in list(self.active_positions.items()):
                    current_price = await self.toss.get_realtime_price(ticker)
                    if not current_price or current_price <= 0:
                        continue

                    # Update peak price
                    if current_price > pos.highest_price:
                        pos.highest_price = current_price

                    # Evaluate exit logic
                    action, ratio, reason = pos.exit_manager.evaluate_exit(
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        highest_price_since_entry=pos.highest_price,
                    )

                    if action == "SELL":
                        curr_ret = ((current_price - pos.entry_price) / pos.entry_price) * 100.0
                        logger.info(f"🚨 [Exit Triggered] {ticker} - Reason: {reason} | Return: {curr_ret:.2f}%")
                        sell_success = await self.toss.sell_market_order(ticker)
                        if sell_success:
                            del self.active_positions[ticker]
                            msg = (
                                f"🏁 [Breakout Exit Executed]\n"
                                f"• Ticker: {ticker}\n"
                                f"• Reason: {reason}\n"
                                f"• Exit Price: ${current_price:.2f}\n"
                                f"• Return: {curr_ret:+.2f}%"
                            )
                            logger.info(msg)
                            if self.telegram:
                                await self.telegram.send_message(msg)

                await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("🛑 [Breakout Strategy] Exit loop cancelled.")
        finally:
            self._monitoring_active = False

    def stop_monitoring(self):
        self._monitoring_active = False

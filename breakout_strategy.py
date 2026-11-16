"""
breakout_strategy.py: Snowball Nanocap Runner Execution Module

Coordinates:
1. Daily Top 1 candidate selection using derived_formula.py
2. Execution via BaseBroker (TossInvestService or MockBroker)
3. Intraday trailing stop & tight stop-loss via ExitManager
4. Atomic persistence to active_trades.json & breakout_positions.json for recovery
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from derived_formula import calculate_breakout_score, should_enter_breakout_trade
from exit_manager import ExitManager
from broker import MockBroker
from utils import atomic_write

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")
POSITIONS_FILE = "breakout_positions.json"


@dataclass
class ActivePosition:
    ticker: str
    entry_price: float
    highest_price: float
    entry_time: datetime
    exit_manager: ExitManager = field(default_factory=ExitManager)
    status: str = "OPEN"

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "entry_price": self.entry_price,
            "highest_price": self.highest_price,
            "entry_time": self.entry_time.isoformat(),
            "status": self.status,
            "stop_loss_pct": self.exit_manager.stop_loss_pct,
            "trailing_step_pct": self.exit_manager.trailing_step_pct,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ActivePosition":
        mgr = ExitManager(
            stop_loss_pct=float(data.get("stop_loss_pct", -3.0)),
            trailing_step_pct=float(data.get("trailing_step_pct", 2.0)),
        )
        try:
            etime = datetime.fromisoformat(data["entry_time"])
        except Exception:
            etime = datetime.now(NEW_YORK)

        return cls(
            ticker=data["ticker"],
            entry_price=float(data["entry_price"]),
            highest_price=float(data.get("highest_price", data["entry_price"])),
            entry_time=etime,
            exit_manager=mgr,
            status=data.get("status", "OPEN"),
        )


class BreakoutStrategyRunner:
    def __init__(
        self,
        broker,
        telegram_service=None,
        score_threshold: float = 15.0,
        stop_loss_pct: float = -3.0,
        trailing_step_pct: float = 2.0,
        max_positions: int = 1,
        storage_file: str = POSITIONS_FILE,
    ):
        self.broker = broker
        # Backward compatibility alias
        self.toss = broker
        self.telegram = telegram_service
        self.score_threshold = score_threshold
        self.stop_loss_pct = stop_loss_pct
        self.trailing_step_pct = trailing_step_pct
        self.max_positions = max_positions
        self.storage_file = storage_file
        self.active_positions: Dict[str, ActivePosition] = {}
        self._monitoring_active = False

        # Load persisted positions on startup
        self._load_positions()

    def _load_positions(self):
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        pos = ActivePosition.from_dict(item)
                        self.active_positions[pos.ticker] = pos
                logger.info(f"💾 [Breakout Strategy] Loaded {len(self.active_positions)} active positions from disk.")
            except Exception as e:
                logger.error(f"Failed to load {self.storage_file}: {e}")

    def _persist_positions(self):
        try:
            records = [pos.to_dict() for pos in self.active_positions.values()]
            atomic_write(self.storage_file, records)
        except Exception as e:
            logger.error(f"Failed to persist {self.storage_file}: {e}")

    async def screen_and_enter(self, candidate_features: List[Dict]) -> List[str]:
        valid_candidates = []
        for feat in candidate_features:
            ticker = feat.get("ticker", "UNKNOWN")
            if should_enter_breakout_trade(feat, threshold=self.score_threshold):
                score = calculate_breakout_score(feat)
                valid_candidates.append((score, ticker, feat))

        valid_candidates.sort(key=lambda x: x[0], reverse=True)
        top_picks = valid_candidates[: self.max_positions]

        entered_tickers = []
        for score, ticker, feat in top_picks:
            if ticker in self.active_positions:
                continue

            logger.info(f"🎯 [Snowball Top Pick] {ticker} - Formula Score: {score:.2f}")

            balance = await self.broker.get_balance()
            if balance < 10.0:
                logger.warning(f"⚠️ Insufficient balance (${balance:.2f}) to enter {ticker}")
                continue

            dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
            cap_env = float(os.getenv("ROTATION_CAPITAL_CAP_USD", "0") or 0)
            effective_balance = min(balance, cap_env) if cap_env > 0 else balance

            available_slots = max(1, self.max_positions - len(self.active_positions))
            budget = effective_balance / available_slots
            current_price = await self.broker.get_realtime_price(ticker)
            if not current_price or current_price <= 0:
                logger.warning(f"⚠️ Could not fetch price for {ticker}")
                continue

            if dry_run and not isinstance(self.broker, MockBroker):
                logger.info(f"🧪 [DRY RUN] 가상 매수 집행: BUY {ticker} (${budget:.2f} @ ${current_price:.2f})")
                buy_success = True
            else:
                buy_success = await self.broker.buy_market_order(ticker, budget)

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
                self._persist_positions()
                entered_tickers.append(ticker)

                msg = (
                    f"🚀 [Snowball Buy Executed]\n"
                    f"• Ticker: {ticker}\n"
                    f"• Score: {score:.1f}\n"
                    f"• Entry Price: ${current_price:.2f}\n"
                    f"• Budget: ${budget:.2f}\n"
                    f"• Stop: -3.0% / Trailing: -2.0% from peak"
                )
                logger.info(msg)
                if self.telegram:
                    await self.telegram.send_message(msg)

        return entered_tickers

    async def run_intraday_exit_loop(self, poll_interval_seconds: int = 60):
        self._monitoring_active = True
        logger.info("📡 [Snowball Strategy] Intraday trailing exit loop started.")

        try:
            while self._monitoring_active and self.active_positions:
                for ticker, pos in list(self.active_positions.items()):
                    current_price = await self.broker.get_realtime_price(ticker)
                    if not current_price or current_price <= 0:
                        continue

                    if current_price > pos.highest_price:
                        pos.highest_price = current_price
                        self._persist_positions()

                    action, ratio, reason = pos.exit_manager.evaluate_exit(
                        entry_price=pos.entry_price,
                        current_price=current_price,
                        highest_price_since_entry=pos.highest_price,
                    )

                    if action == "SELL":
                        curr_ret = ((current_price - pos.entry_price) / pos.entry_price) * 100.0
                        logger.info(f"🚨 [Exit Triggered] {ticker} - Reason: {reason} | Return: {curr_ret:.2f}%")
                        dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
                        if dry_run and not isinstance(self.broker, MockBroker):
                            logger.info(f"🧪 [DRY RUN] 가상 매도 집행: SELL {ticker} ({curr_ret:+.2f}%)")
                            sell_success = True
                        else:
                            sell_success = await self.broker.sell_market_order(ticker)

                        if sell_success:
                            del self.active_positions[ticker]
                            self._persist_positions()
                            msg = (
                                f"🏁 [Snowball Exit Executed]\n"
                                f"• Ticker: {ticker}\n"
                                f"• Reason: {reason}\n"
                                f"• Exit Price: ${current_price:.2f}\n"
                                f"• PnL Return: {curr_ret:+.2f}%"
                            )
                            logger.info(msg)
                            if self.telegram:
                                await self.telegram.send_message(msg)

                await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("🛑 [Snowball Strategy] Exit loop cancelled.")
        finally:
            self._monitoring_active = False

    def stop_monitoring(self):
        self._monitoring_active = False

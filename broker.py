"""
broker.py: Pluggable Broker Abstraction Layer.
Defines BaseBroker interface for exchange-agnostic execution:
- TossInvestService (Live Toss Broker)
- MockBroker (Pure paper-trading broker with zero external credentials required)
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import logging

logger = logging.getLogger("StockReporter")


class BaseBroker(ABC):
    @abstractmethod
    async def get_balance(self) -> float:
        """Returns available buying power in USD."""
        pass

    @abstractmethod
    async def get_realtime_price(self, ticker: str) -> Optional[float]:
        """Returns real-time price in USD or None if unavailable."""
        pass

    @abstractmethod
    async def buy_market_order(self, ticker: str, budget_usd: float) -> bool:
        """Executes a market BUY order with the specified budget."""
        pass

    @abstractmethod
    async def sell_market_order(self, ticker: str) -> bool:
        """Executes a market SELL order for all held shares of ticker."""
        pass

    @abstractmethod
    async def get_sellable_quantity(self, ticker: str) -> Optional[float]:
        """Returns held sellable quantity."""
        pass


class MockBroker(BaseBroker):
    """
    In-memory / Simulated broker for paper trading, dry-run, and offline testing.
    Requires ZERO API credentials.
    """
    def __init__(self, initial_cash_usd: float = 1000.0):
        self.cash = initial_cash_usd
        self.positions: Dict[str, float] = {}  # ticker -> qty
        self.price_cache: Dict[str, float] = {}

    async def get_balance(self) -> float:
        return self.cash

    def set_mock_price(self, ticker: str, price: float):
        self.price_cache[ticker] = price

    async def get_realtime_price(self, ticker: str) -> Optional[float]:
        if ticker in self.price_cache:
            return self.price_cache[ticker]
        # Fallback to yfinance if not manually mocked
        try:
            import yfinance as yf
            import asyncio
            ticker_obj = await asyncio.to_thread(yf.Ticker, ticker)
            fast_info = await asyncio.to_thread(lambda: getattr(ticker_obj, "fast_info", {}))
            price = fast_info.get("last_price") or fast_info.get("previous_close")
            if price and price > 0:
                self.price_cache[ticker] = float(price)
                return float(price)
        except Exception:
            pass
        return self.price_cache.get(ticker, 10.0)

    async def buy_market_order(self, ticker: str, budget_usd: float) -> bool:
        price = await self.get_realtime_price(ticker)
        if not price or price <= 0:
            return False
        if self.cash < budget_usd:
            logger.warning(f"[MockBroker] Insufficient cash: ${self.cash:.2f} < ${budget_usd:.2f}")
            return False
        qty = budget_usd / price
        self.cash -= budget_usd
        self.positions[ticker] = self.positions.get(ticker, 0.0) + qty
        logger.info(f"🧪 [MockBroker BUY] {ticker} - Qty: {qty:.4f} @ ${price:.2f} (Remaining Cash: ${self.cash:.2f})")
        return True

    async def sell_market_order(self, ticker: str) -> bool:
        qty = self.positions.get(ticker, 0.0)
        if qty <= 0:
            return False
        price = await self.get_realtime_price(ticker) or 10.0
        proceeds = qty * price
        self.cash += proceeds
        del self.positions[ticker]
        logger.info(f"🧪 [MockBroker SELL] {ticker} - Qty: {qty:.4f} @ ${price:.2f} (Total Cash: ${self.cash:.2f})")
        return True

    async def get_sellable_quantity(self, ticker: str) -> Optional[float]:
        return self.positions.get(ticker, 0.0)

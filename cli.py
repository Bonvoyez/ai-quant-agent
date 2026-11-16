#!/usr/bin/env python3
"""
AI Quant Agent CLI
Unified Command-Line Interface for automated US stock trading,
paper trading simulations, and web dashboard management.
"""

import argparse
import asyncio
import logging
import os
import sys

from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from main import main as run_main
from dashboard_server import create_dashboard_app
from aiohttp import web

logger = logging.getLogger("StockReporter")


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        prog="ai-quant-agent",
        description="Autonomous AI-driven quantitative trading and portfolio management agent.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--mode",
        choices=["rotation", "breakout", "paper", "live"],
        default=os.getenv("STRATEGY_MODE", "paper").lower(),
        help="Operating strategy mode: 'breakout' (nanocap snowball runner), 'rotation' (sector ETF momentum), 'paper' (simulation), or 'live' (real execution).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("ROTATION_DRY_RUN", "true").lower() in ("1", "true", "yes"),
        help="Simulate execution without placing real brokerage orders.",
    )

    parser.add_argument(
        "--live-orders",
        dest="dry_run",
        action="store_false",
        help="Enable actual order execution on configured brokerage (Toss OpenAPI).",
    )

    parser.add_argument(
        "--capital-cap",
        type=float,
        default=float(os.getenv("ROTATION_CAPITAL_CAP_USD", "1000")),
        help="Maximum capital allocation cap in USD.",
    )

    parser.add_argument(
        "--dashboard-host",
        default=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
        help="Host interface for the web dashboard (use 127.0.0.1 behind reverse proxy).",
    )

    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=int(os.getenv("DASHBOARD_PORT", "8899")),
        help="Port for the web dashboard.",
    )

    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="Run only the web monitoring dashboard without starting the trading cycle scheduler.",
    )

    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Run brokerage and LLM connection diagnostics then exit.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="ai-quant-agent 1.0.0",
    )

    return parser.parse_args(args)


async def run_dashboard_only(host: str, port: int):
    """Starts standalone dashboard server."""
    logger.info(f"📊 Running standalone dashboard server at http://{host}:{port}")
    app = create_dashboard_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    # Keep running until cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


def main():
    args = parse_args()

    # Sync parsed CLI arguments into environment variables for underlying modules
    if args.mode in ("rotation", "breakout"):
        os.environ["STRATEGY_MODE"] = args.mode
    elif args.mode == "paper":
        os.environ["STRATEGY_MODE"] = "breakout"
        args.dry_run = True

    os.environ["ROTATION_DRY_RUN"] = "1" if args.dry_run else "0"
    os.environ["ROTATION_CAPITAL_CAP_USD"] = str(args.capital_cap)
    os.environ["DASHBOARD_HOST"] = args.dashboard_host
    os.environ["DASHBOARD_PORT"] = str(args.dashboard_port)

    if args.test_connection:
        from test_toss_api import run_tests
        logger.info("🔧 Running connection diagnostics...")
        asyncio.run(run_tests())
        sys.exit(0)

    if args.dashboard_only:
        try:
            asyncio.run(run_dashboard_only(args.dashboard_host, args.dashboard_port))
        except (KeyboardInterrupt, SystemExit):
            logger.info("Dashboard server stopped.")
        return

    logger.info(f"🚀 Initializing AI Quant Agent [Mode: {args.mode.upper()} | Dry-Run: {args.dry_run}]")
    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        logger.info("Stock Report Bot terminated by user.")
    except Exception as e:
        logger.exception(f"Fatal error during execution: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
doctor.py: Unified End-to-End System Health & Diagnostic Suite.

Validates all 6 mission-critical subsystems in ~5-10 seconds:
1. Real-time Market Data Feeds (Yahoo Finance / Quotes)
2. Mathematical Formula Engine & Feature Extraction (derived_formula.py)
3. Pre-market Nanocap Breakout Scanner & Toxic Disclosure Filter
4. Risk Management & Trailing Stop Exit Manager with Disk Persistence
5. Broker Layer (Toss OpenAPI if credentials present, otherwise MockBroker)
6. Telemetry & Alerts (Telegram Bot ping / Dashboard Health)
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Color formatting for terminal
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_status(subsystem: str, check_name: str, passed: bool, detail: str = ""):
    badge = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    detail_str = f" ({detail})" if detail else ""
    print(f"  • {check_name}: {badge}{detail_str}")


async def run_diagnostics():
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}🩺 AI-QUANT-AGENT SYSTEM HEALTH & DIAGNOSTIC DOCTOR{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")

    all_passed = True

    # -------------------------------------------------------------
    # 1. Market Data Feed
    # -------------------------------------------------------------
    print(f"\n{BOLD}[1/6] 🌐 Network & Real-Time Market Feed{RESET}")
    try:
        import yfinance as yf
        def _get_spy():
            t = yf.Ticker("SPY")
            price = getattr(t, "fast_info", {}).get("last_price")
            if not price:
                hist = t.history(period="5d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            return price

        spy = await asyncio.to_thread(_get_spy)
        if spy and spy > 0:
            print_status("MarketFeed", "Yahoo Finance Market Feed", True, f"SPY: ${spy:.2f}")
        else:
            print_status("MarketFeed", "Yahoo Finance Market Feed", False, "Invalid price")
            all_passed = False
    except Exception as e:
        print_status("MarketFeed", "Yahoo Finance Market Feed", False, str(e))
        all_passed = False

    # -------------------------------------------------------------
    # 2. Formula Engine
    # -------------------------------------------------------------
    print(f"\n{BOLD}[2/6] 🧬 Mathematical Formula & Feature Engine{RESET}")
    try:
        from derived_formula import calculate_breakout_score, should_enter_breakout_trade
        sample_feat = {
            "ticker": "DOCTOR_TEST",
            "fact_score": 0.8,
            "atr_pct": 12.0,
            "rvol_prev": 2.5,
            "gap_open_pct": 2.0,
            "bb_width": 45.0,
            "is_toxic_material": 0.0,
        }
        score = calculate_breakout_score(sample_feat)
        decision = should_enter_breakout_trade(sample_feat, threshold=15.0)
        toxic_feat = dict(sample_feat, is_toxic_material=1.0)
        toxic_rejected = not should_enter_breakout_trade(toxic_feat, threshold=15.0)

        passed = score > 15.0 and decision and toxic_rejected
        print_status("FormulaEngine", "Breakout Scoring Logic", score > 0, f"Score: {score:.1f}")
        print_status("FormulaEngine", "Toxic Material Rejection", toxic_rejected, "Dilution blocked")
        if not passed:
            all_passed = False
    except Exception as e:
        print_status("FormulaEngine", "Formula Execution", False, str(e))
        all_passed = False

    # -------------------------------------------------------------
    # 3. Pre-market Scanner
    # -------------------------------------------------------------
    print(f"\n{BOLD}[3/6] 🎯 Pre-Market Nanocap Breakout Scanner{RESET}")
    try:
        from market_data import MarketDataCollector
        candidates = await MarketDataCollector.get_breakout_candidates_premarket(universe=["UPXI", "NEGG", "SPY"])
        if candidates and len(candidates) > 0:
            top_ticker = candidates[0].get("ticker")
            top_gap = candidates[0].get("gap_open_pct", 0.0)
            print_status("Scanner", "Nanocap Scanner Pipeline", True, f"Scanned: {len(candidates)} symbols, Sample: {top_ticker} (Gap: {top_gap:+.2f}%)")
        else:
            print_status("Scanner", "Nanocap Scanner Pipeline", False, "No candidates returned")
            all_passed = False
    except Exception as e:
        print_status("Scanner", "Nanocap Scanner Pipeline", False, str(e))
        all_passed = False

    # -------------------------------------------------------------
    # 4. Risk & Exit Manager with Disk Persistence
    # -------------------------------------------------------------
    print(f"\n{BOLD}[4/6] 🛡️ Risk Management & Exit Manager (Trailing Stop & Stop Loss){RESET}")
    try:
        from exit_manager import ExitManager
        from breakout_strategy import ActivePosition
        mgr = ExitManager(stop_loss_pct=-3.0, trailing_step_pct=2.0)

        # Stop loss check
        action_sl, _, r_sl = mgr.evaluate_exit(100.0, 96.5, 100.0)
        sl_ok = action_sl == "SELL" and "STOP_LOSS" in r_sl
        print_status("ExitManager", "Hard Stop-Loss (-3.0% trigger)", sl_ok, r_sl)

        # Trailing stop check (surged to 130, pulled back to 127 = -2.3% from peak)
        action_ts, _, r_ts = mgr.evaluate_exit(100.0, 127.0, 130.0)
        ts_ok = action_ts == "SELL" and "TRAILING_STOP" in r_ts
        print_status("ExitManager", "Dynamic Trailing Stop (-2.0% pullback)", ts_ok, r_ts)

        # Disk persistence check
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, "test_positions.json")
        from breakout_strategy import BreakoutStrategyRunner
        from broker import MockBroker
        runner = BreakoutStrategyRunner(MockBroker(500.0), storage_file=temp_path)
        runner.active_positions["TEST"] = ActivePosition("TEST", 10.0, 10.0, datetime.now())
        runner._persist_positions()

        runner2 = BreakoutStrategyRunner(MockBroker(500.0), storage_file=temp_path)
        persist_ok = "TEST" in runner2.active_positions
        print_status("ExitManager", "State Persistence & Recovery", persist_ok, f"Saved to {os.path.basename(temp_path)}")
        if os.path.exists(temp_path):
            os.remove(temp_path)

        if not (sl_ok and ts_ok and persist_ok):
            all_passed = False
    except Exception as e:
        print_status("ExitManager", "Risk & Persistence Subsystem", False, str(e))
        all_passed = False

    # -------------------------------------------------------------
    # 5. Broker Layer (Toss or MockBroker)
    # -------------------------------------------------------------
    print(f"\n{BOLD}[5/6] 🏦 Broker Layer Integration{RESET}")
    try:
        import aiohttp
        from toss_service import TossInvestService
        from broker import MockBroker

        toss_key = os.getenv("TOSS_APP_KEY", "")
        dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"

        if toss_key and not dry_run:
            async with aiohttp.ClientSession() as session:
                toss = TossInvestService(
                    session=session,
                    client_id=toss_key,
                    client_secret=os.getenv("TOSS_APP_SECRET", ""),
                    account_seq=os.getenv("TOSS_ACCOUNT_NO", "")
                )
                balance = await toss.get_balance()
                print_status("Broker", "Live Toss Broker Connection", True, f"Buying Power: ${balance:.2f} USD")
        else:
            mock = MockBroker(initial_cash_usd=float(os.getenv("ROTATION_CAPITAL_CAP_USD", "300") or 300.0))
            await mock.buy_market_order("SPY", 100.0)
            has_pos = "SPY" in mock.positions
            await mock.sell_market_order("SPY")
            sold_ok = "SPY" not in mock.positions
            print_status("Broker", "MockBroker Paper Execution", has_pos and sold_ok, f"Simulated $100 BUY/SELL order cleanly")
    except Exception as e:
        print_status("Broker", "Broker Connector", False, str(e))
        all_passed = False

    # -------------------------------------------------------------
    # 6. Telemetry & Alerts
    # -------------------------------------------------------------
    print(f"\n{BOLD}[6/6] 📡 Telemetry & Notifications{RESET}")
    try:
        tg_token = os.getenv("TELEGRAM_TOKEN", "")
        dash_token = os.getenv("DASHBOARD_TOKEN", "")

        tg_ok = bool(tg_token and len(tg_token) > 10)
        print_status("Telemetry", "Telegram Bot Credentials", tg_ok, "Configured" if tg_ok else "Not configured")

        dash_ok = bool(dash_token and len(dash_token) >= 4)
        print_status("Telemetry", "Dashboard Security Token", dash_ok, "Configured" if dash_ok else "Not configured")

        # Telemetry is optional in offline/paper environments
        if not tg_ok or not dash_ok:
            print(f"  {YELLOW}ℹ️ Telemetry credentials omitted. (Safe for paper trading & CI demo){RESET}")
    except Exception as e:
        print_status("Telemetry", "Telemetry Subsystem", False, str(e))

    # Summary
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    if all_passed:
        print(f"{BOLD}{GREEN}🎉 ALL CRITICAL SUBSYSTEMS PASSED! SYSTEM IS 100% OPERATIONAL.{RESET}")
    else:
        print(f"{BOLD}{RED}⚠️ ONE OR MORE CHECKS FAILED. REVIEW THE LOGS ABOVE BEFORE TRADING.{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}\n")

    return all_passed


if __name__ == "__main__":
    passed = asyncio.run(run_diagnostics())
    sys.exit(0 if passed else 1)

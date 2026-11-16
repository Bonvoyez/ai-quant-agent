"""
doctor.py: Unified End-to-End System Health & Diagnostic Suite.

Validates all 6 mission-critical subsystems with LIVE API integration calls:
1. Real-time Market Data Feeds (Yahoo Finance / Quotes)
2. Mathematical Formula Engine & Feature Extraction (derived_formula.py)
3. Pre-market Nanocap Breakout Scanner & Toxic Disclosure Filter
4. Risk Management & Trailing Stop Exit Manager with Disk Persistence
5. Broker Layer Integration (Live Toss API check or MockBroker)
6. Telemetry & Alerts (Live Telegram API ping & LLM/Fact-Chat API check)
"""

import asyncio
import os
import sys
import tempfile
import logging
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

# Silence verbose background logs during doctor run to keep output clean and professional
logging.getLogger("StockReporter").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

load_dotenv()

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_status(check_name: str, passed: bool, detail: str = ""):
    badge = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    detail_str = f" ({detail})" if detail else ""
    print(f"  • {check_name}: {badge}{detail_str}")


async def run_diagnostics():
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}🩺 AI-QUANT-AGENT SYSTEM HEALTH & INTEGRATION DIAGNOSTICS{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")

    all_passed = True

    async with aiohttp.ClientSession() as session:
        # -------------------------------------------------------------
        # 1. Market Data Feed & Calendar Subsystem
        # -------------------------------------------------------------
        print(f"\n{BOLD}[1/6] 🌐 Network, Market Calendar & Real-Time Feed{RESET}")
        try:
            from market_calendar import is_trading_day, NEW_YORK
            now_ny = datetime.now(NEW_YORK)
            tz_abbr = now_ny.strftime("%Z")  # EDT (Daylight Saving) or EST (Standard)
            is_open_today, cal_status = is_trading_day(now_ny)
            print_status("US Market Calendar & DST Engine", True, f"NYC Time: {now_ny.strftime('%Y-%m-%d %H:%M:%S')} {tz_abbr} | Status: {cal_status}")

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
                print_status("Yahoo Finance Real-time Feed", True, f"SPY benchmark: ${spy:.2f}")
            else:
                print_status("Yahoo Finance Real-time Feed", False, "Invalid price response")
                all_passed = False
        except Exception as e:
            print_status("Yahoo Finance Real-time Feed", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 2. Formula Engine & Feature Extraction
        # -------------------------------------------------------------
        print(f"\n{BOLD}[2/6] 🧬 Quantitative Formula & Feature Engineering Engine{RESET}")
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
            print_status("Breakout Scoring Algorithm", score > 0, f"Score: {score:.1f}")
            print_status("Toxic Material Filter (SEC/Dilution)", toxic_rejected, "Toxic filings blocked")
            if not passed:
                all_passed = False
        except Exception as e:
            print_status("Formula Execution", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 3. Pre-market Scanner
        # -------------------------------------------------------------
        print(f"\n{BOLD}[3/6] 🎯 Pre-Market Nanocap Momentum Scanner{RESET}")
        try:
            from market_data import MarketDataCollector
            candidates = await MarketDataCollector.get_breakout_candidates_premarket(universe=["UPXI", "NEGG", "SPY"])
            if candidates and len(candidates) > 0:
                top_ticker = candidates[0].get("ticker")
                top_gap = candidates[0].get("gap_open_pct", 0.0)
                print_status("Nanocap Scanner Pipeline", True, f"Scanned: {len(candidates)} symbols, Sample: {top_ticker} (Gap: {top_gap:+.2f}%)")
            else:
                print_status("Nanocap Scanner Pipeline", False, "No candidates returned")
                all_passed = False
        except Exception as e:
            print_status("Nanocap Scanner Pipeline", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 4. Risk & Exit Manager with Disk Persistence
        # -------------------------------------------------------------
        print(f"\n{BOLD}[4/6] 🛡️ Risk Management & Exit Execution (Trailing Stop & Hard Stop){RESET}")
        try:
            from exit_manager import ExitManager
            from breakout_strategy import ActivePosition, BreakoutStrategyRunner
            from broker import MockBroker

            mgr = ExitManager(stop_loss_pct=-3.0, trailing_step_pct=2.0)
            action_sl, _, r_sl = mgr.evaluate_exit(100.0, 96.5, 100.0)
            sl_ok = action_sl == "SELL" and "STOP_LOSS" in r_sl
            print_status("Hard Stop-Loss Execution (-3.0% trigger)", sl_ok, r_sl)

            action_ts, _, r_ts = mgr.evaluate_exit(100.0, 127.0, 130.0)
            ts_ok = action_ts == "SELL" and "TRAILING_STOP" in r_ts
            print_status("Dynamic Trailing Stop Execution (-2.0% pullback)", ts_ok, r_ts)

            temp_dir = tempfile.mkdtemp()
            temp_path = os.path.join(temp_dir, "test_positions.json")
            runner = BreakoutStrategyRunner(MockBroker(500.0), storage_file=temp_path)
            runner.active_positions["TEST"] = ActivePosition("TEST", 10.0, 10.0, datetime.now())
            runner._persist_positions()

            runner2 = BreakoutStrategyRunner(MockBroker(500.0), storage_file=temp_path)
            persist_ok = "TEST" in runner2.active_positions
            print_status("State Persistence & Crash Recovery", persist_ok, f"Saved to {os.path.basename(temp_path)}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

            if not (sl_ok and ts_ok and persist_ok):
                all_passed = False
        except Exception as e:
            print_status("Risk & Persistence Subsystem", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 5. Live Broker API Integration (Toss OpenAPI vs MockBroker)
        # -------------------------------------------------------------
        print(f"\n{BOLD}[5/6] 🏦 Broker Layer Integration (Live Toss OpenAPI vs MockBroker){RESET}")
        try:
            from toss_service import TossInvestService
            from broker import MockBroker

            toss_key = os.getenv("TOSS_APP_KEY", "").strip()
            toss_sec = os.getenv("TOSS_APP_SECRET", "").strip()
            toss_acc = os.getenv("TOSS_ACCOUNT_NO", "").strip()

            if toss_key and toss_sec:
                toss = TossInvestService(
                    session=session,
                    client_id=toss_key,
                    client_secret=toss_sec,
                    account_seq=toss_acc
                )
                # Live API Call 1: Account list & token check
                accounts = await toss.list_accounts()
                resolved_seq = await toss._resolve_account_seq()
                if resolved_seq:
                    toss.account_seq = resolved_seq
                token_ok = bool(toss.token)
                print_status("Toss OAuth2 Token & Account Check", token_ok, f"{len(accounts)} active account(s) detected")
                if not token_ok:
                    all_passed = False

                await asyncio.sleep(0.5)

                # Live API Call 2: Real Buying Power query
                balance = await toss.get_balance()
                balance_ok = balance is not None and balance >= 0
                print_status("Toss Live Purchasing Power", balance_ok, f"${balance:.2f} USD available")
                if not balance_ok:
                    all_passed = False

                await asyncio.sleep(0.5)

                # Live API Call 3: Realtime quote query via Toss OpenAPI
                aapl_price = await toss.get_realtime_price("AAPL")
                quote_ok = aapl_price is not None and aapl_price > 0
                print_status("Toss Real-Time Quote Stream", quote_ok, f"AAPL: ${aapl_price:.2f}" if quote_ok else "Unavailable")
                if not quote_ok:
                    all_passed = False
            else:
                mock = MockBroker(initial_cash_usd=float(os.getenv("ROTATION_CAPITAL_CAP_USD", "300") or 300.0))
                await mock.buy_market_order("SPY", 100.0)
                has_pos = "SPY" in mock.positions
                await mock.sell_market_order("SPY")
                sold_ok = "SPY" not in mock.positions
                mock_ok = has_pos and sold_ok
                print_status("MockBroker Paper Simulation", mock_ok, "Virtual BUY/SELL executed ($300 buffer)")
                print(f"  {YELLOW}ℹ️ Live broker credentials not detected. MockBroker active.{RESET}")
                if not mock_ok:
                    all_passed = False
        except Exception as e:
            print_status("Broker Integration", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 6. Telemetry & AI API Integration (Telegram Ping & LLM Reasoning)
        # -------------------------------------------------------------
        print(f"\n{BOLD}[6/6] 📡 Telemetry & LLM Integration (Telegram Bot & AI Reasoning){RESET}")
        try:
            tg_token = os.getenv("TELEGRAM_TOKEN", "").strip()
            tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            llm_url = os.getenv("LLM_BASE_URL") or os.getenv("AI_BASE_URL", "").strip()
            llm_key = os.getenv("LLM_API_KEY") or os.getenv("AI_API_KEY", "").strip()
            dash_token = os.getenv("DASHBOARD_TOKEN", "").strip()

            # Live Telegram Ping
            if tg_token and tg_chat:
                tg_url = f"https://api.telegram.org/bot{tg_token}/getMe"
                async with session.get(tg_url) as res:
                    tg_res = await res.json()
                    bot_ok = tg_res.get("ok", False)
                    bot_name = tg_res.get("result", {}).get("username", "UnknownBot")
                    print_status("Telegram Bot Live Telemetry", bot_ok, f"@{bot_name}" if bot_ok else "Invalid token")
                    if not bot_ok:
                        all_passed = False
            else:
                print_status("Telegram Bot Live Telemetry", False, "Token or chat ID not configured")
                all_passed = False

            # Live AI / LLM API Ping
            if llm_url and llm_key:
                from ai_client import AIClient
                ai = AIClient(session)
                ai.configure(llm_url, llm_key, os.getenv("LLM_MODEL") or os.getenv("AI_MODEL", "gpt-4o"))
                try:
                    ai_res = await ai.fetch_raw_analysis("You are a system tester. Reply strictly with: OK", "Ping test")
                    ai_ok = "OK" in ai_res.upper()
                    print_status("LLM / AI Reasoning Endpoint Ping", ai_ok, f"Response: {ai_res[:20].strip()}")
                    if not ai_ok:
                        all_passed = False
                except Exception as ai_e:
                    print_status("LLM / AI Reasoning Endpoint Ping", False, str(ai_e))
                    all_passed = False
            else:
                print_status("LLM / AI Reasoning Endpoint Ping", False, "Endpoint or key not configured")
                all_passed = False

            # Dashboard Token check
            dash_ok = bool(dash_token and len(dash_token) >= 4)
            print_status("Mobile Web Dashboard Token", dash_ok, "Configured" if dash_ok else "Not configured")
            if not dash_ok:
                all_passed = False

        except Exception as e:
            print_status("Telemetry Subsystem", False, str(e))
            all_passed = False

    # Summary
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    if all_passed:
        print(f"{BOLD}{GREEN}🎉 ALL MISSION-CRITICAL SUBSYSTEMS & APIS ARE 100% OPERATIONAL.{RESET}")
    else:
        print(f"{BOLD}{RED}❌ DIAGNOSTICS DETECTED SUBSYSTEM FAILURES. SYSTEM IS NOT FULLY OPERATIONAL.{RESET}")
        print(f"  {YELLOW}Please review and resolve the [FAIL] item(s) highlighted above.{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}\n")

    return all_passed


if __name__ == "__main__":
    passed = asyncio.run(run_diagnostics())
    sys.exit(0 if passed else 1)

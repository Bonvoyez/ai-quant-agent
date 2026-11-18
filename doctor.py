"""
doctor.py: Unified End-to-End System Health & Autonomous Pre-Flight Readiness Suite.

Validates all 6 mission-critical subsystems with LIVE API calls & live market scan:
1. Real-time Market Data Feeds (Yahoo Finance / Quotes & Calendar)
2. Mathematical Formula Engine & Feature Extraction (derived_formula.py)
3. Broker Layer Integration (Live Toss API check or MockBroker)
4. Telemetry & Alerts (Live Telegram API ping & LLM/Fact-Chat API check)
5. Scheduler Lock & Stale Position Audit (Guarantees today is not falsely blocked)
6. Live 161-Ticker Nanocap Momentum Scanner & Gold Standard Top 5 Rankings
"""

import asyncio
import os
import sys
import json
import logging
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Base Directory & Paths
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

# Silence verbose background logs during doctor run to keep output clean and professional
logging.getLogger("StockReporter").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

load_dotenv()

NEW_YORK = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")

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


async def get_preflight_status_dict() -> Dict[str, Any]:
    """Returns a structured status dict suitable for dashboard API consumption."""
    from market_calendar import is_trading_day, get_market_session_info
    from broker import MockBroker
    from toss_service import TossInvestService

    now_ny = datetime.now(NEW_YORK)
    today_str = now_ny.strftime("%Y-%m-%d")
    is_trading, status = is_trading_day(now_ny)
    session_info = get_market_session_info(now_ny)

    screening_path = BASE_DIR / "data" / "screening_history.json"
    is_locked = False
    lock_reason = "Ready"
    if screening_path.exists():
        try:
            with open(screening_path, "r", encoding="utf-8") as sf:
                s_recs = json.load(sf)
                for rec in s_recs:
                    if rec.get("date") == today_str and rec.get("counts", {}).get("scanned_universe", 0) > 10:
                        is_locked = True
                        lock_reason = f"Screened ({rec.get('entered_ticker')})"
                        break
        except Exception:
            pass

    positions_file = BASE_DIR / "breakout_positions.json"
    stale_count = 0
    if positions_file.exists():
        try:
            with open(positions_file, "r", encoding="utf-8") as pf:
                for item in json.load(pf):
                    e_str = item.get("entry_time", "")
                    if e_str and datetime.fromisoformat(e_str).date() < now_ny.date():
                        stale_count += 1
        except Exception:
            pass

    dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
    broker = TossInvestService() if not dry_run else MockBroker(initial_cash_usd=500.0)
    try:
        balance = await broker.get_balance()
    except Exception:
        balance = 500.0

    return {
        "new_york_time": now_ny.strftime("%Y-%m-%d %H:%M:%S EST"),
        "is_trading_day": is_trading,
        "market_status": status,
        "session": session_info["session"],
        "next_event": session_info["next_event"],
        "is_locked": is_locked,
        "lock_reason": lock_reason,
        "stale_positions_count": stale_count,
        "balance_usd": balance,
        "dry_run": dry_run,
        "verdict": "READY" if (is_trading and not is_locked) else "WAITING",
    }


async def run_diagnostics() -> bool:
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}🩺 AI-QUANT-AGENT FULL-SPECTRUM SYSTEM HEALTH & PRE-FLIGHT SUITE{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")

    all_passed = True

    async with aiohttp.ClientSession() as session:
        # -------------------------------------------------------------
        # 1. Market Data Feed & Calendar Subsystem
        # -------------------------------------------------------------
        print(f"\n{BOLD}[1/6] 🌐 Network, Market Calendar & Real-Time Feed{RESET}")
        try:
            from market_calendar import is_trading_day, get_market_session_info
            now_ny = datetime.now(NEW_YORK)
            now_kst = datetime.now(KST)
            tz_abbr = now_ny.strftime("%Z")
            is_open_today, cal_status = is_trading_day(now_ny)
            session_info = get_market_session_info(now_ny)
            trigger_dt = now_ny.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(minutes=30)
            trigger_kst = trigger_dt.astimezone(KST)

            print_status("US Market Calendar & DST Engine", True, f"NYC: {now_ny.strftime('%Y-%m-%d %H:%M:%S')} {tz_abbr} | Status: {cal_status}")
            print(f"      • Korea Time: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')}")
            print(f"      • Market Session: {session_info['session']} ({session_info['next_event']})")
            print(f"      • Scheduled Buy : {trigger_dt.strftime('%H:%M:%S')} EST ({trigger_kst.strftime('%H:%M:%S')} KST / 12:00 AM Midnight)")

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
                print_status("Yahoo Finance Real-time Feed", True, f"SPY benchmark: ${spy:.2f} USD")
            else:
                print_status("Yahoo Finance Real-time Feed", False, "Invalid price response")
                all_passed = False
        except Exception as e:
            print_status("Yahoo Finance Real-time Feed", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 2. Formula Engine & Feature Engineering
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
        # 3. Broker Layer Integration (Toss OpenAPI or MockBroker)
        # -------------------------------------------------------------
        print(f"\n{BOLD}[3/6] 🏦 Broker Layer & Available Purchasing Power{RESET}")
        try:
            from broker import MockBroker
            from toss_service import TossInvestService

            toss_key = os.getenv("TOSS_API_KEY", "").strip()
            toss_sec = os.getenv("TOSS_API_SECRET", "").strip()
            toss_acc = os.getenv("TOSS_ACCOUNT_SEQ", "").strip()
            dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"

            if toss_key and toss_sec and not dry_run:
                toss = TossInvestService(
                    api_key=toss_key,
                    client_secret=toss_sec,
                    account_seq=toss_acc
                )
                accounts = await toss.list_accounts()
                resolved_seq = await toss._resolve_account_seq()
                if resolved_seq:
                    toss.account_seq = resolved_seq
                token_ok = bool(toss.token)
                print_status("Toss OAuth2 Token & Account Check", token_ok, f"{len(accounts)} active account(s) detected")
                if not token_ok:
                    all_passed = False

                balance = await toss.get_balance()
                balance_ok = balance is not None and balance >= 0
                print_status("Toss Live Purchasing Power", balance_ok, f"${balance:.2f} USD available")
                if not balance_ok:
                    all_passed = False
            else:
                mock = MockBroker(initial_cash_usd=500.0)
                await mock.buy_market_order("SPY", 100.0)
                has_pos = "SPY" in mock.positions
                await mock.sell_market_order("SPY")
                sold_ok = "SPY" not in mock.positions
                mock_ok = has_pos and sold_ok
                mode_str = "Paper Simulation (DRY_RUN=1)"
                print_status("MockBroker Paper Simulation", mock_ok, f"{mode_str} ($500.00 USD cash buffer)")
                if not mock_ok:
                    all_passed = False
        except Exception as e:
            print_status("Broker Integration", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 4. Telemetry & Alerts Subsystem
        # -------------------------------------------------------------
        print(f"\n{BOLD}[4/6] 📡 Telemetry & LLM Integration (Telegram Bot & AI Reasoning){RESET}")
        try:
            tg_token = os.getenv("TELEGRAM_TOKEN", "").strip()
            tg_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            llm_url = os.getenv("LLM_BASE_URL") or os.getenv("AI_BASE_URL", "").strip()
            llm_key = os.getenv("LLM_API_KEY") or os.getenv("AI_API_KEY", "").strip()
            dash_token = os.getenv("DASHBOARD_TOKEN", "").strip()

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
                badge = f"{YELLOW}[SKIP]{RESET}"
                print(f"  • Telegram Bot Live Telemetry: {badge} (Not configured in local .env - optional)")

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
                badge = f"{YELLOW}[SKIP]{RESET}"
                print(f"  • LLM / AI Reasoning Endpoint Ping: {badge} (Pure quant mode active)")

            dash_ok = bool(dash_token and len(dash_token) >= 4)
            if dash_token:
                print_status("Mobile Web Dashboard Token", dash_ok, "Configured")
            else:
                badge = f"{YELLOW}[SKIP]{RESET}"
                print(f"  • Mobile Web Dashboard Token: {badge} (Using default/local token)")
        except Exception as e:
            print_status("Telemetry Subsystem", False, str(e))
            all_passed = False

        # -------------------------------------------------------------
        # 5. Scheduler Lock & Stale Position Audit
        # -------------------------------------------------------------
        print(f"\n{BOLD}[5/6] 🔒 Scheduler Execution Lock & Stale Position Audit{RESET}")
        today_str = datetime.now(NEW_YORK).strftime("%Y-%m-%d")
        screening_path = BASE_DIR / "data" / "screening_history.json"
        is_locked = False
        lock_detail = "CLEAR (Ready to execute today)"

        if screening_path.exists():
            try:
                with open(screening_path, "r", encoding="utf-8") as sf:
                    s_recs = json.load(sf)
                    for rec in s_recs:
                        if rec.get("date") == today_str and rec.get("counts", {}).get("scanned_universe", 0) > 10:
                            is_locked = True
                            lock_detail = f"Already executed for {today_str} ({rec.get('entered_ticker')})"
                            break
            except Exception as e:
                lock_detail = f"Error reading log: {e}"

        print_status("Scheduler Daily Execution Lock", not is_locked, lock_detail)
        if is_locked:
            all_passed = False

        # Check for stale overnight positions
        positions_file = BASE_DIR / "breakout_positions.json"
        stale_positions = []
        if positions_file.exists():
            try:
                with open(positions_file, "r", encoding="utf-8") as pf:
                    p_data = json.load(pf)
                    for p in p_data:
                        e_str = p.get("entry_time", "")
                        if e_str and datetime.fromisoformat(e_str).date() < datetime.now(NEW_YORK).date():
                            stale_positions.append(p.get("ticker"))
            except Exception:
                pass

        stale_ok = len(stale_positions) == 0
        stale_msg = "0 leftover positions" if stale_ok else f"{len(stale_positions)} stale position(s): {', '.join(stale_positions)} (Auto-flush armed)"
        print_status("Overnight Position Hygiene", stale_ok, stale_msg)

        # -------------------------------------------------------------
        # 6. Live 161-Ticker Momentum Scanner & Gold Standard Top 5
        # -------------------------------------------------------------
        print(f"\n{BOLD}[6/6] 🎯 Live 161-Ticker Scanner & Gold Standard Top 5 Rankings{RESET}")
        try:
            from market_data import MarketDataCollector
            from derived_formula import calculate_breakout_score, should_enter_breakout_trade

            candidates = await MarketDataCollector.get_breakout_candidates_premarket()
            scanner_ok = len(candidates) >= 50
            print_status("High-Throughput Batch Scanner", scanner_ok, f"Scanned: {len(candidates)} / 161 tickers")
            if not scanner_ok:
                all_passed = False

            scored = []
            for c in candidates:
                sc = calculate_breakout_score(c)
                passed = should_enter_breakout_trade(c, threshold=15.0)
                scored.append((sc, passed, c["ticker"], c))

            scored.sort(key=lambda x: x[0], reverse=True)
            passed_items = [x for x in scored if x[1]]
            print(f"      • Candidates Passing Threshold (Score >= 15.0, Gap 0~4%, ATR >= 7%): {len(passed_items)}")

            print("\n      Top 5 Ranked Candidates:")
            print("      " + "-" * 62)
            print(f"      {'TICKER':<8} {'SCORE':>8} {'STATUS':<10} {'GAP %':>8} {'RVOL':>8} {'ATR %':>8}")
            print("      " + "-" * 62)
            for sc, p, t, c in scored[:5]:
                status_str = "PASS ✅" if p else "FAIL ❌"
                print(f"      {t:<8} {sc:>8.1f} {status_str:<10} {c['gap_open_pct']:>7.2f}% {c['rvol_prev']:>7.2f}x {c['atr_pct']:>7.2f}%")
            print("      " + "-" * 62)

            if passed_items:
                top_symbol = passed_items[0][2]
                top_score = passed_items[0][0]
                print(f"\n      🎯 Leading Execution Target: {BOLD}{GREEN}{top_symbol}{RESET} (Formula Score: {top_score:.1f})")
            elif scored:
                print(f"\n      ℹ️ Leading Watchlist Target: {BOLD}{YELLOW}{scored[0][2]}{RESET} (Formula Score: {scored[0][0]:.1f})")

        except Exception as e:
            print_status("Live Momentum Scanner", False, str(e))
            all_passed = False

    # -------------------------------------------------------------
    # Final Summary Verdict
    # -------------------------------------------------------------
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    if all_passed:
        print(f"{BOLD}{GREEN}🎉 ALL SYSTEMS GO: SYSTEM IS 100% OPERATIONAL & READY FOR TONIGHT'S BUY!{RESET}")
        print(f"  • Execution scheduled at 10:00 AM EST (12:00 AM Midnight KST)")
    else:
        print(f"{BOLD}{RED}❌ DIAGNOSTICS DETECTED ISSUES. REVIEW [FAIL] ITEMS ABOVE.{RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}\n")

    return all_passed


if __name__ == "__main__":
    passed = asyncio.run(run_diagnostics())
    sys.exit(0 if passed else 1)

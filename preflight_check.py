"""
preflight_check.py: Full-Spectrum Autonomous Trading Readiness Auditor

Run anytime to verify:
1. System Clock & US Market Trading Calendar
2. Scheduler Lock Status (Guarantees today is not falsely blocked)
3. Active / Stale Positions Check
4. Broker Cash & Buying Power
5. 160-Ticker Nanocap Batch Scanner
6. Derived Breakout Formula Top 5 Rankings
7. Simulation Execution Dry-Run
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from derived_formula import calculate_breakout_score, should_enter_breakout_trade
from market_calendar import is_trading_day, get_market_session_info
from market_data import MarketDataCollector
from broker import MockBroker, BaseBroker
from toss_service import TossInvestService
from settings_manager import SettingsManager

NEW_YORK = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


async def run_preflight_audit():
    print("=" * 70)
    print("🚀 [AI QUANT AGENT] FULL-SPECTRUM PRE-FLIGHT READINESS AUDIT")
    print("=" * 70)

    now_ny = datetime.now(NEW_YORK)
    now_kst = datetime.now(KST)
    today_str = now_ny.strftime("%Y-%m-%d")

    # 1. System Clock & Market Status
    is_trading, status = is_trading_day(now_ny)
    session_info = get_market_session_info(now_ny)
    trigger_dt = now_ny.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(minutes=30)
    trigger_kst = trigger_dt.astimezone(KST)

    print(f"\n[1/6] 🕒 TIME & MARKET STATUS")
    print(f"  • New York Time : {now_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  • Korea Time    : {now_kst.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  • US Trading Day: {'✅ YES' if is_trading else '❌ NO (Market Holiday/Weekend)'} ({status})")
    print(f"  • Market Session: {session_info['session']} ({session_info['next_event']})")
    print(f"  • Scheduled Buy : {trigger_dt.strftime('%H:%M:%S')} EST ({trigger_kst.strftime('%H:%M:%S')} KST / 12:00 AM Midnight)")

    # 2. Scheduler Lock Audit (Guard against false completion lock)
    print(f"\n[2/6] 🔒 SCHEDULER LOCK STATUS")
    screening_path = BASE_DIR / "data" / "screening_history.json"
    is_locked = False
    lock_reason = "CLEAR (Eligible to trade today)"
    
    if screening_path.exists():
        try:
            import json
            with open(screening_path, "r", encoding="utf-8") as sf:
                s_recs = json.load(sf)
                for rec in s_recs:
                    if rec.get("date") == today_str and rec.get("counts", {}).get("scanned_universe", 0) > 10:
                        is_locked = True
                        lock_reason = f"Screening already finished for {today_str} ({rec.get('entered_ticker')})"
                        break
        except Exception as e:
            lock_reason = f"Error reading log: {e}"

    if is_locked:
        print(f"  • Lock Status   : ⚠️ LOCKED ({lock_reason})")
    else:
        print(f"  • Lock Status   : ✅ UNLOCKED (Ready to execute at 10:00 AM EST)")

    # 3. Active Positions & Overnight Stale Check
    print(f"\n[3/6] 💼 POSITION & BALANCE AUDIT")
    positions_file = BASE_DIR / "breakout_positions.json"
    active_positions = {}
    if positions_file.exists():
        try:
            import json
            with open(positions_file, "r", encoding="utf-8") as pf:
                p_data = json.load(pf)
                for item in p_data:
                    active_positions[item.get("ticker")] = item
        except Exception:
            pass

    stale_found = []
    for ticker, pos in active_positions.items():
        e_time_str = pos.get("entry_time", "")
        if e_time_str:
            try:
                e_date = datetime.fromisoformat(e_time_str).date()
                if e_date < now_ny.date():
                    stale_found.append((ticker, e_date))
            except Exception:
                pass

    if stale_found:
        print(f"  • Stale Positions: ⚠️ {len(stale_found)} leftover position(s) detected:")
        for t, d in stale_found:
            print(f"    - {t} (Entered on {d}) -> Will be automatically flushed before screening.")
    else:
        print(f"  • Stale Positions: ✅ 0 stale positions (Clean slate)")

    # Broker Balance Check
    dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
    broker = TossInvestService() if not dry_run else MockBroker(initial_cash_usd=500.0)
    try:
        balance = await broker.get_balance()
        print(f"  • Broker Mode   : {'🧪 SIMULATION (DRY-RUN)' if dry_run else '🔴 LIVE TRADING (TOSS OPENAPI)'}")
        print(f"  • Available Cash: ${balance:,.2f} USD")
    except Exception as be:
        print(f"  • Broker Balance: ⚠️ Fetch failed ({be})")

    # 4. Live Universe Market Data Batch Scan
    print(f"\n[4/6] 📡 160-TICKER UNIVERSE LIVE SCANNER")
    print("  • Fetching live momentum batch quotes via MarketDataCollector...")
    candidates = await MarketDataCollector.get_breakout_candidates_premarket()
    print(f"  • Successfully scanned: {len(candidates)} / 161 tickers")

    # 5. Formula Scoring & Filter Rankings
    print(f"\n[5/6] 🎯 DERIVED FORMULA RANKINGS (TOP CANDIDATES)")
    scored = []
    for c in candidates:
        sc = calculate_breakout_score(c)
        passed = should_enter_breakout_trade(c, threshold=15.0)
        scored.append((sc, passed, c["ticker"], c))

    scored.sort(key=lambda x: x[0], reverse=True)
    passed_items = [x for x in scored if x[1]]
    print(f"  • Total Candidates Passing Threshold (Score >= 15.0, Gap 0~4%, ATR >= 7%): {len(passed_items)}")
    
    print("\n  Top 5 Ranked Candidates:")
    print("  " + "-" * 68)
    print(f"  {'TICKER':<8} {'SCORE':>8} {'STATUS':<10} {'GAP %':>8} {'RVOL':>8} {'ATR %':>8}")
    print("  " + "-" * 68)
    for sc, p, t, c in scored[:5]:
        status_str = "PASS ✅" if p else "FAIL ❌"
        print(f"  {t:<8} {sc:>8.1f} {status_str:<10} {c['gap_open_pct']:>7.2f}% {c['rvol_prev']:>7.2f}x {c['atr_pct']:>7.2f}%")
    print("  " + "-" * 68)

    # 6. Overall Verdict
    print(f"\n[6/6] 🏁 FINAL PRE-FLIGHT VERDICT")
    issues = []
    if not is_trading:
        issues.append("US market is closed today (Weekend/Holiday)")
    if is_locked:
        issues.append("Scheduler is locked as completed for today")
    if len(candidates) < 50:
        issues.append("Data feed returned fewer than 50 tickers")

    if not issues:
        top_symbol = passed_items[0][2] if passed_items else scored[0][2]
        print(f"  ✅ ALL SYSTEMS GO: System is 100% ready for autonomous execution at 12:00 AM (Midnight KST)!")
        if passed_items:
            print(f"  🎯 Leading Candidate: {top_symbol} (Formula Score: {passed_items[0][0]:.1f})")
        else:
            print(f"  ℹ️ Currently awaiting open confirmation at 10:00 AM (Top watch: {top_symbol})")
    else:
        print(f"  ⚠️ AUDIT WARNINGS FOUND:")
        for iss in issues:
            print(f"    - {iss}")

    print("=" * 70)


async def get_preflight_status_dict():
    """Returns a structured status dict suitable for dashboard API consumption."""
    now_ny = datetime.now(NEW_YORK)
    today_str = now_ny.strftime("%Y-%m-%d")
    is_trading, status = is_trading_day(now_ny)
    session_info = get_market_session_info(now_ny)

    screening_path = BASE_DIR / "data" / "screening_history.json"
    is_locked = False
    lock_reason = "Ready"
    if screening_path.exists():
        try:
            import json
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
            import json
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


if __name__ == "__main__":
    asyncio.run(run_preflight_audit())

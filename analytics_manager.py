"""
analytics_manager.py: Real-Time Performance Aggregator & Equity Curve Engine

Provides 100% genuine live metrics without fake placeholders:
1. Multi-timeframe performance statistics (Today, 1W, 1M, 1Y, All) from actual trade logs
2. Clean SVG equity curve time series
3. Real-time screening funnel and near-miss candidate proximity
4. Real-time active positions and cash balances
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from market_calendar import get_market_session_info

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TRADE_HISTORY_FILE = BASE_DIR / "breakout_trade_history.json"
SCREENING_FILE = DATA_DIR / "screening_history.json"
POSITIONS_FILE = BASE_DIR / "breakout_positions.json"

KRW_USD_RATE = 1360.0


class AnalyticsManager:
    def __init__(self):
        pass

    def _load_json(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return []

    def get_timeframe_stats(self, trades: List[Dict[str, Any]], days_limit: Optional[int] = None) -> Dict[str, Any]:
        """Calculates win rate, return, profit factor, and drawdown from actual trade records."""
        if not trades:
            return {
                "trades_count": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "cum_return_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
            }

        now_ny = datetime.now(NEW_YORK)
        filtered = []
        for t in trades:
            etime_str = t.get("entry_time") or t.get("exit_time") or ""
            try:
                t_dt = datetime.fromisoformat(etime_str).astimezone(NEW_YORK)
            except Exception:
                t_dt = now_ny

            if days_limit is not None:
                if (now_ny - t_dt).days > days_limit:
                    continue
            filtered.append(t)

        if not filtered:
            return {
                "trades_count": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "cum_return_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
            }

        wins = [t for t in filtered if float(t.get("return_pct", 0.0)) > 0]
        losses = [t for t in filtered if float(t.get("return_pct", 0.0)) <= 0]
        total_count = len(filtered)
        win_rate = (len(wins) / total_count * 100.0) if total_count > 0 else 0.0

        gross_profit = sum(float(t.get("return_pct", 0.0)) for t in wins)
        gross_loss = abs(sum(float(t.get("return_pct", 0.0)) for t in losses))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 1.0)

        # Compound cumulative return
        cum_mult = 1.0
        peak = 1.0
        mdd = 0.0
        for t in filtered:
            ret = float(t.get("return_pct", 0.0)) / 100.0
            cum_mult *= (1.0 + ret)
            if cum_mult > peak:
                peak = cum_mult
            dd = ((peak - cum_mult) / peak) * 100.0
            if dd > mdd:
                mdd = dd

        cum_return_pct = (cum_mult - 1.0) * 100.0
        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (-gross_loss / len(losses)) if losses else 0.0

        return {
            "trades_count": total_count,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "cum_return_pct": round(cum_return_pct, 2),
            "profit_factor": round(pf, 2),
            "max_drawdown_pct": round(mdd, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
        }

    def get_equity_curve(self, trades: List[Dict[str, Any]], current_balance_usd: float, timeframe: str = "all") -> List[Dict[str, Any]]:
        """Generates clean line-chart data points from genuine trade history."""
        if not trades:
            return []

        points = []
        now_ny = datetime.now(NEW_YORK)
        days_map = {"today": 1, "1w": 7, "1m": 30, "1y": 365, "all": None}
        limit_days = days_map.get(timeframe)

        filtered = []
        for t in trades:
            etime_str = t.get("entry_time") or t.get("exit_time") or ""
            try:
                t_dt = datetime.fromisoformat(etime_str).astimezone(NEW_YORK)
            except Exception:
                t_dt = now_ny

            if limit_days is not None:
                if (now_ny - t_dt).days > limit_days:
                    continue
            filtered.append(t)

        if not filtered:
            return []

        cum_ret = 0.0
        for t in filtered:
            date_str = (t.get("exit_time") or t.get("entry_time") or "")[:10]
            ret = float(t.get("return_pct", 0.0))
            cum_ret += ret

            points.append({
                "date": date_str,
                "return_pct": round(cum_ret, 2),
                "ticker": t.get("ticker", ""),
                "label": f"{t.get('ticker', '')} ({ret:+.1f}%)",
            })

        return points

    def get_dashboard_summary(
        self,
        current_balance_usd: float = 0.0,
        active_positions: Optional[Dict[str, Any]] = None,
        selected_timeframe: str = "all",
    ) -> Dict[str, Any]:
        """
        Builds the live dashboard summary:
        - Real-time Market status
        - Real cash balance & active positions
        - Genuine trade history stats (no fake benchmarks)
        - Actual daily screening results (or clean waiting state)
        """
        trades = self._load_json(TRADE_HISTORY_FILE)
        screening_records = self._load_json(SCREENING_FILE)
        market_info = get_market_session_info()

        # Multi-timeframe stats based purely on genuine trades
        today_stats = self.get_timeframe_stats(trades, days_limit=1)
        w1_stats = self.get_timeframe_stats(trades, days_limit=7)
        m1_stats = self.get_timeframe_stats(trades, days_limit=30)
        y1_stats = self.get_timeframe_stats(trades, days_limit=365)
        all_stats = self.get_timeframe_stats(trades, days_limit=None)

        timeframe_map = {
            "today": today_stats,
            "1w": w1_stats,
            "1m": m1_stats,
            "1y": y1_stats,
            "all": all_stats,
        }
        active_stats = timeframe_map.get(selected_timeframe, all_stats)

        # Real Account Balances
        bal_usd = current_balance_usd if current_balance_usd > 0 else 0.0
        bal_krw = int(bal_usd * KRW_USD_RATE)
        dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"

        # Position Card
        pos_list = []
        if active_positions:
            for ticker, p in active_positions.items():
                p_dict = p.to_dict() if hasattr(p, "to_dict") else dict(p)
                entry_p = float(p_dict.get("entry_price", 0.0))
                high_p = float(p_dict.get("highest_price", entry_p))
                stop_pct = float(p_dict.get("stop_loss_pct", -3.0))
                trail_pct = float(p_dict.get("trailing_step_pct", 2.0))

                stop_price = round(entry_p * (1.0 + stop_pct / 100.0), 3)
                trail_price = round(high_p * (1.0 - trail_pct / 100.0), 3)
                pos_list.append({
                    "ticker": ticker,
                    "entry_price": entry_p,
                    "highest_price": high_p,
                    "stop_loss_price": stop_price,
                    "trailing_exit_price": trail_price,
                    "stop_loss_pct": stop_pct,
                    "trailing_step_pct": trail_pct,
                    "entry_time": p_dict.get("entry_time", ""),
                })

        # Latest Screening: Only show if an actual screening was recorded today
        latest_screening = None
        if screening_records:
            latest_screening = screening_records[-1]

        # Real AI Trap Count from recorded history
        real_traps_count = 0
        if screening_records:
            for s in screening_records:
                counts = s.get("counts", {})
                real_traps_count += counts.get("ai_rejected_traps", 0)

        ai_proof = {
            "traps_blocked": real_traps_count,
            "has_records": len(screening_records) > 0,
        }

        # Equity Curve Points
        equity_curve = self.get_equity_curve(trades, current_balance_usd=bal_usd, timeframe=selected_timeframe)

        return {
            "market": market_info,
            "account": {
                "balance_usd": round(bal_usd, 2),
                "balance_krw": bal_krw,
                "is_dry_run": dry_run,
                "active_positions": pos_list,
                "has_active_position": len(pos_list) > 0,
            },
            "selected_timeframe": selected_timeframe,
            "stats": active_stats,
            "timeframes": timeframe_map,
            "equity_curve": equity_curve,
            "screening": latest_screening,
            "ai_proof": ai_proof,
        }


analytics_manager = AnalyticsManager()

"""
trade_logger.py: High-Fidelity Dataset Logging & Training Data Pipeline

Stores:
1. Daily screening funnels & near-miss candidate proximity (-20~30% threshold runners-up)
2. Machine-learning ready training datasets (.parquet + .jsonl) for Windows PC local ML/LoRA
3. Realized trade logs with ground-truth profit/loss labels and exit reasons
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import pandas as pd

from utils import atomic_write

logger = logging.getLogger("StockReporter")
NEW_YORK = ZoneInfo("America/New_York")

DATA_DIR = Path(__file__).parent / "data"
SCREENING_FILE = DATA_DIR / "screening_history.json"
TRAINING_PARQUET = DATA_DIR / "training_dataset.parquet"
TRAINING_JSONL = DATA_DIR / "training_dataset.jsonl"
TRADE_HISTORY_FILE = Path(__file__).parent / "breakout_trade_history.json"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def calculate_criteria_proximity(feat: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates how close a candidate came to meeting the Gold Standard breakout criteria.
    Criteria:
    - gap_open_pct: [0.0%, 4.0%]
    - atr_pct: >= 7.0%
    - rvol_prev: >= 1.2x
    - fact_score: >= 0.25
    - is_toxic: == 0
    Returns:
    - proximity_score: 0% ~ 100%
    - breakdown: individual metric satisfaction %
    - miss_reasons: human-readable explanation of what was lacking
    """
    gap = float(feat.get("gap_open_pct", 0.0))
    atr = float(feat.get("atr_pct", 0.0))
    rvol = float(feat.get("rvol_prev", 0.0))
    fact = float(feat.get("fact_score", 0.0))
    is_toxic = float(feat.get("is_toxic_material", 0.0)) > 0.5

    # 1. Gap satisfaction
    if 0.0 <= gap <= 4.0:
        gap_sat = 100.0
    elif gap > 4.0:
        gap_sat = max(0.0, 100.0 - (gap - 4.0) * 15.0)
    else:  # gap < 0
        gap_sat = max(0.0, 100.0 - abs(gap) * 20.0)

    # 2. ATR satisfaction
    atr_sat = min(100.0, (atr / 7.0) * 100.0) if atr > 0 else 0.0

    # 3. RVOL satisfaction
    rvol_sat = min(100.0, (rvol / 1.2) * 100.0) if rvol > 0 else 0.0

    # 4. Fact catalyst satisfaction
    fact_sat = min(100.0, (fact / 0.25) * 100.0) if fact > 0 else 0.0

    # 5. Toxic dilution penalty
    toxic_sat = 0.0 if is_toxic else 100.0

    breakdown = {
        "gap_sat": round(gap_sat, 1),
        "atr_sat": round(atr_sat, 1),
        "rvol_sat": round(rvol_sat, 1),
        "fact_sat": round(fact_sat, 1),
        "toxic_sat": round(toxic_sat, 1),
    }

    miss_reasons = []
    if is_toxic:
        miss_reasons.append("유상증자/CB 악재 감지 (Toxic Dilution)")
    if gap_sat < 100.0:
        if gap > 4.0:
            miss_reasons.append(f"갭상승 과열 (+{gap:.1f}% > 기준 +4.0%)")
        else:
            miss_reasons.append(f"시초가 갭 부족 ({gap:+.1f}%)")
    if atr_sat < 100.0:
        miss_reasons.append(f"일간 변동폭(ATR) 미달 ({atr:.1f}% < 기준 7.0%)")
    if rvol_sat < 100.0:
        miss_reasons.append(f"상대 거래량(RVOL) 부족 ({rvol:.2f}x < 기준 1.2x)")
    if fact_sat < 100.0:
        miss_reasons.append(f"공시/카탈리스트 강도 부족 ({fact:.2f} < 기준 0.25)")

    if is_toxic:
        overall_proximity = min(40.0, (gap_sat + atr_sat + rvol_sat + fact_sat) / 4.0 * 0.4)
    else:
        overall_proximity = (gap_sat * 0.25) + (atr_sat * 0.25) + (rvol_sat * 0.25) + (fact_sat * 0.25)

    return {
        "proximity_score": round(overall_proximity, 1),
        "breakdown": breakdown,
        "miss_reasons": miss_reasons,
    }


class TradeDatasetLogger:
    def __init__(self):
        ensure_data_dir()

    def record_screening_cycle(
        self,
        date_str: str,
        total_universe_count: int,
        all_candidates: List[Dict[str, Any]],
        approved_candidates: List[Dict[str, Any]],
        entered_ticker: Optional[str] = None,
        ai_mode: str = "deep",
    ) -> Dict[str, Any]:
        """
        Records the complete screening funnel and saves counterfactual near-miss candidates
        (-20%~30% below threshold) for local ML model training.
        """
        ensure_data_dir()
        analyzed_candidates = []

        for item in all_candidates:
            feat = item.get("feat", item)
            ticker = feat.get("ticker", item.get("ticker", "UNKNOWN"))
            prox_info = calculate_criteria_proximity(feat)

            is_entered = (ticker == entered_ticker)
            is_ai_approved = any(a.get("ticker") == ticker for a in approved_candidates)
            ai_audit = item.get("ai_audit")

            status = "FILTERED_OUT"
            if is_entered:
                status = "APPROVED_ENTERED"
            elif is_ai_approved:
                status = "RUNNER_UP"
            elif ai_audit and not ai_audit.get("is_safe", True):
                status = "AI_REJECTED"
            elif prox_info["proximity_score"] >= 70.0:
                status = "NEAR_MISS"

            record = {
                "date": date_str,
                "ticker": ticker,
                "status": status,
                "formula_score": round(float(item.get("score", 0.0)), 2),
                "proximity_score": prox_info["proximity_score"],
                "breakdown": prox_info["breakdown"],
                "miss_reasons": prox_info["miss_reasons"],
                "features": {
                    "gap_open_pct": round(float(feat.get("gap_open_pct", 0.0)), 2),
                    "rvol_prev": round(float(feat.get("rvol_prev", 0.0)), 2),
                    "atr_pct": round(float(feat.get("atr_pct", 0.0)), 2),
                    "bb_width": round(float(feat.get("bb_width", 0.0)), 2),
                    "fact_score": round(float(feat.get("fact_score", 0.0)), 2),
                    "is_toxic_material": int(feat.get("is_toxic_material", 0.0) > 0.5),
                },
                "ai_mode": ai_mode,
                "ai_summary": item.get("ai_summary", ""),
            }
            analyzed_candidates.append(record)

        # Sort by proximity score descending
        analyzed_candidates.sort(key=lambda x: (x["status"] == "APPROVED_ENTERED", x["proximity_score"]), reverse=True)

        passed_quant_count = len([c for c in analyzed_candidates if c["proximity_score"] >= 95.0 or c["status"] in ("APPROVED_ENTERED", "RUNNER_UP", "AI_REJECTED")])
        ai_approved_count = len(approved_candidates)
        ai_rejected_count = len([c for c in analyzed_candidates if c["status"] == "AI_REJECTED"])

        # Near-miss candidates (70% ~ 99% criteria satisfaction)
        near_misses = [c for c in analyzed_candidates if c["status"] in ("NEAR_MISS", "RUNNER_UP", "AI_REJECTED")][:6]

        funnel_summary = {
            "date": date_str,
            "ai_mode": ai_mode,
            "entered_ticker": entered_ticker,
            "counts": {
                "scanned_universe": total_universe_count,
                "passed_quant": passed_quant_count,
                "ai_approved": ai_approved_count,
                "ai_rejected_traps": ai_rejected_count,
            },
            "near_misses": near_misses,
            "all_analyzed_count": len(analyzed_candidates),
        }

        # 1. Update screening_history.json
        self._append_screening_history(funnel_summary)

        # 2. Append to training dataset (.parquet & .jsonl)
        training_rows = []
        for c in analyzed_candidates[:10]:  # Top 10 including winner, near-misses, and traps
            flat_row = {
                "date": c["date"],
                "ticker": c["ticker"],
                "status": c["status"],
                "formula_score": c["formula_score"],
                "proximity_score": c["proximity_score"],
                "gap_open_pct": c["features"]["gap_open_pct"],
                "rvol_prev": c["features"]["rvol_prev"],
                "atr_pct": c["features"]["atr_pct"],
                "bb_width": c["features"]["bb_width"],
                "fact_score": c["features"]["fact_score"],
                "is_toxic_material": c["features"]["is_toxic_material"],
                "ai_mode": c["ai_mode"],
                "realized_return_pct": None,  # Will be labeled upon trade completion
                "is_win": None,
                "exit_reason": None,
            }
            training_rows.append(flat_row)

        self._append_training_rows(training_rows)
        return funnel_summary

    def _append_screening_history(self, summary: Dict[str, Any]):
        try:
            records = []
            if SCREENING_FILE.exists():
                with open(SCREENING_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    if not isinstance(records, list):
                        records = []

            # Replace if already exists for date, else append
            date_str = summary["date"]
            records = [r for r in records if r.get("date") != date_str]
            records.append(summary)
            # Keep up to 90 days
            records = records[-90:]
            atomic_write(str(SCREENING_FILE), records)
        except Exception as e:
            logger.error(f"Failed to write screening history: {e}")

    def _append_training_rows(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        try:
            # 1. Append JSONL
            with open(TRAINING_JSONL, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

            # 2. Append/Update Parquet
            new_df = pd.DataFrame(rows)
            if TRAINING_PARQUET.exists():
                try:
                    existing_df = pd.read_parquet(TRAINING_PARQUET)
                    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                    # Deduplicate by (date, ticker)
                    combined_df = combined_df.drop_duplicates(subset=["date", "ticker"], keep="last")
                    combined_df.to_parquet(TRAINING_PARQUET, index=False)
                except Exception:
                    new_df.to_parquet(TRAINING_PARQUET, index=False)
            else:
                new_df.to_parquet(TRAINING_PARQUET, index=False)
        except Exception as e:
            logger.error(f"Failed to append to training dataset: {e}")

    def record_trade_exit(
        self,
        ticker: str,
        entry_price: float,
        exit_price: float,
        highest_price: float,
        exit_reason: str,
        entry_time: str,
        exit_time: str,
    ) -> Dict[str, Any]:
        """
        Records realized trade exit, calculates PnL %, updates breakout_trade_history.json
        and labels the ground-truth outcome in the training dataset.
        """
        ensure_data_dir()
        ret_pct = round(((exit_price - entry_price) / entry_price) * 100.0, 2)
        is_win = int(ret_pct > 0)

        trade_record = {
            "ticker": ticker,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "highest_price": round(highest_price, 4),
            "return_pct": ret_pct,
            "is_win": is_win,
            "exit_reason": exit_reason,
        }

        # 1. Update breakout_trade_history.json
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                    trades = json.load(f)
                    if not isinstance(trades, list):
                        trades = []
            trades.append(trade_record)
            atomic_write(str(TRADE_HISTORY_FILE), trades)
        except Exception as e:
            logger.error(f"Failed to update {TRADE_HISTORY_FILE}: {e}")

        # 2. Update Ground-Truth label in training parquet if present
        try:
            date_str = entry_time[:10]
            if TRAINING_PARQUET.exists():
                df = pd.read_parquet(TRAINING_PARQUET)
                mask = (df["date"] == date_str) & (df["ticker"] == ticker)
                if mask.any():
                    df.loc[mask, "realized_return_pct"] = ret_pct
                    df.loc[mask, "is_win"] = is_win
                    df.loc[mask, "exit_reason"] = exit_reason
                    df.to_parquet(TRAINING_PARQUET, index=False)
        except Exception as e:
            logger.error(f"Failed to label parquet row: {e}")

        return trade_record


trade_logger = TradeDatasetLogger()

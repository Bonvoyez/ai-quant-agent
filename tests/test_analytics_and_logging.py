"""
Unit tests for Analytics, Trade Logging, and Real-Time Market Session.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import pytest

from market_calendar import get_market_session_info
from trade_logger import calculate_criteria_proximity, TradeDatasetLogger
from analytics_manager import analytics_manager
from dashboard_server import create_dashboard_app
from aiohttp import web

NEW_YORK = ZoneInfo("America/New_York")


def test_criteria_proximity_calculation():
    # 1. Perfect candidate
    feat_perfect = {
        "gap_open_pct": 2.0,
        "atr_pct": 8.5,
        "rvol_prev": 1.8,
        "fact_score": 0.35,
        "is_toxic_material": 0,
    }
    prox = calculate_criteria_proximity(feat_perfect)
    assert prox["proximity_score"] == 100.0
    assert len(prox["miss_reasons"]) == 0

    # 2. Near-miss candidate (RVOL slightly low)
    feat_near_miss = {
        "gap_open_pct": 2.0,
        "atr_pct": 7.0,
        "rvol_prev": 0.96,  # 80% of 1.2
        "fact_score": 0.25,
        "is_toxic_material": 0,
    }
    prox_near = calculate_criteria_proximity(feat_near_miss)
    assert 90.0 <= prox_near["proximity_score"] < 100.0
    assert any("RVOL" in r for r in prox_near["miss_reasons"])

    # 3. Toxic Dilution candidate (Must be penalized severely)
    feat_toxic = {
        "gap_open_pct": 2.0,
        "atr_pct": 10.0,
        "rvol_prev": 2.5,
        "fact_score": 0.40,
        "is_toxic_material": 1,
    }
    prox_toxic = calculate_criteria_proximity(feat_toxic)
    assert prox_toxic["proximity_score"] <= 40.0
    assert any("유상증자" in r for r in prox_toxic["miss_reasons"])


def test_trade_logger_pipeline(tmp_path, monkeypatch):
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("trade_logger.DATA_DIR", test_data_dir)
    monkeypatch.setattr("trade_logger.SCREENING_FILE", test_data_dir / "screening_history.json")
    monkeypatch.setattr("trade_logger.TRAINING_PARQUET", test_data_dir / "training_dataset.parquet")
    monkeypatch.setattr("trade_logger.TRAINING_JSONL", test_data_dir / "training_dataset.jsonl")
    monkeypatch.setattr("trade_logger.TRADE_HISTORY_FILE", test_data_dir / "breakout_trade_history.json")

    logger = TradeDatasetLogger()

    candidates = [
        {"ticker": "WINR", "score": 25.0, "feat": {"ticker": "WINR", "gap_open_pct": 2.0, "atr_pct": 8.0, "rvol_prev": 1.5, "fact_score": 0.3, "is_toxic_material": 0}},
        {"ticker": "MISS", "score": 12.0, "feat": {"ticker": "MISS", "gap_open_pct": 1.0, "atr_pct": 6.0, "rvol_prev": 1.0, "fact_score": 0.2, "is_toxic_material": 0}},
    ]

    summary = logger.record_screening_cycle(
        date_str="2026-11-17",
        total_universe_count=120,
        all_candidates=candidates,
        approved_candidates=[candidates[0]],
        entered_ticker="WINR",
        ai_mode="deep",
    )

    assert summary["counts"]["scanned_universe"] == 120
    assert summary["entered_ticker"] == "WINR"

    # Verify Parquet file was generated and is valid
    pq_path = test_data_dir / "training_dataset.parquet"
    assert pq_path.exists()
    df = pd.read_parquet(pq_path)
    assert len(df) == 2
    assert "WINR" in df["ticker"].values

    # Test trade exit logging & labeling
    exit_rec = logger.record_trade_exit(
        ticker="WINR",
        entry_price=10.0,
        exit_price=11.2,
        highest_price=11.5,
        exit_reason="Trailing Stop (-2% from peak)",
        entry_time="2026-11-17T09:30:00-05:00",
        exit_time="2026-11-17T11:45:00-05:00",
    )
    assert exit_rec["return_pct"] == 12.0
    assert exit_rec["is_win"] == 1

    # Verify Parquet row was updated with outcome label
    df_after = pd.read_parquet(pq_path)
    winr_row = df_after[df_after["ticker"] == "WINR"].iloc[0]
    assert winr_row["realized_return_pct"] == 12.0
    assert winr_row["is_win"] == 1


def test_market_session_info():
    # Regular market test: 11:00 AM NY
    reg_dt = datetime(2026, 11, 17, 11, 0, 0, tzinfo=NEW_YORK)
    info_reg = get_market_session_info(reg_dt)
    assert info_reg["session"] == "REGULAR"
    assert info_reg["is_regular_open"] is True
    assert "정규장" in info_reg["session_label"]
    assert "정규장 마감까지" in info_reg["next_event"]

    # Pre-market test: 06:30 AM NY
    pre_dt = datetime(2026, 11, 17, 6, 30, 0, tzinfo=NEW_YORK)
    info_pre = get_market_session_info(pre_dt)
    assert info_pre["session"] == "PRE_MARKET"
    assert info_pre["is_regular_open"] is False
    assert "정규장 개장까지" in info_pre["next_event"]

    # Weekend test: Saturday
    sat_dt = datetime(2026, 11, 21, 12, 0, 0, tzinfo=NEW_YORK)
    info_sat = get_market_session_info(sat_dt)
    assert info_sat["session"] == "WEEKEND"
    assert info_sat["is_regular_open"] is False


def test_analytics_manager_summary():
    summary = analytics_manager.get_dashboard_summary(
        current_balance_usd=1000.0,
        active_positions={},
        selected_timeframe="all",
    )
    assert "market" in summary
    assert "account" in summary
    assert summary["account"]["balance_usd"] == 1000.0
    assert summary["account"]["balance_krw"] == 1_360_000
    assert "stats" in summary
    assert "equity_curve" in summary
    assert "screening" in summary
    assert "ai_proof" in summary


@pytest.mark.asyncio
async def test_dashboard_summary_api(monkeypatch):
    monkeypatch.setattr("dashboard_server.DASHBOARD_TOKEN", "test_sec_token")
    app = create_dashboard_app()
    client = web.Application()

    from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    # Unauthorized request
    res_unauth = await client.get("/api/summary")
    assert res_unauth.status == 401

    # Authorized request
    res_auth = await client.get("/api/summary?token=test_sec_token&timeframe=all")
    assert res_auth.status == 200
    data = await res_auth.json()
    assert "market" in data
    assert "account" in data
    assert "equity_curve" in data
    assert "screening" in data

    await client.close()

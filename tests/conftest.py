import pytest
from pathlib import Path
import trade_logger
import breakout_strategy

@pytest.fixture(autouse=True)
def isolate_test_data(tmp_path, monkeypatch):
    """
    Guarantees that no unit test touches production JSON, Parquet, or state files.
    All data writes are redirected to an isolated per-test temporary directory.
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    
    test_screening = test_data_dir / "screening_history.json"
    test_parquet = test_data_dir / "training_dataset.parquet"
    test_jsonl = test_data_dir / "training_dataset.jsonl"
    test_trade_hist = test_data_dir / "breakout_trade_history.json"
    test_positions = str(test_data_dir / "breakout_positions.json")

    monkeypatch.setattr(trade_logger, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(trade_logger, "SCREENING_FILE", test_screening)
    monkeypatch.setattr(trade_logger, "TRAINING_PARQUET", test_parquet)
    monkeypatch.setattr(trade_logger, "TRAINING_JSONL", test_jsonl)
    monkeypatch.setattr(trade_logger, "TRADE_HISTORY_FILE", test_trade_hist)
    monkeypatch.setattr(breakout_strategy, "POSITIONS_FILE", test_positions)

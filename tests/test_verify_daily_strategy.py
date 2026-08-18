import json
import pytest
from datetime import datetime
from unittest.mock import Mock
import pandas as pd
import exchange_calendars as xcals

import verify_daily_strategy as vds


def test_domain_types():
    result = vds.CheckResult(
        rule_id="test_rule",
        title="Test Rule",
        severity="PASS",
        summary="All good",
        details=["detail 1"],
        metrics={"count": 1},
    )
    assert result.rule_id == "test_rule"
    assert result.severity == "PASS"

    m = vds.MarketData(
        close=pd.DataFrame({"2330": [100.0]}),
        open=pd.DataFrame({"2330": [99.0]}),
        high=pd.DataFrame({"2330": [101.0]}),
        low=pd.DataFrame({"2330": [98.0]}),
        volume=pd.DataFrame({"2330": [1000]}),
    )
    assert not m.close.empty


def test_is_trading_day_calendar():
    # 2026-08-19 is Wednesday (trading day)
    assert vds.is_trading_day("2026-08-19") is True
    # 2026-08-23 is Sunday (non-trading day)
    assert vds.is_trading_day("2026-08-23") is False
    # Test datetime object
    dt = datetime(2026, 8, 23, 17, 30)
    assert vds.is_trading_day(dt) is False


def test_load_paper_snapshot_remote_success():
    mock_fetcher = Mock(return_value=json.dumps({
        "capital": 100000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-19", "equity": 100000.0}],
        "daily_signals": [],
    }))

    data, source, warnings = vds.load_paper_snapshot(
        url="https://example.com/paper.json",
        local_path="/nonexistent/paper.json",
        fetcher=mock_fetcher,
    )
    assert source == "github_raw"
    assert data["capital"] == 100000.0
    assert len(warnings) == 0
    mock_fetcher.assert_called_once_with("https://example.com/paper.json")


def test_load_paper_snapshot_remote_fallback_local(tmp_path):
    local_file = tmp_path / "paper_equity.json"
    local_file.write_text(json.dumps({
        "capital": 50000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-18", "equity": 50000.0}],
        "daily_signals": [],
    }))

    mock_fetcher = Mock(side_effect=Exception("Connection timed out"))

    data, source, warnings = vds.load_paper_snapshot(
        url="https://example.com/paper.json",
        local_path=str(local_file),
        fetcher=mock_fetcher,
    )
    assert source == "local_file"
    assert data["capital"] == 50000.0
    assert len(warnings) > 0
    assert "local" in warnings[0].lower() or "fallback" in warnings[0].lower()


def test_load_paper_snapshot_both_fail():
    mock_fetcher = Mock(side_effect=Exception("Connection timed out"))
    with pytest.raises(Exception):
        vds.load_paper_snapshot(
            url="https://example.com/paper.json",
            local_path="/nonexistent/path/paper.json",
            fetcher=mock_fetcher,
        )


def test_validate_snapshot_valid():
    valid_data = {
        "capital": 20049.0,
        "positions": {
            "2330": {
                "entry": 1000.0,
                "tp": 1120.0,
                "sl": 910.0,
                "entry_date": "2026-08-10",
                "shares": 100,
                "day_count": 4,
            }
        },
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-14", "equity": 120049.0}],
        "daily_signals": [{"date": "2026-08-14", "tickers": ["2330"]}],
    }
    is_valid, snapshot_date, errors, warnings = vds.validate_snapshot(valid_data)
    assert is_valid is True
    assert snapshot_date == "2026-08-14"
    assert len(errors) == 0
    assert len(warnings) == 0


def test_validate_snapshot_missing_top_field():
    invalid_data = {
        "positions": {},
        "closed_trades": [],
        "equity_curve": [],
        "daily_signals": [],
    }
    is_valid, snapshot_date, errors, warnings = vds.validate_snapshot(invalid_data)
    assert is_valid is False
    assert any("capital" in e for e in errors)


def test_validate_snapshot_corrupted_position():
    data = {
        "capital": 10000.0,
        "positions": {
            "2330": {
                "entry": -10.0,
                "tp": 1120.0,
                "sl": 910.0,
                "entry_date": "2026-08-10",
                "shares": 100,
                "day_count": 4,
            }
        },
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-14", "equity": 10000.0}],
        "daily_signals": [],
    }
    is_valid, snapshot_date, errors, warnings = vds.validate_snapshot(data)
    assert is_valid is False
    assert any("2330" in e for e in errors)


def test_validate_snapshot_fallback_snapshot_date():
    data = {
        "capital": 10000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [],
        "daily_signals": [{"date": "2026-08-14", "tickers": ["2330"]}],
    }
    is_valid, snapshot_date, errors, warnings = vds.validate_snapshot(data)
    assert is_valid is True
    assert snapshot_date == "2026-08-14"
    assert len(warnings) > 0


def test_validate_snapshot_no_dates():
    data = {
        "capital": 10000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [],
        "daily_signals": [],
    }
    is_valid, snapshot_date, errors, warnings = vds.validate_snapshot(data)
    assert is_valid is False
    assert any("date" in e for e in errors)

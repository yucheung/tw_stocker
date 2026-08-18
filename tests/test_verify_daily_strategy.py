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


# ==========================================
# Task 2 Tests: TP/SL & TIME Rule Validators
# ==========================================

def test_compute_atr20():
    # 25 days of constant high=105, low=95, close=100
    dates = pd.date_range("2026-07-01", periods=25, freq="B")
    high = pd.DataFrame({"2330": [105.0] * 25}, index=dates)
    low = pd.DataFrame({"2330": [95.0] * 25}, index=dates)
    close = pd.DataFrame({"2330": [100.0] * 25}, index=dates)
    open_ = pd.DataFrame({"2330": [100.0] * 25}, index=dates)
    vol = pd.DataFrame({"2330": [1000] * 25}, index=dates)
    md = vds.MarketData(close=close, open=open_, high=high, low=low, volume=vol)

    atr_series = vds.compute_atr20("2330", md)
    assert not atr_series.empty
    # TR for each day = 105 - 95 = 10.0
    assert pytest.approx(atr_series.iloc[-1], rel=1e-3) == 10.0


def test_tp_sl_uses_atr_available_before_entry():
    # Calendar: 2026-08-17 (Mon), 2026-08-18 (Tue), 2026-08-19 (Wed)
    cal = xcals.get_calendar("XTAI")
    # Entry date: 2026-08-19. Previous session is 2026-08-18.
    # Create 30 days of data ending on 2026-08-19
    sessions = cal.sessions_in_range("2026-07-01", "2026-08-19")
    n = len(sessions)
    # TR up to 2026-08-18 was 10.0 (high=105, low=95, close=100)
    # On 2026-08-19, ATR jumps to 50.0 (high=150, low=50)
    high_vals = [105.0] * (n - 1) + [150.0]
    low_vals = [95.0] * (n - 1) + [50.0]
    close_vals = [100.0] * n
    open_vals = [100.0] * n
    vol_vals = [1000] * n

    md = vds.MarketData(
        close=pd.DataFrame({"2330": close_vals}, index=sessions),
        open=pd.DataFrame({"2330": open_vals}, index=sessions),
        high=pd.DataFrame({"2330": high_vals}, index=sessions),
        low=pd.DataFrame({"2330": low_vals}, index=sessions),
        volume=pd.DataFrame({"2330": vol_vals}, index=sessions),
    )

    # Stored TP/SL calculated with entry=100.0, ATR20(2026-08-18)=10.0
    # TP = 100 + 4 * 10 = 140.0
    # SL = 100 - 3 * 10 = 70.0
    positions = {
        "2330": {
            "entry": 100.0,
            "tp": 140.0,
            "sl": 70.0,
            "entry_date": "2026-08-19",
            "shares": 1000,
            "day_count": 0,
        }
    }

    result = vds.validate_tp_sl(positions, md, calendar=cal)
    assert result.severity == "PASS"
    assert result.rule_id == "tp_sl"
    assert "符合" in result.summary


def test_tp_sl_violation():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-07-01", "2026-08-19")
    n = len(sessions)
    md = vds.MarketData(
        close=pd.DataFrame({"2330": [100.0] * n}, index=sessions),
        open=pd.DataFrame({"2330": [100.0] * n}, index=sessions),
        high=pd.DataFrame({"2330": [105.0] * n}, index=sessions),
        low=pd.DataFrame({"2330": [95.0] * n}, index=sessions),
        volume=pd.DataFrame({"2330": [1000] * n}, index=sessions),
    )

    # Correct SL should be 70.0, but stored is 80.0 (delta = 10.0 > tolerance)
    positions = {
        "2330": {
            "entry": 100.0,
            "tp": 140.0,
            "sl": 80.0,
            "entry_date": "2026-08-19",
            "shares": 1000,
            "day_count": 0,
        }
    }

    result = vds.validate_tp_sl(positions, md, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics["violations"] == 1
    assert any("2330" in d and "SL" in d for d in result.details)


def test_tp_sl_sanity_check():
    cal = xcals.get_calendar("XTAI")
    positions = {
        "2330": {
            "entry": 100.0,
            "tp": 90.0,  # tp < entry (invalid)
            "sl": 70.0,
            "entry_date": "2026-08-19",
            "shares": 1000,
            "day_count": 0,
        }
    }
    # Market data is empty, but sanity check should catch it immediately
    md = vds.MarketData(
        close=pd.DataFrame(), open=pd.DataFrame(), high=pd.DataFrame(), low=pd.DataFrame(), volume=pd.DataFrame()
    )
    result = vds.validate_tp_sl(positions, md, calendar=cal)
    assert result.severity == "CRITICAL"


def test_tp_sl_missing_market_data():
    cal = xcals.get_calendar("XTAI")
    positions = {
        "2330": {
            "entry": 100.0,
            "tp": 140.0,
            "sl": 70.0,
            "entry_date": "2026-08-19",
            "shares": 1000,
            "day_count": 0,
        }
    }
    # Market data has no columns
    md = vds.MarketData(
        close=pd.DataFrame(), open=pd.DataFrame(), high=pd.DataFrame(), low=pd.DataFrame(), volume=pd.DataFrame()
    )
    result = vds.validate_tp_sl(positions, md, calendar=cal)
    assert result.severity == "WARNING"
    assert result.metrics["unknown"] == 1


def test_time_rule_normal_pass():
    cal = xcals.get_calendar("XTAI")
    # 2026-08-10 (Mon) to 2026-08-14 (Fri) has 4 sessions in (2026-08-10, 2026-08-14]
    positions = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": "2026-08-10",
            "shares": 10,
            "day_count": 4,
        }
    }
    result = vds.validate_time_rule(positions, "2026-08-14", calendar=cal)
    assert result.severity == "PASS"
    assert result.rule_id == "time_rule"
    assert result.metrics["max_holding_days"] == 4


def test_time_rule_boundary_20_days():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-07-01", "2026-08-14")
    entry_date = sessions[-21].strftime("%Y-%m-%d")
    snapshot_date = sessions[-1].strftime("%Y-%m-%d")

    positions = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date,
            "shares": 10,
            "day_count": 20,
        }
    }
    result = vds.validate_time_rule(positions, snapshot_date, calendar=cal)
    assert result.severity == "PASS"
    assert result.metrics["max_holding_days"] == 20


def test_time_rule_exceeded_21_days():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-07-01", "2026-08-14")
    entry_date = sessions[-22].strftime("%Y-%m-%d")
    snapshot_date = sessions[-1].strftime("%Y-%m-%d")

    positions = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date,
            "shares": 10,
            "day_count": 21,
        }
    }
    result = vds.validate_time_rule(positions, snapshot_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics["max_holding_days"] == 21


def test_time_rule_day_count_mismatch():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-07-01", "2026-08-14")
    entry_date = sessions[-22].strftime("%Y-%m-%d")  # 21 calendar days
    snapshot_date = sessions[-1].strftime("%Y-%m-%d")

    # Tracker says day_count=18, but calendar days is 21 -> effective_days = 21 -> CRITICAL
    positions = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date,
            "shares": 10,
            "day_count": 18,
        }
    }
    result = vds.validate_time_rule(positions, snapshot_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics["max_holding_days"] == 21

    # Tracker says day_count=5, but calendar days is 4 -> effective_days = 5 <= 20 -> WARNING for mismatch
    entry_date_short = sessions[-5].strftime("%Y-%m-%d")  # 4 calendar days
    positions_short = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date_short,
            "shares": 10,
            "day_count": 5,
        }
    }
    res_short = vds.validate_time_rule(positions_short, snapshot_date, calendar=cal)
    assert res_short.severity == "WARNING"


def test_time_rule_future_entry_date():
    cal = xcals.get_calendar("XTAI")
    positions = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": "2026-08-20",  # Future relative to snapshot
            "shares": 10,
            "day_count": 0,
        }
    }
    result = vds.validate_time_rule(positions, "2026-08-14", calendar=cal)
    assert result.severity == "CRITICAL"


# ========================================================
# Task 3 Tests: Regime, Sector & Position Count Validators
# ========================================================

@pytest.mark.parametrize(
    ("above_60", "above_20", "expected_cap"),
    [
        (True, True, 1.0),
        (True, False, 0.7),
        (False, True, 0.4),
        (False, False, 0.1),
    ],
)
def test_regime_cap_mapping(above_60, above_20, expected_cap):
    assert vds.regime_cap(above_60, above_20) == expected_cap


def test_regime_exposure_pass_within_tolerance():
    # 0050 close=100.0, MA20=90.0, MA60=80.0 -> above both -> cap 100%
    dates = pd.date_range("2026-05-01", "2026-08-14", freq="B")
    close_df = pd.DataFrame(
        {
            "0050": [80.0] * 50 + [90.0] * 15 + [100.0] * (len(dates) - 65),
            "2330": [1000.0] * len(dates),
        },
        index=dates,
    )
    md = vds.MarketData(
        close=close_df,
        open=close_df,
        high=close_df,
        low=close_df,
        volume=pd.DataFrame(1000, index=dates, columns=close_df.columns),
    )

    positions = {
        "2330": {"entry": 1000.0, "tp": 1100.0, "sl": 900.0, "entry_date": "2026-08-10", "shares": 70, "day_count": 4}
    }
    # position_value = 70 * 1000 = 70,000. capital = 30,000 -> total = 100,000 -> exposure = 70%
    snapshot = {
        "capital": 30000.0,
        "positions": positions,
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-14", "equity": 100000.0}],
        "daily_signals": [],
    }

    result = vds.validate_regime_exposure(snapshot, md, "2026-08-14")
    assert result.severity == "PASS"
    assert result.rule_id == "regime_exposure"
    assert result.metrics["regime_cap"] == 1.0


def test_regime_exposure_exceeded():
    # 0050: MA60=100, MA20=80, close=90 -> close <= MA60, close > MA20 -> cap 40% (0.40)
    dates = pd.date_range("2026-05-01", "2026-08-14", freq="B")
    n = len(dates)
    # Create price series for 0050: 60 bars avg ~ 100, last 20 avg ~ 80, current close = 90
    p_0050 = [100.0] * (n - 21) + [75.0] * 20 + [90.0]
    close_df = pd.DataFrame(
        {
            "0050": p_0050,
            "2330": [1000.0] * n,
        },
        index=dates,
    )
    md = vds.MarketData(
        close=close_df,
        open=close_df,
        high=close_df,
        low=close_df,
        volume=pd.DataFrame(1000, index=dates, columns=close_df.columns),
    )

    # position value = 50 * 1000 = 50,000. Capital = 50,000. Total = 100,000 -> exposure = 50% > 40% (+0.5% tol)
    positions = {
        "2330": {"entry": 1000.0, "tp": 1100.0, "sl": 900.0, "entry_date": "2026-08-10", "shares": 50, "day_count": 4}
    }
    snapshot = {
        "capital": 50000.0,
        "positions": positions,
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-14", "equity": 100000.0}],
        "daily_signals": [],
    }

    result = vds.validate_regime_exposure(snapshot, md, "2026-08-14")
    assert result.severity == "CRITICAL"
    assert result.metrics["regime_cap"] == 0.4
    assert result.metrics["exposure"] == 0.5


def test_regime_exposure_missing_data():
    # 0050 has only 10 bars (< 60 bars)
    dates = pd.date_range("2026-08-01", "2026-08-14", freq="B")
    close_df = pd.DataFrame({"0050": [100.0] * len(dates), "2330": [1000.0] * len(dates)}, index=dates)
    md = vds.MarketData(
        close=close_df, open=close_df, high=close_df, low=close_df, volume=pd.DataFrame(100, index=dates, columns=close_df.columns)
    )
    positions = {
        "2330": {"entry": 1000.0, "tp": 1100.0, "sl": 900.0, "entry_date": "2026-08-10", "shares": 50, "day_count": 4}
    }
    snapshot = {
        "capital": 50000.0,
        "positions": positions,
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-14", "equity": 100000.0}],
        "daily_signals": [],
    }
    result = vds.validate_regime_exposure(snapshot, md, "2026-08-14")
    assert result.severity == "WARNING"


def test_sector_concentration_pass():
    # 2330 (semiconductor), 2603 (shipping)
    # 2330: 50,000 value (50%), 2603: 50,000 value (50%) -> <= 75% -> PASS
    positions = {
        "2330": {"entry": 500.0, "tp": 600.0, "sl": 400.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
        "2603": {"entry": 100.0, "tp": 120.0, "sl": 80.0, "entry_date": "2026-08-10", "shares": 500, "day_count": 4},
    }
    prices = {"2330": 500.0, "2603": 100.0}
    result = vds.validate_sector_concentration(positions, prices)
    assert result.severity == "PASS"
    assert result.rule_id == "sector_concentration"
    assert result.metrics["max_sector_pct"] == 0.5


def test_sector_concentration_boundary_75_tolerance():
    # Semiconductor: 75.4% (within 75% + 0.5% tolerance) -> PASS
    positions = {
        "2330": {"entry": 754.0, "tp": 800.0, "sl": 700.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
        "2603": {"entry": 246.0, "tp": 300.0, "sl": 200.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
    }
    prices = {"2330": 754.0, "2603": 246.0}
    result = vds.validate_sector_concentration(positions, prices)
    assert result.severity == "PASS"

    # Semiconductor: 76.0% (> 75.5%) -> CRITICAL
    prices_crit = {"2330": 760.0, "2603": 240.0}
    positions_crit = {
        "2330": {"entry": 760.0, "tp": 800.0, "sl": 700.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
        "2603": {"entry": 240.0, "tp": 300.0, "sl": 200.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
    }
    result_crit = vds.validate_sector_concentration(positions_crit, prices_crit)
    assert result_crit.severity == "CRITICAL"


def test_sector_concentration_missing_price():
    positions = {
        "2330": {"entry": 500.0, "tp": 600.0, "sl": 400.0, "entry_date": "2026-08-10", "shares": 100, "day_count": 4},
        "2603": {"entry": 100.0, "tp": 120.0, "sl": 80.0, "entry_date": "2026-08-10", "shares": 500, "day_count": 4},
    }
    # 2603 price missing
    prices = {"2330": 500.0}
    result = vds.validate_sector_concentration(positions, prices)
    assert result.severity == "WARNING"


def test_position_count_pass_and_exceeded():
    positions_6 = {f"233{i}": {"shares": 10} for i in range(6)}
    res_6 = vds.validate_position_count(positions_6, limit=7)
    assert res_6.severity == "PASS"
    assert res_6.summary == "6 / 7"

    positions_7 = {f"233{i}": {"shares": 10} for i in range(7)}
    res_7 = vds.validate_position_count(positions_7, limit=7)
    assert res_7.severity == "PASS"
    assert res_7.summary == "7 / 7"

    positions_8 = {f"233{i}": {"shares": 10} for i in range(8)}
    res_8 = vds.validate_position_count(positions_8, limit=7)
    assert res_8.severity == "CRITICAL"
    assert "超出上限" in res_8.summary

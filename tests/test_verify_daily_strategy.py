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
    snapshot_date = sessions[-1].strftime("%Y-%m-%d")

    # 19 days holding -> PASS
    entry_date_19 = sessions[-20].strftime("%Y-%m-%d")
    positions_19 = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date_19,
            "shares": 10,
            "day_count": 19,
        }
    }
    res_19 = vds.validate_time_rule(positions_19, snapshot_date, calendar=cal)
    assert res_19.severity == "PASS"
    assert res_19.metrics["max_holding_days"] == 19

    # 20 days holding -> CRITICAL (must exit on/at day 20)
    entry_date_20 = sessions[-21].strftime("%Y-%m-%d")
    positions_20 = {
        "2330": {
            "entry": 1000.0,
            "tp": 1100.0,
            "sl": 900.0,
            "entry_date": entry_date_20,
            "shares": 10,
            "day_count": 20,
        }
    }
    result = vds.validate_time_rule(positions_20, snapshot_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics["max_holding_days"] == 20
    assert result.metrics["violations"] == 1


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


# ============================================
# Task 4 Tests: Top-7 Signal Consistency Tests
# ============================================

def test_compute_v85_scores():
    # Construct 60 stocks with 70 trading days of data
    dates = pd.date_range("2026-05-01", "2026-08-14", freq="B")
    tickers = [f"stock_{i:02d}" for i in range(60)]

    # stock_00 has highest momentum and trend
    data = {}
    for i, t in enumerate(tickers):
        # Stock base price
        base = 100.0 + i
        # Upward drift proportional to -i (so stock_00 drifts most up)
        drift = (60 - i) * 0.5
        series = [base + drift * (j / len(dates)) for j in range(len(dates))]
        data[t] = series

    close_df = pd.DataFrame(data, index=dates)
    vol_df = pd.DataFrame(100000.0, index=dates, columns=tickers)
    md = vds.MarketData(close=close_df, open=close_df, high=close_df * 1.01, low=close_df * 0.99, volume=vol_df)

    scores_df, ma60_df, universe_mask = vds.compute_v85_scores(md, top_n=60)
    assert not scores_df.empty
    # stock_00 should have score = 4.0
    last_scores = scores_df.iloc[-1]
    assert pytest.approx(last_scores["stock_00"], rel=1e-3) == 4.0
    assert last_scores["stock_00"] > last_scores["stock_59"]


def _build_limit_order_universe(cal, end_session="2026-08-17"):
    """60-ticker universe where score/MA60 rank T00 highest; open == close by
    default so callers can override a specific ticker's next-session open to
    control the FILLED / CANCELLED_OPEN_ABOVE_LIMIT decision precisely."""
    sessions = cal.sessions_in_range("2026-05-01", end_session)
    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)
    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)
    return sessions, md


def test_signal_consistency_pass_with_matured_gap():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)  # includes next session 2026-08-17
    signal_date = "2026-08-14"

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "PASS"
    assert result.rule_id == "signal_consistency"
    assert "合格" in result.summary


def test_missing_next_open_is_indeterminate_warning():
    cal = xcals.get_calendar("XTAI")
    # Data only up to signal_date (2026-08-14), no next session 2026-08-17
    sessions, md = _build_limit_order_universe(cal, end_session="2026-08-14")
    signal_date = "2026-08-14"

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "WARNING"
    assert any("待" in d or "PENDING" in d for d in result.details) or "待" in result.summary
    assert result.metrics["indeterminate"] is True


def test_next_open_below_signal_close_is_expected_fill():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    md.open.loc[sessions[-1], "T00"] = limit_price - 5.0  # next_open < limit -> expected FILLED

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity != "CRITICAL"
    assert "T00" in result.metrics["expected_fills"]


def test_next_open_above_limit_is_expected_cancel_not_violation():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    md.open.loc[sessions[-1], "T00"] = limit_price + 140.0  # next_open > limit -> expected cancel, not a violation

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity != "CRITICAL"
    assert "T00" in result.metrics["expected_cancellations"]


def test_position_above_limit_is_critical_impossible_fill():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"
    next_session_str = sessions[-1].strftime("%Y-%m-%d")

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    fill_above_limit = limit_price + 50.0
    md.open.loc[sessions[-1], "T00"] = fill_above_limit  # market says open > limit -> should cancel

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        # ...but a position exists proving it filled anyway, above the limit.
        "positions": {
            "T00": {
                "entry": fill_above_limit,
                "tp": fill_above_limit + 200.0,
                "sl": fill_above_limit - 50.0,
                "entry_date": next_session_str,
                "shares": 100,
                "day_count": 0,
            }
        },
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert "entry > limit" in " ".join(result.details)


def test_order_event_filled_despite_open_above_limit_is_critical():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    fill_above_limit = limit_price + 50.0
    md.open.loc[sessions[-1], "T00"] = fill_above_limit

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "status": "FILLED",
                "fill_price": fill_above_limit,
            }
        ],
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert "entry > limit" in " ".join(result.details)


def test_order_event_fill_price_mismatches_next_open_is_critical():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 5.0
    md.open.loc[sessions[-1], "T00"] = actual_open

    # fill_price differs from next_open by more than tolerance
    bogus_fill = actual_open - 10.0
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "status": "FILLED",
                "fill_price": bogus_fill,
            }
        ],
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert "與次日開盤" in " ".join(result.details)


def test_expected_filled_but_order_event_cancelled_warns():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"
    next_session_str = sessions[-1].strftime("%Y-%m-%d")

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 2.0
    md.open.loc[sessions[-1], "T00"] = actual_open  # open <= limit -> expected FILLED

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "execution_date": next_session_str,
                "status": "CANCELLED_NO_CAPACITY",
                "fill_price": None,
            }
        ],
    }

    # execution_date <= today (開盤後驗證) -> 應產生 WARNING
    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal, today=next_session_str)
    assert result.severity == "WARNING"
    assert "預期應成交" in " ".join(result.details)
    assert "CANCELLED_NO_CAPACITY" in " ".join(result.details)


def test_expected_filled_reverse_check_skipped_when_execution_date_in_future():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"
    next_session_str = sessions[-1].strftime("%Y-%m-%d")

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 2.0
    md.open.loc[sessions[-1], "T00"] = actual_open  # open <= limit -> expected FILLED

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "execution_date": next_session_str,
                "status": "CANCELLED_NO_CAPACITY",
                "fill_price": None,
            }
        ],
    }

    # execution_date > today (隔天還沒開盤) -> 跳過反向檢查，不得誤報 WARNING
    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal, today=signal_date)
    assert result.severity == "PASS"
    assert not any("預期應成交" in d for d in result.details)


def test_missing_order_events_and_positions_warns():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    # Make T00 expected FILLED (next_open <= limit_price)
    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    md.open.loc[sessions[-1], "T00"] = limit_price - 5.0

    # snapshot has NO order_events and NO positions for T00
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "positions": {},
        "order_events": [],
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "WARNING"
    assert "T00" in result.metrics["missing_event_tickers"]
    assert any("T00" in d and "訂單遺失" in d for d in result.details)


def test_order_events_or_positions_present_prevents_missing_event_warning():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"
    next_session_str = sessions[-1].strftime("%Y-%m-%d")

    # Make T00 expected FILLED
    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 5.0
    md.open.loc[sessions[-1], "T00"] = actual_open

    # Case A: order_events has FILLED event
    snapshot_event = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "positions": {},
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "execution_date": next_session_str,
                "status": "FILLED",
                "fill_price": actual_open,
            }
        ],
    }
    result_ev = vds.validate_signal_consistency(snapshot_event, md, signal_date, calendar=cal)
    assert "T00" not in result_ev.metrics["missing_event_tickers"]

    # Case B: positions has matching entry
    snapshot_pos = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "positions": {
            "T00": {
                "entry": actual_open,
                "tp": actual_open + 50.0,
                "sl": actual_open - 30.0,
                "entry_date": next_session_str,
                "shares": 100,
                "day_count": 0,
            }
        },
        "order_events": [],
    }
    result_pos = vds.validate_signal_consistency(snapshot_pos, md, signal_date, calendar=cal)
    assert "T00" not in result_pos.metrics["missing_event_tickers"]


def test_is_future_dirty_date_fail_closed_triggers_warning():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 2.0
    md.open.loc[sessions[-1], "T00"] = actual_open  # open <= limit -> expected FILLED

    # execution_date is corrupt / dirty (not matching YYYY-MM-DD or None)
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "execution_date": "INVALID_DATE_FORMAT",
                "status": "CANCELLED_CORRUPT",
                "fill_price": None,
            }
        ],
    }

    # Fail-closed (is_future=False): warning is NOT suppressed and dirty data is visible
    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal, today=signal_date)
    assert result.severity == "WARNING"
    assert any("預期應成交" in d and "CANCELLED_CORRUPT" in d for d in result.details)


def test_event_and_position_both_present_independent_if():
    cal = xcals.get_calendar("XTAI")
    sessions, md = _build_limit_order_universe(cal)
    signal_date = "2026-08-14"
    next_session = "2026-08-17"

    limit_price = float(md.close.loc[pd.Timestamp(signal_date), "T00"])
    actual_open = limit_price - 2.0
    md.open.loc[sessions[-1], "T00"] = actual_open

    # Event looks fine, but position has invalid entry price
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}],
        "positions": {
            "T00": {
                "entry": limit_price + 100.0,  # Invalid position entry > limit!
                "entry_date": next_session,
                "shares": 100,
            }
        },
        "order_events": [
            {
                "ticker": "T00",
                "signal_date": signal_date,
                "status": "FILLED",
                "fill_price": actual_open,
            }
        ],
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert "部位 entry" in " ".join(result.details)


def test_signal_consistency_ma60_violation():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    # Make T00 close below MA60 on 2026-08-14
    data["T00"] = [200.0] * (len(sessions) - 5) + [50.0] * 5  # MA60 ~ 180, current close = 50 < 180
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)

    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": [f"T{i:02d}" for i in range(7)]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"


def test_signal_consistency_duplicates_and_count_violation():
    cal = xcals.get_calendar("XTAI")
    md = vds.MarketData(close=pd.DataFrame(), open=pd.DataFrame(), high=pd.DataFrame(), low=pd.DataFrame(), volume=pd.DataFrame())

    # Duplicate tickers
    snap_dup = {"daily_signals": [{"date": "2026-08-14", "tickers": ["2330", "2330", "2454"]}]}
    res_dup = vds.validate_signal_consistency(snap_dup, md, "2026-08-14", calendar=cal)
    assert res_dup.severity == "CRITICAL"
    assert "重複" in res_dup.summary or any("重複" in d for d in res_dup.details)

    # 8 tickers
    snap_8 = {"daily_signals": [{"date": "2026-08-14", "tickers": [f"233{i}" for i in range(8)]}]}
    res_8 = vds.validate_signal_consistency(snap_8, md, "2026-08-14", calendar=cal)
    assert res_8.severity == "CRITICAL"
    assert "超過" in res_8.summary or "7" in res_8.summary


def test_v85_score_matches_order_artifact():
    # Load orders_20260814.json artifact
    with open("artifacts/orders_20260814.json", "r", encoding="utf-8") as f:
        artifact = json.load(f)
    orders = artifact["orders"]
    assert len(orders) == 7

    # Verify score formula on rank 1 and 2
    # In 60 stocks universe:
    # Rank 1: rank_mom=1.0, rank_trend=1.0 -> 3*1.0 + 1*1.0 = 4.0
    assert orders[0]["score"] == 4.0
    # Rank 2: 3*(59/60) + 1*(59/60) = 3.9333...
    assert pytest.approx(orders[1]["score"], abs=1e-4) == 4 * 59 / 60


# ========================================================
# Task 5 Tests: Renderer, JSON Logger & CLI Orchestration
# ========================================================

def test_non_trading_day_is_completely_silent(tmp_path, capsys):
    fetcher = Mock()
    log_file = tmp_path / "verify_log.json"
    cfg = vds.VerifyConfig(log_path=str(log_file))

    # 2026-08-23 is Sunday (non-trading day)
    now_sunday = datetime(2026, 8, 23, 17, 30, tzinfo=vds.TAIPEI_TZ)
    report = vds.run_verification(
        config=cfg,
        now=now_sunday,
        fetcher=fetcher,
    )
    assert report == ""
    assert capsys.readouterr() == ("", "")
    fetcher.assert_not_called()
    assert not log_file.exists()


def test_render_report_all_pass():
    context = {
        "run_date": "2026-08-19",
        "snapshot_date": "2026-08-19",
        "run_id": "20260819T173012+0800",
        "overall_status": "PASS",
        "paper_source": "github_raw",
        "critical_count": 0,
        "warning_count": 0,
        "pass_count": 6,
    }
    results = [
        vds.CheckResult("tp_sl", "TP/SL 價格", "PASS", "6/6 符合 ATR 4×/3×", []),
        vds.CheckResult("time_rule", "20 天 TIME rule", "PASS", "最長持倉 8 天", []),
        vds.CheckResult("regime_exposure", "Regime 曝險", "PASS", "實際 66.4% <= 上限 70.0%", []),
        vds.CheckResult("sector_concentration", "Sector concentration", "PASS", "最高半導體 41.8% <= 75.0%", []),
        vds.CheckResult("signal_consistency", "Top-7 信號一致性", "PASS", "7/7 score 與 MA 合格；Gap 已驗證", []),
        vds.CheckResult("position_count", "持倉數量", "PASS", "6 / 7", []),
    ]
    report = vds.render_report(context, results)
    assert "📋 tw_stocker v8.5 每日策略驗證" in report
    assert "總結：✅ PASS｜6 pass" in report
    assert "✅ PASS TP/SL 價格｜6/6 符合 ATR 4×/3×" in report
    assert "稽核 ID：20260819T173012+0800" in report


def test_render_report_mixed_severity():
    context = {
        "run_date": "2026-08-19",
        "snapshot_date": "2026-08-19",
        "run_id": "20260819T173012+0800",
        "overall_status": "CRITICAL",
        "paper_source": "github_raw",
        "critical_count": 2,
        "warning_count": 2,
        "pass_count": 2,
    }
    results = [
        vds.CheckResult("tp_sl", "TP/SL 價格", "CRITICAL", "1/6 部位不符", ["2330 SL=920.00，預期 905.40"]),
        vds.CheckResult("time_rule", "20 天 TIME rule", "PASS", "最長持倉 12 天", []),
        vds.CheckResult("regime_exposure", "Regime 曝險", "CRITICAL", "實際 73.2% > 上限 70.0%", ["0050 close=198.40"]),
        vds.CheckResult("sector_concentration", "Sector concentration", "WARNING", "2454 缺收盤價，無法完整判定", []),
        vds.CheckResult("signal_consistency", "Top-7 信號一致性", "WARNING", "score/MA 通過；Gap 待 2026-08-20 開盤驗證", []),
        vds.CheckResult("position_count", "持倉數量", "PASS", "6 / 7", []),
    ]
    report = vds.render_report(context, results)
    assert "總結：🚨 CRITICAL｜2 critical / 2 warning / 2 pass" in report
    assert "🚨 CRITICAL TP/SL 價格｜1/6 部位不符" in report
    assert "  2330 SL=920.00，預期 905.40" in report


def test_append_verify_log_creates_and_appends(tmp_path):
    log_file = tmp_path / "verify_log.json"
    context1 = {
        "run_id": "RUN_01",
        "run_at": "2026-08-19T17:30:00+08:00",
        "run_date": "2026-08-19",
        "snapshot_date": "2026-08-19",
        "overall_status": "PASS",
        "paper_source": "github_raw",
        "paper_url": "https://example.com/paper.json",
        "critical_count": 0,
        "warning_count": 0,
        "pass_count": 6,
    }
    results = [vds.CheckResult("tp_sl", "TP/SL 價格", "PASS", "all pass", [], {"checked": 1})]
    vds.append_verify_log(str(log_file), context1, results)

    assert log_file.exists()
    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(data["runs"]) == 1
    assert data["runs"][0]["run_id"] == "RUN_01"

    # Append second run
    context2 = dict(context1, run_id="RUN_02")
    vds.append_verify_log(str(log_file), context2, results)
    data2 = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(data2["runs"]) == 2
    assert data2["runs"][1]["run_id"] == "RUN_02"


def test_append_verify_log_corrupt_file_raises(tmp_path):
    log_file = tmp_path / "verify_log.json"
    log_file.write_text("INVALID_JSON{", encoding="utf-8")
    context = {
        "run_id": "RUN_01",
        "run_at": "2026-08-19T17:30:00+08:00",
        "run_date": "2026-08-19",
        "snapshot_date": "2026-08-19",
        "overall_status": "PASS",
        "critical_count": 0,
        "warning_count": 0,
        "pass_count": 6,
    }
    with pytest.raises(RuntimeError):
        vds.append_verify_log(str(log_file), context, [])


def test_run_verification_orchestration_pass(tmp_path):
    log_file = tmp_path / "verify_log.json"
    cfg = vds.VerifyConfig(log_path=str(log_file))

    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-19")
    run_dt = datetime(2026, 8, 19, 17, 30, tzinfo=vds.TAIPEI_TZ)

    # 60 tickers
    tickers = ["0050", "2330", "2454", "2603"] + [f"T{i:02d}" for i in range(56)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)
    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)

    snapshot = {
        "capital": 100000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-19", "equity": 100000.0}],
        "daily_signals": [{"date": "2026-08-19", "tickers": tickers[:7]}],
    }

    mock_fetcher = Mock(return_value=json.dumps(snapshot))
    mock_market_fetcher = Mock(return_value=md)

    report = vds.run_verification(
        config=cfg,
        now=run_dt,
        fetcher=mock_fetcher,
        market_fetcher=mock_market_fetcher,
        calendar=cal,
    )
    assert "📋 tw_stocker v8.5 每日策略驗證" in report
    assert log_file.exists()
    log_data = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(log_data["runs"]) == 1
    assert len(log_data["runs"][0]["checks"]) == 6


def test_signal_consistency_ranking_violation_low_score_selected_high_score_omitted():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)

    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)

    # Actual signals omit T00 (highest score) and include T07 (rank 8)
    bad_signals = ["T01", "T02", "T03", "T04", "T05", "T06", "T07"]
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": bad_signals}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert any("遺漏高分合格股" in d or "不符 Top-7 排名" in d for d in result.details)
    assert "T00" in str(result.details)
    assert "T07" in str(result.details)


def test_signal_consistency_ranking_violation_wrong_order():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)

    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)

    # Correct top 7 stocks but in reversed order
    reversed_signals = ["T06", "T05", "T04", "T03", "T02", "T01", "T00"]
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": reversed_signals}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert any("順序不符排名" in d or "不一致" in d for d in result.details)


def test_signal_consistency_ranking_pass_when_fewer_than_7_eligible():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    # Only T00, T01, T02 have strong upward momentum; others are flat/downward (scores < 2.0 or price <= MA60)
    data = {}
    for i, t in enumerate(tickers):
        if i < 3:
            data[t] = [100.0 + (10 - i) * (j / len(sessions)) for j in range(len(sessions))]
        else:
            # Downward trend, below MA60
            data[t] = [200.0 - (j / len(sessions)) * 50 for j in range(len(sessions))]

    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)

    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)

    # Actual signals has only the 3 eligible stocks
    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": ["T00", "T01", "T02"]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "PASS"
    assert result.metrics["passed_count"] == 3


def test_signal_consistency_coverage_gate_warning():
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    # Only 30 stocks in market data (< 48 required)
    tickers = [f"T{i:02d}" for i in range(30)]
    data = {t: [100.0 + (30 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    open_df = close_df.copy()
    high_df = close_df * 1.05
    low_df = close_df * 0.95
    vol_df = pd.DataFrame(100000.0, index=sessions, columns=tickers)

    md = vds.MarketData(close=close_df, open=open_df, high=high_df, low=low_df, volume=vol_df)

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": ["T00", "T01", "T02"]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "WARNING"
    assert result.metrics["universe_coverage_insufficient"] is True
    assert "母體資料不足" in result.summary
    assert "score/MA 通過" not in result.summary


def test_signal_consistency_no_matching_date_warns():
    """當日無匹配 daily_signals 應為 WARNING，不得 PASS。"""
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    md = vds.MarketData(close=close_df, open=close_df.copy(), high=close_df * 1.05, low=close_df * 0.95,
                        volume=pd.DataFrame(100000.0, index=sessions, columns=tickers))

    # daily_signals 只有其他日期，沒有 2026-08-14
    snapshot = {
        "daily_signals": [{"date": "2026-08-13", "tickers": ["T00", "T01"]}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "WARNING"
    assert result.metrics["signal_count"] == 0
    assert result.metrics.get("indeterminate") is True


def test_signal_consistency_empty_tickers_critical():
    """signal entry 存在但 tickers 為空陣列 → CRITICAL（schema 異常）。"""
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    md = vds.MarketData(close=close_df, open=close_df.copy(), high=close_df * 1.05, low=close_df * 0.95,
                        volume=pd.DataFrame(100000.0, index=sessions, columns=tickers))

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": []}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics.get("schema_error") is True


def test_signal_consistency_null_tickers_critical():
    """tickers: null 應為 CRITICAL（schema 異常），不得 TypeError 降級為 WARNING。"""
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    tickers = [f"T{i:02d}" for i in range(60)]
    data = {t: [100.0 + (60 - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
    close_df = pd.DataFrame(data, index=sessions)
    md = vds.MarketData(close=close_df, open=close_df.copy(), high=close_df * 1.05, low=close_df * 0.95,
                        volume=pd.DataFrame(100000.0, index=sessions, columns=tickers))

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": None}]
    }

    result = vds.validate_signal_consistency(snapshot, md, signal_date, calendar=cal)
    assert result.severity == "CRITICAL"
    assert result.metrics.get("schema_error") is True
    assert "型別" in result.summary


def test_signal_consistency_coverage_boundary_53_and_54():
    """53 欄 < 54 門檻 → WARNING；54 欄 = 門檻 → 正常評分。"""
    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-17")
    signal_date = "2026-08-14"

    def build_md(n_cols):
        tickers = [f"T{i:02d}" for i in range(n_cols)]
        data = {t: [100.0 + (n_cols - i) * (j / len(sessions)) for j in range(len(sessions))] for i, t in enumerate(tickers)}
        close_df = pd.DataFrame(data, index=sessions)
        return vds.MarketData(close=close_df, open=close_df.copy(), high=close_df * 1.05, low=close_df * 0.95,
                              volume=pd.DataFrame(100000.0, index=sessions, columns=tickers))

    snapshot = {
        "daily_signals": [{"date": signal_date, "tickers": ["T00", "T01"]}]
    }

    # 53 欄 → WARNING (coverage insufficient)
    r53 = vds.validate_signal_consistency(snapshot, build_md(53), signal_date, calendar=cal)
    assert r53.severity == "WARNING"
    assert r53.metrics.get("universe_coverage_insufficient") is True

    # 54 欄 → 正常評分（非 coverage 問題）
    r54 = vds.validate_signal_consistency(snapshot, build_md(54), signal_date, calendar=cal)
    assert r54.metrics.get("universe_coverage_insufficient") is False or "universe_coverage_insufficient" not in r54.metrics


def test_run_verification_stale_snapshot_elevates_to_critical(tmp_path):
    log_file = tmp_path / "verify_log.json"
    cfg = vds.VerifyConfig(log_path=str(log_file))

    cal = xcals.get_calendar("XTAI")
    sessions = cal.sessions_in_range("2026-05-01", "2026-08-19")
    run_dt = datetime(2026, 8, 19, 17, 30, tzinfo=vds.TAIPEI_TZ)

    tickers = ["0050", "2330"] + [f"T{i:02d}" for i in range(58)]
    data = {t: [100.0] * len(sessions) for t in tickers}
    close_df = pd.DataFrame(data, index=sessions)
    md = vds.MarketData(close=close_df, open=close_df, high=close_df, low=close_df, volume=pd.DataFrame(1000, index=sessions, columns=tickers))

    # Snapshot is from 2026-08-18 (yesterday), run_date is 2026-08-19
    snapshot = {
        "capital": 100000.0,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-18", "equity": 100000.0}],
        "daily_signals": [],
    }

    report = vds.run_verification(
        config=cfg,
        now=run_dt,
        fetcher=Mock(return_value=json.dumps(snapshot)),
        market_fetcher=Mock(return_value=md),
        calendar=cal,
    )

    assert "🚨 CRITICAL" in report
    assert "Stale snapshot" in report or "stale snapshot" in report
    log_data = json.loads(log_file.read_text(encoding="utf-8"))
    assert log_data["runs"][0]["overall_status"] == "CRITICAL"
    assert any(c["rule_id"] == "snapshot_freshness" and c["severity"] == "CRITICAL" for c in log_data["runs"][0]["checks"])


def test_run_verification_schema_error_elevates_to_critical(tmp_path):
    log_file = tmp_path / "verify_log.json"
    cfg = vds.VerifyConfig(log_path=str(log_file))

    cal = xcals.get_calendar("XTAI")
    run_dt = datetime(2026, 8, 19, 17, 30, tzinfo=vds.TAIPEI_TZ)

    # Missing "capital" field
    corrupt_snapshot = {
        "positions": {},
        "closed_trades": [],
        "equity_curve": [{"date": "2026-08-19", "equity": 100000.0}],
        "daily_signals": [],
    }

    md = vds.MarketData(close=pd.DataFrame(), open=pd.DataFrame(), high=pd.DataFrame(), low=pd.DataFrame(), volume=pd.DataFrame())

    report = vds.run_verification(
        config=cfg,
        now=run_dt,
        fetcher=Mock(return_value=json.dumps(corrupt_snapshot)),
        market_fetcher=Mock(return_value=md),
        calendar=cal,
    )

    assert "🚨 CRITICAL" in report
    log_data = json.loads(log_file.read_text(encoding="utf-8"))
    assert log_data["runs"][0]["overall_status"] == "CRITICAL"
    assert any(c["rule_id"] == "snapshot_schema" and c["severity"] == "CRITICAL" for c in log_data["runs"][0]["checks"])


def test_run_verification_json_decode_error_elevates_to_critical(tmp_path):
    log_file = tmp_path / "verify_log.json"
    cfg = vds.VerifyConfig(log_path=str(log_file), paper_local_path="/nonexistent/paper.json")

    cal = xcals.get_calendar("XTAI")
    run_dt = datetime(2026, 8, 19, 17, 30, tzinfo=vds.TAIPEI_TZ)

    report = vds.run_verification(
        config=cfg,
        now=run_dt,
        fetcher=Mock(return_value="NOT_VALID_JSON{"),
        calendar=cal,
    )

    assert "🚨 CRITICAL" in report
    assert "Paper Snapshot 載入" in report
    log_data = json.loads(log_file.read_text(encoding="utf-8"))
    assert log_data["runs"][0]["overall_status"] == "CRITICAL"
    assert log_data["runs"][0]["checks"][0]["rule_id"] == "paper_snapshot"
    assert log_data["runs"][0]["checks"][0]["severity"] == "CRITICAL"


def test_append_verify_log_locking(tmp_path):
    log_file = tmp_path / "verify_log.json"
    lock_file = tmp_path / "verify_log.json.lock"

    context = {
        "run_id": "RUN_LOCK_TEST",
        "run_at": "2026-08-19T17:30:00+08:00",
        "run_date": "2026-08-19",
        "snapshot_date": "2026-08-19",
        "overall_status": "PASS",
        "critical_count": 0,
        "warning_count": 0,
        "pass_count": 6,
    }
    vds.append_verify_log(str(log_file), context, [])
    assert log_file.exists()
    assert lock_file.exists()
    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(data["runs"]) == 1


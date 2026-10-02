import numpy as np
import pandas as pd
import exchange_calendars as xcals

from scripts.replay_multifactor_c import simulate_open_price_replay


def test_three_month_replay_runs_signal_orders_open_fill_and_close_mark(tmp_path):
    sessions = xcals.get_calendar("XTAI").sessions_in_range("2023-10-01", "2024-03-31")
    sessions = sessions.tz_localize(None)
    prices = {}
    for ticker, drift in (("0050.TW", 0.0), ("1111.TW", 0.12), ("2222.TW", 0.08), ("3333.TW", 0.04)):
        close = 80.0 + np.arange(len(sessions), dtype=float) * drift
        prices[ticker] = pd.DataFrame(
            {
                "Open": close - 0.4,
                "High": close + 0.5,
                "Low": close - 1.0,
                "Close": close,
                "Volume": 1_000_000,
                "Turnover": close * 1_000_000,
            },
            index=sessions,
        )

    result = simulate_open_price_replay(
        "2024-01-01",
        "2024-03-31",
        prices,
        {},
        {},
        initial_capital=100_000,
        orders_dir=tmp_path / "orders",
    )

    state = result["state"]
    assert [signal["signal_date"] for signal in result["signals"]] == [
        "2024-01-11",
        "2024-02-15",
        "2024-03-11",
    ]
    assert all(len(signal["target_symbols"]) == 3 for signal in result["signals"])
    assert result["filled_orders"] == 3
    assert state["equity_curve"]
    assert len(state["equity_curve"]) > 30
    assert all(position["tp"] is None and position["sl"] is None for position in state["positions"].values())
    assert (tmp_path / "orders" / "orders_20240111.json").exists()

import numpy as np
import pandas as pd

from daily_signal_mfc import create_daily_signal
from independent_sim import get_default_state


def _price_frame(index, drift=0.0, volume=1_000_000):
    close = 100.0 + np.arange(len(index), dtype=float) * drift
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": volume,
            "Turnover": close * volume,
        },
        index=index,
    )


def test_daily_signal_writes_next_session_equal_weight_orders_on_rebalance_date(tmp_path):
    dates = pd.bdate_range("2023-03-01", "2024-01-11")
    prices = {
        "0050.TW": _price_frame(dates),
        "1111.TW": _price_frame(dates, drift=0.1),
        "2222.TW": _price_frame(dates, drift=0.2),
    }
    state = get_default_state(strategy_id="multifactor_c_v1")

    payload = create_daily_signal(
        "2024-01-11",
        prices,
        {},
        {},
        state,
        tmp_path,
    )

    assert payload["is_rebalance_date"] is True
    assert payload["execution_date"] == "2024-01-12"
    assert {order["side"] for order in payload["orders"]} == {"buy"}
    assert {order["execution_date"] for order in payload["orders"]} == {"2024-01-12"}
    assert {order["time_in_force"] for order in payload["orders"]} == {"DAY"}
    assert all(order["limit_price"] == order["reference_close"] for order in payload["orders"])
    assert len({order["slot_amount"] for order in payload["orders"]}) == 1
    assert (tmp_path / "orders_20240111.json").exists()


def test_daily_signal_writes_empty_orders_before_the_11th_and_does_not_rebalance(tmp_path):
    dates = pd.bdate_range("2023-03-01", "2024-01-12")
    prices = {
        "0050.TW": _price_frame(dates),
        "1111.TW": _price_frame(dates, drift=0.1),
    }
    state = get_default_state(strategy_id="multifactor_c_v1")

    payload = create_daily_signal("2024-01-10", prices, {}, {}, state, tmp_path)

    assert payload["is_rebalance_date"] is False
    assert payload["orders"] == []
    assert payload["metadata"]["target_symbols"] == []
    assert (tmp_path / "orders_20240110.json").exists()

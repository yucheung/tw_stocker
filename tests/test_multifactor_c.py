import numpy as np
import pandas as pd

from strategy.multifactor_c import FactorBuilder, StrategyC, build_market_membership, build_rebalance_orders


def _prices(index, close=100.0, volume=1_000_000.0):
    close_values = np.full(len(index), close, dtype=float)
    return pd.DataFrame(
        {
            "Open": close_values,
            "High": close_values,
            "Low": close_values,
            "Close": close_values,
            "Volume": np.full(len(index), volume, dtype=float),
            "Turnover": close_values * volume,
        },
        index=index,
    )


def test_snapshot_excludes_unannounced_revenue_and_future_institution_rows():
    as_of = pd.Timestamp("2024-01-10")
    prices = pd.bdate_range(end=as_of, periods=80)
    revenue_dates = list(pd.date_range("2023-02-01", periods=12, freq="MS")) + [pd.Timestamp("2024-01-02")]
    revenue = pd.DataFrame(
        {
            "date": revenue_dates,
            "revenue": list(range(100, 112)) + [999],
            "revenue_YoY": list(range(1, 12)) + [15, 999],
            "revenue_3m_YoY": list(range(1, 12)) + [15, 999],
        }
    )
    institutions = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-09", "2024-01-11"]),
            "name": ["Investment_Trust", "Investment_Trust"],
            "net_buy": [5, 999],
        }
    )
    builder = FactorBuilder(
        {"0050.TW": _prices(prices), "2330.TW": _prices(prices)},
        {"2330.TW": revenue},
        {"2330.TW": institutions},
    )

    snapshot = builder.compute_factors_snapshot(as_of).set_index("symbol")

    assert snapshot.loc["2330.TW", "rev_yoy"] == 15
    assert snapshot.loc["2330.TW", "trust_net_buy_20d"] == 5
    assert snapshot.loc["2330.TW", "trust_ratio_20d"] == 5 / 20_000_000 * 100


def test_rebalance_dates_roll_to_first_available_session_after_the_10th():
    sessions = pd.to_datetime(
        ["2024-01-10", "2024-01-12", "2024-01-15", "2024-02-09", "2024-02-12", "2024-02-13"]
    )
    builder = FactorBuilder({"0050.TW": _prices(sessions)})

    dates = builder.get_rebalance_dates("2024-01-01", "2024-02-29")

    assert [d.strftime("%Y-%m-%d") for d in dates] == ["2024-01-12", "2024-02-12"]


def test_market_pool_for_each_year_uses_only_prior_calendar_year_turnover():
    dates = pd.to_datetime(["2020-01-02", "2020-12-30", "2021-01-04", "2021-12-30"])
    prices = {
        "1111.TW": _prices(dates, close=20, volume=100),
        "2222.TWO": _prices(dates, close=10, volume=100),
    }
    prices["1111.TW"].loc[pd.Timestamp("2021-01-04"), "Turnover"] = 1
    prices["2222.TWO"].loc[pd.Timestamp("2021-01-04"), "Turnover"] = 1_000_000

    membership = build_market_membership(prices, top_n=1)

    assert membership[2021] == ["1111.TW"]


def test_strategy_c_selects_top_n_equal_weights_and_reduces_bear_exposure():
    snapshot = pd.DataFrame(
        [
            {"symbol": "1111.TW", "is_liquid": True, "above_ma20": True, "ma_bullish": True,
             "ret_20d": 10, "ret_60d": 20, "rev_yoy": 30, "rev_3m_yoy": 30,
             "trust_ratio_20d": 30, "trust_net_buy_20d": 30, "foreign_net_buy_20d": 30},
            {"symbol": "2222.TW", "is_liquid": True, "above_ma20": True, "ma_bullish": True,
             "ret_20d": 20, "ret_60d": 10, "rev_yoy": 20, "rev_3m_yoy": 20,
             "trust_ratio_20d": 20, "trust_net_buy_20d": 20, "foreign_net_buy_20d": 20},
            {"symbol": "3333.TW", "is_liquid": True, "above_ma20": True, "ma_bullish": False,
             "ret_20d": 1, "ret_60d": 1, "rev_yoy": 1, "rev_3m_yoy": 1,
             "trust_ratio_20d": 1, "trust_net_buy_20d": 1, "foreign_net_buy_20d": 1},
        ]
    )
    strategy = StrategyC(top_n=2, bear_equity_exposure=0.2)

    bull = strategy.select_portfolio(snapshot, is_market_bullish=True)
    bear = strategy.select_portfolio(snapshot, is_market_bullish=False)

    assert bull == {"1111.TW": 0.5, "2222.TW": 0.5}
    assert bear == {"1111.TW": 0.1, "2222.TW": 0.1}


def test_rebalance_orders_sell_old_holdings_and_use_equal_weight_budget_per_buy():
    orders = build_rebalance_orders(
        "2026-09-11",
        "2026-09-14",
        {"2330.TW": 0.1, "2317.TW": 0.1},
        {"2330.TW": 900.0, "2317.TW": 200.0, "1101.TW": 30.0},
        {"1101.TW": {"shares": 100, "entry": 25.0}},
        equity=100_000,
    )

    sell = next(order for order in orders if order["side"] == "sell")
    buys = [order for order in orders if order["side"] == "buy"]
    assert sell["order_id"] == "multifactor_c_v1:2026-09-11:1101.TW:sell"
    assert sell["execution_date"] == "2026-09-14"
    assert all(order["time_in_force"] == "DAY" for order in orders)
    assert [order["slot_amount"] for order in buys] == [10_000.0, 10_000.0]


def test_rebalance_orders_resize_retained_names_and_only_sell_the_overweight_shares():
    orders = build_rebalance_orders(
        "2026-09-11",
        "2026-09-14",
        {"2330.TW": 0.5, "2317.TW": 0.5},
        {"2330.TW": 100.0, "2317.TW": 100.0},
        {"2330.TW": {"shares": 600, "entry": 90.0, "last_valid_close": 100.0}},
        equity=100_000,
    )

    assert [(order["side"], order["ticker"], order.get("shares")) for order in orders] == [
        ("sell", "2330.TW", 100),
        ("buy", "2317.TW", None),
    ]
    assert orders[1]["slot_amount"] == 50_000.0

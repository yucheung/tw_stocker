import json

import independent_sim as sim


def test_multifactor_c_strategy_config_is_isolated_and_odd_lot():
    config = sim.get_strategy_config("multifactor_c_v1")

    assert config["strategy_id"] == "multifactor_c_v1"
    assert config["default_data_dir"] == "independent_sim_data_mfc"
    assert config["orders_dir"] == "artifacts/multifactor_c"
    assert config["max_positions"] == 20
    assert config["lot_size"] == 1
    assert config["trade_unit"] == "odd_lot"
    assert config["initial_capital"] == 1_000_000.0


def test_mfc_order_planning_keeps_sides_and_uses_next_session():
    state = sim.get_default_state(strategy_id="multifactor_c_v1")
    source_orders = [
        {
            "order_id": "multifactor_c_v1:2024-01-15:1101.TW:sell",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "1101.TW",
            "side": "sell",
            "shares": 3,
            "reference_close": 30,
            "limit_price": 30,
            "time_in_force": "DAY",
        },
        {
            "order_id": "multifactor_c_v1:2024-01-15:2330.TW:buy",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "2330.TW",
            "side": "buy",
            "slot_amount": 10_000,
            "reference_close": 600,
            "limit_price": 600,
            "time_in_force": "DAY",
        },
    ]

    pending = sim.plan_mfc_orders(state, source_orders, "2024-01-15")

    assert [order["side"] for order in pending] == ["sell", "buy"]
    assert [order["execution_date"] for order in pending] == ["2024-01-16", "2024-01-16"]
    assert pending[0]["shares"] == 3
    assert pending[1]["slot_amount"] == 10_000
    assert all(order["time_in_force"] == "DAY" for order in pending)


def test_mfc_open_execution_sells_then_buys_and_skips_sub_share_slot():
    state = sim.get_default_state(strategy_id="multifactor_c_v1", capital=100_000)
    state["cash"] = 50_000.0
    state["positions"]["1101.TW"] = {
        "ticker": "1101.TW",
        "entry": 25.0,
        "last_valid_close": 30.0,
        "last_valid_date": "2024-01-15",
        "shares": 100,
        "tp": None,
        "sl": None,
        "tp_sl_mode": "none",
        "entry_date": "2024-01-10",
        "signal_date": "2024-01-09",
        "order_id": "old-entry",
    }
    state["pending_orders"] = [
        {
            "order_id": "sell-old",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "1101.TW",
            "side": "sell",
            "shares": 100,
            "reference_close": 30.0,
            "limit_price": 30.0,
            "time_in_force": "DAY",
        },
        {
            "order_id": "buy-one-share-budget",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "2330.TW",
            "side": "buy",
            "slot_amount": 2_000.0,
            "reference_close": 20.0,
            "limit_price": 20.0,
            "time_in_force": "DAY",
        },
        {
            "order_id": "buy-under-one-share",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "2454.TW",
            "side": "buy",
            "slot_amount": 20.0,
            "reference_close": 50.0,
            "limit_price": 50.0,
            "time_in_force": "DAY",
        },
    ]

    events = sim.execute_open_orders(
        state,
        {
            "1101.TW": {"date": "2024-01-16", "open": 31.0, "close": 32.0},
            "2330.TW": {"date": "2024-01-16", "open": 20.0, "close": 21.0},
            "2454.TW": {"date": "2024-01-16", "open": 50.0, "close": 51.0},
        },
        "2024-01-16",
    )

    by_id = {event["order_id"]: event for event in events}
    assert by_id["sell-old"]["status"] == "FILLED"
    assert by_id["buy-one-share-budget"]["status"] == "FILLED"
    assert by_id["buy-one-share-budget"]["shares"] == 99
    assert by_id["buy-under-one-share"]["status"] == "CANCELLED_BELOW_LOT_SIZE"
    assert "1 share" in by_id["buy-under-one-share"]["message"]
    assert state["positions"]["2330.TW"]["tp"] is None
    assert state["positions"]["2330.TW"]["sl"] is None
    assert "1101.TW" not in state["positions"]
    assert state["closed_trades"][-1]["exit_reason"] == "REBALANCE"


def test_mfc_open_execution_can_add_to_an_underweight_retained_position():
    state = sim.get_default_state(strategy_id="multifactor_c_v1", capital=100_000)
    state["cash"] = 50_000.0
    state["positions"]["2330.TW"] = {
        "ticker": "2330.TW",
        "entry": 90.0,
        "last_valid_close": 100.0,
        "buy_cost_remaining": 920.0,
        "shares": 10,
        "tp": None,
        "sl": None,
        "tp_sl_mode": "none",
        "entry_date": "2024-01-10",
        "signal_date": "2024-01-09",
        "order_id": "old-entry",
    }
    state["pending_orders"] = [
        {
            "order_id": "buy-retained-underweight",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "2330.TW",
            "side": "buy",
            "slot_amount": 2_000.0,
            "reference_close": 20.0,
            "limit_price": 20.0,
            "time_in_force": "DAY",
        }
    ]

    events = sim.execute_open_orders(
        state,
        {"2330.TW": {"date": "2024-01-16", "open": 20.0, "close": 21.0}},
        "2024-01-16",
    )

    assert events[0]["status"] == "FILLED"
    assert state["positions"]["2330.TW"]["shares"] > 10
    assert 20.0 < state["positions"]["2330.TW"]["entry"] < 90.0
    assert state["positions"]["2330.TW"]["entry_date"] == "2024-01-10"
    expected_total_cost = 920.0 + events[0]["shares"] * events[0]["fill_price"] + events[0]["buy_cost"]
    assert state["positions"]["2330.TW"]["buy_cost_remaining"] == expected_total_cost

    position = state["positions"]["2330.TW"]
    shares_before_sale = position["shares"]
    cost_before_sale = position["buy_cost_remaining"]
    sold_shares = 10
    state["pending_orders"] = [
        {
            "order_id": "sell-partial-scale-in-position",
            "signal_date": "2024-02-15",
            "execution_date": "2024-02-16",
            "ticker": "2330.TW",
            "side": "sell",
            "shares": sold_shares,
            "reference_close": 21.0,
            "limit_price": 21.0,
            "time_in_force": "DAY",
        }
    ]

    sale_events = sim.execute_open_orders(
        state,
        {"2330.TW": {"date": "2024-02-16", "open": 21.0, "close": 21.5}},
        "2024-02-16",
    )

    allocated_cost = round(cost_before_sale * sold_shares / shares_before_sale, 2)
    expected_buy_cost = round(allocated_cost - position["entry"] * sold_shares, 2)
    assert sale_events[0]["status"] == "FILLED"
    assert state["closed_trades"][-1]["buy_cost"] == expected_buy_cost
    assert position["buy_cost_remaining"] == round(cost_before_sale - allocated_cost, 2)


def test_mfc_close_only_marks_equity_and_labels_non_rebalance_day(tmp_path, monkeypatch, capsys):
    state = sim.get_default_state(strategy_id="multifactor_c_v1", capital=100_000)
    state["cash"] = 50_000.0
    state["positions"]["2330.TW"] = {
        "ticker": "2330.TW",
        "entry": 10.0,
        "last_valid_close": 10.0,
        "shares": 100,
        "tp": 11.0,
        "sl": 9.0,
        "entry_date": "2024-01-10",
        "signal_date": "2024-01-09",
        "order_id": "entry",
    }
    data_dir = tmp_path / "sim"
    sim.save_state_atomic(state, data_dir)
    order_path = tmp_path / "orders_20240115.json"
    order_path.write_text(
        json.dumps(
            {
                "strategy_id": "multifactor_c_v1",
                "signal_date": "2024-01-15",
                "execution_date": "2024-01-16",
                "is_rebalance_date": False,
                "orders": [],
                "metadata": {"previous_holdings": ["2330.TW"], "target_symbols": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sim,
        "fetch_market_bars",
        lambda tickers, as_of=None: {
            "2330.TW": {"date": as_of, "open": 8.0, "high": 12.0, "low": 7.0, "close": 8.0}
        },
    )
    monkeypatch.setattr(sim, "fetch_benchmark_close", lambda as_of: 100.0)

    sim.run_close_and_plan(data_dir, order_path, as_of="2024-01-15")

    saved = sim.load_state(data_dir)
    output = capsys.readouterr().out
    assert "2330.TW" in saved["positions"]
    assert saved["closed_trades"] == []
    assert saved["equity_curve"][-1]["market_value"] == 800.0
    assert "非調倉日" in output


def test_mfc_status_reports_positions_and_pending_orders_without_tp_sl_or_atr(tmp_path, capsys):
    state = sim.get_default_state(strategy_id="multifactor_c_v1", capital=100_000)
    state["positions"]["2330.TW"] = {
        "ticker": "2330.TW",
        "entry": 10.0,
        "last_valid_close": 11.0,
        "shares": 100,
        "tp": None,
        "sl": None,
        "tp_sl_mode": "none",
        "entry_date": "2024-01-10",
        "signal_date": "2024-01-09",
        "order_id": "entry",
    }
    state["pending_orders"] = [
        {
            "order_id": "buy-next-open",
            "signal_date": "2024-01-15",
            "execution_date": "2024-01-16",
            "ticker": "2317.TW",
            "side": "buy",
            "limit_price": 100.0,
            "slot_amount": 5_000.0,
        }
    ]
    sim.save_state_atomic(state, tmp_path)

    sim.print_status(tmp_path)

    output = capsys.readouterr().out
    assert "TP/SL=none" in output
    assert "Buy" in output
    assert "ATR" not in output

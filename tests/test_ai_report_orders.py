from ai_report import build_order_record

BASE_CONFIG = {
    'max_hold_days': 20,
    'position_size': 0.10,
    'tp_sl_mode': 'atr',
    'tp_atr_mult': 4.0,
    'sl_atr_mult': 3.0,
    'tp_pct': 0.15,
    'sl_pct': 0.08,
}


def test_order_record_declares_close_limit_lifecycle():
    order = build_order_record(
        signal_date="2026-08-19",
        execution_date="2026-08-20",
        ticker="2330",
        rank=1,
        score=3.2,
        reference_close=1180.0,
        strategy_config=BASE_CONFIG,
    )
    assert order["order_type"] == "limit"
    assert order["limit_price"] == 1180.0
    assert order["entry_model"] == "signal_close_limit_next_open_v1"
    assert order["time_in_force"] == "DAY_UNTIL_0930"
    assert order["cancel_time"] == "09:30:00"
    assert order["timezone"] == "Asia/Taipei"
    assert "gap_limit_atr" not in order


def test_order_record_carries_rank_score_and_reference_close():
    order = build_order_record(
        signal_date="2026-08-19",
        execution_date="2026-08-20",
        ticker="2330",
        rank=2,
        score=3.2,
        reference_close=1180.0,
        strategy_config=BASE_CONFIG,
    )
    assert order["ticker"] == "2330"
    assert order["side"] == "buy"
    assert order["rank"] == 2
    assert order["score"] == 3.2
    assert order["reference_close"] == 1180.0
    assert order["signal_date"] == "2026-08-19"
    assert order["execution_date"] == "2026-08-20"


def test_order_record_includes_tp_sl_when_provided():
    order = build_order_record(
        signal_date="2026-08-19",
        execution_date="2026-08-20",
        ticker="2330",
        rank=1,
        score=3.2,
        reference_close=1180.0,
        strategy_config=BASE_CONFIG,
        tp_price=1300.0,
        sl_price=1100.0,
        atr=25.0,
    )
    assert order["tp_price"] == 1300.0
    assert order["sl_price"] == 1100.0
    assert order["atr"] == 25.0

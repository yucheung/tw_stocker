import math

import pytest

from strategy.order_execution import evaluate_buy_limit_at_open


@pytest.mark.parametrize(
    ("open_price", "status", "fill_price"),
    [
        (99.0, "FILLED", 99.0),
        (100.0, "FILLED", 100.0),
        (100.01, "CANCELLED_OPEN_ABOVE_LIMIT", None),
        (None, "CANCELLED_NO_OPEN_PRICE", None),
        (float("nan"), "CANCELLED_NO_OPEN_PRICE", None),
        (0.0, "CANCELLED_NO_OPEN_PRICE", None),
    ],
)
def test_evaluate_buy_limit_at_open(open_price, status, fill_price):
    decision = evaluate_buy_limit_at_open(100.0, open_price)
    assert decision.status == status
    assert decision.fill_price == fill_price


@pytest.mark.parametrize("limit", [0.0, -1.0, math.nan, math.inf])
def test_invalid_limit_is_internal_order_error(limit):
    with pytest.raises(ValueError, match="limit_price"):
        evaluate_buy_limit_at_open(limit, 99.0)


def test_evaluate_buy_limit_until_0930_open_fill():
    from strategy.order_execution import evaluate_buy_limit_until_0930

    # Open touches limit at open auction
    decision = evaluate_buy_limit_until_0930(limit_price=100.0, open_price=99.0, low_price=95.0)
    assert decision.filled
    assert decision.status == "FILLED"
    assert decision.fill_price == 99.0
    assert not decision.is_estimated


def test_evaluate_buy_limit_until_0930_intraday_touch_and_cancel():
    from strategy.order_execution import evaluate_buy_limit_until_0930

    # open=1345, limit=1315, low=1300 -> FILLED @1315 (intraday estimated)
    decision = evaluate_buy_limit_until_0930(limit_price=1315.0, open_price=1345.0, low_price=1300.0)
    assert decision.filled
    assert decision.status == "FILLED_INTRADAY_ESTIMATED"
    assert decision.fill_price == 1315.0
    assert decision.is_estimated

    # low=1320 -> CANCELLED
    cancelled = evaluate_buy_limit_until_0930(limit_price=1315.0, open_price=1345.0, low_price=1320.0)
    assert not cancelled.filled
    assert cancelled.status == "CANCELLED_OPEN_ABOVE_LIMIT"
    assert cancelled.fill_price is None


def test_evaluate_buy_limit_until_0930_with_intraday_bars():
    from strategy.order_execution import evaluate_buy_limit_until_0930

    bars = [
        {"time": "09:05", "open": 1345.0, "high": 1345.0, "low": 1330.0, "close": 1335.0},
        {"time": "09:10", "open": 1335.0, "high": 1335.0, "low": 1310.0, "close": 1312.0},
    ]
    decision = evaluate_buy_limit_until_0930(limit_price=1315.0, open_price=1345.0, intraday_bars=bars)
    assert decision.filled
    assert decision.status == "FILLED"
    assert decision.fill_price == 1315.0
    assert not decision.is_estimated


@pytest.mark.parametrize("limit", [0.0, -1.0, math.nan, math.inf])
def test_evaluate_buy_limit_until_0930_invalid_limit(limit):
    from strategy.order_execution import evaluate_buy_limit_until_0930

    with pytest.raises(ValueError, match="limit_price"):
        evaluate_buy_limit_until_0930(limit, 99.0)


@pytest.mark.parametrize("open_px", [None, float("nan"), 0.0, -10.0])
def test_evaluate_buy_limit_until_0930_invalid_open(open_px):
    from strategy.order_execution import evaluate_buy_limit_until_0930

    decision = evaluate_buy_limit_until_0930(100.0, open_px)
    assert not decision.filled
    assert decision.status == "CANCELLED_NO_OPEN_PRICE"


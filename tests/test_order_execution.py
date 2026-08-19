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

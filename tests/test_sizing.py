"""Unit tests for strategy/sizing.py — shared lot-aware position sizing (A1).

Covers docs/EVAL_STRATEGY_20260910.md 批次 A1: integer-lot rounding, NT$20
minimum commission, and the CANCELLED_BELOW_LOT_SIZE / CANCELLED_INSUFFICIENT_CASH
split so a too-small position slot and genuinely-low cash are distinguishable.
"""
import math

import pytest

from strategy.sizing import compute_commission, size_position


class TestComputeCommission:
    def test_rate_based_commission_above_floor(self):
        # 5350 * 0.1425% = 7.62 -> floored to NT$20 minimum
        assert compute_commission(5350.0) == 20.0

    def test_rate_based_commission_above_min(self):
        # 100,000 * 0.1425% = 142.5 > 20 -> rate-based
        assert compute_commission(100_000.0) == 142.5

    def test_zero_notional_is_zero(self):
        assert compute_commission(0.0) == 0.0


class TestSizePosition:
    def test_price_too_high_for_slot_is_below_lot_size(self):
        # slot = 1,000,000 * 0.15 = 150,000; one lot of 1315 costs ~1.32M.
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=1315.0)
        assert result.shares == 0
        assert result.status == "CANCELLED_BELOW_LOT_SIZE"

    def test_affordable_price_rounds_down_to_full_lot(self):
        # slot = 150,000; one lot of 100 costs ~100,142.5; two lots (~200,285) don't fit.
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=100.0)
        assert result.shares == 1000
        assert result.shares != 1497  # not the pre-A1 fractional-share result

    def test_min_commission_applied_on_fill(self):
        # cash-capped to 1 lot @ 5.30 = 5300 notional -> rate commission 7.55 < 20 floor
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=5.30, cash=6000.0, reserve=0.0)
        assert result.shares == 1000
        assert result.commission == 20.0

    def test_slot_affordable_but_cash_short_is_insufficient_cash(self):
        # slot big enough for 1 lot at price 100 (~100,142.5) but cash after reserve is only 50,000
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=100.0, cash=50_000.0, reserve=0.0)
        assert result.shares == 0
        assert result.status == "CANCELLED_INSUFFICIENT_CASH"

    def test_odd_lot_allows_fractional_board_lot(self):
        # lot_size=1 (odd-lot mode): should not round down to nearest 1000
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=100.0, lot_size=1)
        assert result.shares == 1497

    def test_zero_price_cannot_be_sized(self):
        # No affordability is computable at a zero/invalid price.
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=0.0)
        assert result.shares == 0
        assert result.status == "CANCELLED_BELOW_LOT_SIZE"

    def test_ok_result_reports_trade_amount_and_commission(self):
        result = size_position(equity=1_000_000, position_size=0.15, fill_price=100.0)
        assert result.status == "OK"
        assert result.trade_amount == pytest.approx(1000 * 100.0)
        assert result.commission == pytest.approx(max(1000 * 100.0 * 0.001425, 20.0))

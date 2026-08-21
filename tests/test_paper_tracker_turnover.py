"""Unit and integration tests for paper_tracker turnover mechanism.

Tests find_replace_candidate() and 7/7 full-capacity replacement logic.
"""

import unittest
from unittest import mock
import pytest
import paper_tracker as pt


class TestFindReplaceCandidate(unittest.TestCase):
    def test_empty_positions_returns_none(self):
        self.assertIsNone(pt.find_replace_candidate({}, new_rank=1))

    def test_none_rank_returns_none(self):
        positions = {"2330": {"entry_rank": 5, "day_count": 6}}
        self.assertIsNone(pt.find_replace_candidate(positions, new_rank=None))

    def test_held_less_than_five_days_returns_none(self):
        # Held 4 days: even with large rank gap (7 - 1 = 6), should not replace (< 5 days)
        positions = {"2330": {"entry_rank": 7, "day_count": 4}}
        self.assertIsNone(pt.find_replace_candidate(positions, new_rank=1))

    def test_held_five_to_eight_days_requires_gap_ge_three(self):
        # 5-8 days (5 <= day_count < 8): requires rank gap >= 3
        positions_fail = {"2330": {"entry_rank": 3, "day_count": 6}}  # gap 3 - 1 = 2 < 3
        self.assertIsNone(pt.find_replace_candidate(positions_fail, new_rank=1))

        positions_pass = {"2330": {"entry_rank": 4, "day_count": 6}}  # gap 4 - 1 = 3 >= 3
        self.assertEqual(pt.find_replace_candidate(positions_pass, new_rank=1), "2330")

    def test_held_eight_to_twelve_days_requires_gap_ge_two(self):
        # 8-12 days (8 <= day_count <= 12): requires rank gap >= 2
        positions_fail = {"2330": {"entry_rank": 2, "day_count": 10}}  # gap 2 - 1 = 1 < 2
        self.assertIsNone(pt.find_replace_candidate(positions_fail, new_rank=1))

        positions_pass = {"2330": {"entry_rank": 3, "day_count": 10}}  # gap 3 - 1 = 2 >= 2
        self.assertEqual(pt.find_replace_candidate(positions_pass, new_rank=1), "2330")

    def test_held_over_twelve_days_requires_gap_ge_one(self):
        # > 12 days (day_count > 12): requires rank gap >= 1
        positions_fail = {"2330": {"entry_rank": 1, "day_count": 13}}  # gap 1 - 1 = 0 < 1
        self.assertIsNone(pt.find_replace_candidate(positions_fail, new_rank=1))

        positions_pass = {"2330": {"entry_rank": 2, "day_count": 13}}  # gap 2 - 1 = 1 >= 1
        self.assertEqual(pt.find_replace_candidate(positions_pass, new_rank=1), "2330")

    def test_tie_breaking_by_day_count(self):
        # Both 2317 and 3008 have entry_rank 7 (gap 6), but 2317 has day_count 10 vs 3008 day_count 6
        positions = {
            "3008": {"entry_rank": 7, "day_count": 6},
            "2317": {"entry_rank": 7, "day_count": 10},
        }
        res = pt.find_replace_candidate(positions, new_rank=1)
        self.assertEqual(res, "2317")

    def test_tie_breaking_by_ticker(self):
        # Same rank, same day_count -> alphabetical
        positions = {
            "3008": {"entry_rank": 7, "day_count": 6},
            "2317": {"entry_rank": 7, "day_count": 6},
        }
        res = pt.find_replace_candidate(positions, new_rank=1)
        self.assertEqual(res, "2317")

    def test_missing_entry_rank_safely_ignored(self):
        positions = {
            "2330": {"day_count": 10},  # no entry_rank
            "2317": {"entry_rank": 6, "day_count": 6},  # gap 6 - 1 = 5 >= 3
        }
        res = pt.find_replace_candidate(positions, new_rank=1)
        self.assertEqual(res, "2317")


class TestTurnoverIntegration(unittest.TestCase):
    def test_turnover_execution_replaces_weakest_when_full(self):
        today = pt.date.today().isoformat()
        # 7 positions already full
        positions = {
            f"T{i}": {
                "entry": 100.0,
                "tp": 120.0,
                "sl": 80.0,
                "entry_date": "2026-08-01",
                "shares": 100,
                "day_count": i,
                "max_hold_days": 15,
                "entry_rank": i + 1,  # T0: rank 1, T6: rank 7
            }
            for i in range(7)
        }
        data = {
            "start_date": "2026-01-01",
            "initial_capital": 500_000,
            "capital": 100_000,
            "positions": positions,
            "pending_orders": [
                {
                    "ticker": "NEW_TKR",
                    "entry": 100.0,
                    "limit_price": 100.0,
                    "tp": 120.0,
                    "sl": 80.0,
                    "reference_close": 100.0,
                    "atr": 5.0,
                    "execution_date": today,
                    "signal_date": today,
                    "rank": 1,
                    "max_hold_days": 15,
                    "position_size": 0.07,
                    "regime_scale": 1.0,
                }
            ],
            "closed_trades": [],
            "equity_curve": [],
            "daily_signals": [],
            "order_events": [],
        }

        # Market bars: all existing positions close at 100, NEW_TKR opens at 99 (<= 100 limit)
        bars = {
            f"T{i}": {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "date": today}
            for i in range(7)
        }
        bars["NEW_TKR"] = {"open": 99.0, "high": 102.0, "low": 98.0, "close": 101.0, "date": today}

        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, "get_current_bars", return_value=bars), \
             mock.patch.object(pt, "extract_signals_from_report", return_value=[]), \
             mock.patch.object(pt.xcals, "get_calendar", return_value=mock_cal), \
             mock.patch.object(pt, "save_data"), \
             mock.patch.object(pt, "generate_html"):
            pt.update_tracker(data)

        # T6 (entry_rank 7) should be closed with reason 'REPLACE'
        self.assertNotIn("T6", data["positions"])
        self.assertIn("NEW_TKR", data["positions"])
        self.assertEqual(len(data["positions"]), 7)
        self.assertEqual(data["positions"]["NEW_TKR"]["entry_rank"], 1)

        # Check closed trades has REPLACE
        replace_trades = [t for t in data["closed_trades"] if t["reason"] == "REPLACE"]
        self.assertEqual(len(replace_trades), 1)
        self.assertEqual(replace_trades[0]["ticker"], "T6")

    def test_turnover_execution_cancels_when_rank_gap_insufficient(self):
        today = pt.date.today().isoformat()
        # 7 positions with ranks 1..7
        positions = {
            f"T{i}": {
                "entry": 100.0,
                "tp": 120.0,
                "sl": 80.0,
                "entry_date": "2026-08-01",
                "shares": 100,
                "day_count": i,
                "max_hold_days": 15,
                "entry_rank": i + 1,
            }
            for i in range(7)
        }
        data = {
            "start_date": "2026-01-01",
            "initial_capital": 500_000,
            "capital": 100_000,
            "positions": positions,
            "pending_orders": [
                {
                    "ticker": "WEAK_NEW",
                    "entry": 100.0,
                    "limit_price": 100.0,
                    "tp": 120.0,
                    "sl": 80.0,
                    "reference_close": 100.0,
                    "atr": 5.0,
                    "execution_date": today,
                    "signal_date": today,
                    "rank": 6,  # rank 6 vs weakest held rank 7 -> gap 1 < 2
                    "max_hold_days": 15,
                    "position_size": 0.07,
                    "regime_scale": 1.0,
                }
            ],
            "closed_trades": [],
            "equity_curve": [],
            "daily_signals": [],
            "order_events": [],
        }

        bars = {
            f"T{i}": {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "date": today}
            for i in range(7)
        }
        bars["WEAK_NEW"] = {"open": 99.0, "high": 102.0, "low": 98.0, "close": 101.0, "date": today}

        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, "get_current_bars", return_value=bars), \
             mock.patch.object(pt, "extract_signals_from_report", return_value=[]), \
             mock.patch.object(pt.xcals, "get_calendar", return_value=mock_cal), \
             mock.patch.object(pt, "save_data"), \
             mock.patch.object(pt, "generate_html"):
            pt.update_tracker(data)

        # No replacement should occur
        self.assertNotIn("WEAK_NEW", data["positions"])
        self.assertEqual(len(data["positions"]), 7)
        self.assertEqual(data["order_events"][-1]["status"], "CANCELLED_NO_CAPACITY")
        self.assertEqual(len(data["closed_trades"]), 0)

    def test_full_capacity_signals_queued_as_replace(self):
        today = pt.date.today().isoformat()
        # 7 positions already full with entry ranks 1..7
        positions = {
            f"T{i}": {
                "entry": 100.0,
                "tp": 120.0,
                "sl": 80.0,
                "entry_date": "2026-08-01",
                "shares": 100,
                "day_count": i,
                "max_hold_days": 15,
                "entry_rank": i + 1,
            }
            for i in range(7)
        }
        data = {
            "start_date": "2026-01-01",
            "initial_capital": 500_000,
            "capital": 100_000,
            "positions": positions,
            "pending_orders": [],
            "closed_trades": [],
            "equity_curve": [],
            "daily_signals": [],
            "order_events": [],
        }

        # 3 new signals:
        # 1: rank 1 -> vs T6 (rank 7), gap 6 >= 2 -> REPLACE
        # 2: rank 2 -> vs T5 (rank 6), gap 4 >= 2 -> REPLACE
        # 3: rank 7 -> vs remaining (rank 1..5), gap < 0 -> CANNOT REPLACE
        new_signals = [
            {"ticker": "NEW_1", "entry": 100.0, "tp": 120.0, "sl": 80.0, "rank": 1, "execution_date": "2099-01-02"},
            {"ticker": "NEW_2", "entry": 100.0, "tp": 120.0, "sl": 80.0, "rank": 2, "execution_date": "2099-01-02"},
            {"ticker": "NEW_3", "entry": 100.0, "tp": 120.0, "sl": 80.0, "rank": 7, "execution_date": "2099-01-02"},
        ]

        bars = {
            f"T{i}": {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "date": today}
            for i in range(7)
        }

        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, "get_current_bars", return_value=bars), \
             mock.patch.object(pt, "extract_signals_from_report", return_value=new_signals), \
             mock.patch.object(pt.xcals, "get_calendar", return_value=mock_cal), \
             mock.patch.object(pt, "save_data"), \
             mock.patch.object(pt, "generate_html"):
            pt.update_tracker(data)

        # Positions were full (7), so available_slots = 0.
        # NEW_1 and NEW_2 should be queued as REPLACE orders; NEW_3 discarded.
        self.assertEqual(len(data["pending_orders"]), 2)
        p_orders = {o["ticker"]: o for o in data["pending_orders"]}
        self.assertIn("NEW_1", p_orders)
        self.assertIn("NEW_2", p_orders)
        self.assertNotIn("NEW_3", p_orders)
        self.assertEqual(p_orders["NEW_1"]["action"], "REPLACE")
        self.assertEqual(p_orders["NEW_1"]["replace_target"], "T6")
        self.assertEqual(p_orders["NEW_2"]["action"], "REPLACE")
        self.assertEqual(p_orders["NEW_2"]["replace_target"], "T5")

    def test_turnover_execution_preserves_position_if_cash_insufficient(self):
        """P2-1 Atomicity: If sizing/cash fails for new order, the replace target position must NOT be closed."""
        today = pt.date.today().isoformat()
        # 7 positions already full with entry ranks 1..7, T6 held 6 days (gap 7 - 1 = 6 >= 3)
        positions = {
            f"T{i}": {
                "entry": 100.0,
                "tp": 120.0,
                "sl": 80.0,
                "entry_date": "2026-08-01",
                "shares": 100,
                "day_count": i,
                "max_hold_days": 15,
                "entry_rank": i + 1,
            }
            for i in range(7)
        }
        # Set capital so low that even with T6 proceeds, shares == 0 or cash < reserve_cash
        data = {
            "start_date": "2026-01-01",
            "initial_capital": 500_000,
            "capital": 0,  # 0 cash, reserve_cash = 100,000
            "positions": positions,
            "pending_orders": [
                {
                    "ticker": "EXPENSIVE_NEW",
                    "entry": 200_000.0,  # High price so trade_amount / fill_price = 0 shares
                    "limit_price": 200_000.0,
                    "tp": 240_000.0,
                    "sl": 160_000.0,
                    "reference_close": 200_000.0,
                    "atr": 5000.0,
                    "execution_date": today,
                    "signal_date": today,
                    "rank": 1,
                    "max_hold_days": 15,
                    "position_size": 0.07,
                    "regime_scale": 1.0,
                }
            ],
            "closed_trades": [],
            "equity_curve": [],
            "daily_signals": [],
            "order_events": [],
        }

        bars = {
            f"T{i}": {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0, "date": today}
            for i in range(7)
        }
        bars["EXPENSIVE_NEW"] = {"open": 200_000.0, "high": 201_000.0, "low": 199_000.0, "close": 200_000.0, "date": today}

        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, "get_current_bars", return_value=bars), \
             mock.patch.object(pt, "extract_signals_from_report", return_value=[]), \
             mock.patch.object(pt.xcals, "get_calendar", return_value=mock_cal), \
             mock.patch.object(pt, "save_data"), \
             mock.patch.object(pt, "generate_html"):
            pt.update_tracker(data)

        # Atomicity check:
        # 1. T6 should STILL be in positions!
        self.assertIn("T6", data["positions"])
        self.assertEqual(len(data["positions"]), 7)
        # 2. No trades should be recorded in closed_trades
        self.assertEqual(len(data["closed_trades"]), 0)
        # 3. Order should be cancelled due to insufficient cash
        self.assertEqual(data["order_events"][-1]["status"], "CANCELLED_INSUFFICIENT_CASH")


if __name__ == "__main__":
    unittest.main()

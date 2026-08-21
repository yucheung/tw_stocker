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
        positions = {"2330": {"entry_rank": 5, "day_count": 3}}
        self.assertIsNone(pt.find_replace_candidate(positions, new_rank=None))

    def test_rank_gap_less_than_two_returns_none(self):
        # held_rank 3, new_rank 2 -> gap 1 < 2
        positions = {"2330": {"entry_rank": 3, "day_count": 3}}
        self.assertIsNone(pt.find_replace_candidate(positions, new_rank=2, min_rank_gap=2))

    def test_rank_gap_ge_two_returns_weakest(self):
        # held_rank 5, new_rank 1 -> gap 4 >= 2
        positions = {
            "2330": {"entry_rank": 1, "day_count": 5},
            "2454": {"entry_rank": 3, "day_count": 4},
            "2317": {"entry_rank": 7, "day_count": 2},  # gap 7 - 1 = 6 (largest gap)
            "3008": {"entry_rank": 5, "day_count": 3},  # gap 5 - 1 = 4
        }
        res = pt.find_replace_candidate(positions, new_rank=1, min_rank_gap=2)
        self.assertEqual(res, "2317")

    def test_tie_breaking_by_day_count(self):
        # Both 2317 and 3008 have entry_rank 7 (gap 6), but 2317 has day_count 10 vs 3008 day_count 3
        positions = {
            "3008": {"entry_rank": 7, "day_count": 3},
            "2317": {"entry_rank": 7, "day_count": 10},
        }
        res = pt.find_replace_candidate(positions, new_rank=1, min_rank_gap=2)
        self.assertEqual(res, "2317")

    def test_tie_breaking_by_ticker(self):
        # Same rank, same day_count -> alphabetical
        positions = {
            "3008": {"entry_rank": 7, "day_count": 5},
            "2317": {"entry_rank": 7, "day_count": 5},
        }
        res = pt.find_replace_candidate(positions, new_rank=1, min_rank_gap=2)
        self.assertEqual(res, "2317")

    def test_missing_entry_rank_safely_ignored(self):
        positions = {
            "2330": {"day_count": 10},  # no entry_rank
            "2317": {"entry_rank": 6, "day_count": 5},  # gap 6 - 1 = 5
        }
        res = pt.find_replace_candidate(positions, new_rank=1, min_rank_gap=2)
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


if __name__ == "__main__":
    unittest.main()

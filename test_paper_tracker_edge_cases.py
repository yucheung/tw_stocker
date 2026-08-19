#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_tracker.py edge case 加固單元測試（Codex review B1 / A1 / A2 / A3 +
PLAN_simulation.md Task 4: 買進限價單 09:30 生命週期）

執行方式：
    python3 -m unittest test_paper_tracker_edge_cases -v
"""
import unittest
from unittest import mock

import paper_tracker as pt


class TestRecomputeTpSl(unittest.TestCase):
    """A1: NaN/無效 ATR 防護 + A2: 合法 0 值不被 or 覆寫 + A3: SL ≤ 0 sanity check"""

    def test_atr_none_fallback(self):
        # A1: ATR 為 None → 退回百分比 fallback（100 × 1.15 / 100 × 0.92）
        tp, sl = pt.recompute_tp_sl({}, 100.0, None)
        self.assertAlmostEqual(tp, 115.0)
        self.assertAlmostEqual(sl, 92.0)

    def test_atr_nan_fallback(self):
        # A1: ATR 為 NaN → 必須退回百分比 fallback
        #     （NaN <= 0 恆為 False，舊碼 `atr_val <= 0` 擋不住，會產生 NaN 價位）
        tp, sl = pt.recompute_tp_sl({}, 100.0, float('nan'))
        self.assertAlmostEqual(tp, 115.0)
        self.assertAlmostEqual(sl, 92.0)

    def test_atr_zero_and_negative_fallback(self):
        # A1: ATR = 0 或負值 → 退回百分比 fallback
        for bad_atr in (0.0, -3.0):
            tp, sl = pt.recompute_tp_sl({}, 100.0, bad_atr)
            self.assertAlmostEqual(tp, 115.0)
            self.assertAlmostEqual(sl, 92.0)

    def test_tp_sl_pct_zero_not_overridden(self):
        # A2: tp_pct / sl_pct = 0.0 是合法值，不應被 `or default` 覆寫成 0.15 / 0.08
        tp, sl = pt.recompute_tp_sl({'tp_pct': 0.0, 'sl_pct': 0.0}, 100.0, None)
        self.assertAlmostEqual(tp, 100.0)
        self.assertAlmostEqual(sl, 100.0)

    def test_atr_mult_zero_not_overridden(self):
        # A2: tp_atr_mult / sl_atr_mult = 0.0 不應被覆寫成預設 4.0 / 3.0
        order = {'tp_atr_mult': 0.0, 'sl_atr_mult': 0.0}
        tp, sl = pt.recompute_tp_sl(order, 100.0, 5.0)
        self.assertAlmostEqual(tp, 100.0)
        self.assertAlmostEqual(sl, 100.0)

    def test_huge_atr_sl_sanity(self):
        # A3: ATR 過大使 SL 為負（100 - 1000×3 = -2900）→ 退回百分比 fallback 92.0
        tp, sl = pt.recompute_tp_sl({}, 100.0, 1000.0)
        self.assertGreater(tp, 100.0)
        self.assertAlmostEqual(sl, 92.0)
        self.assertGreater(sl, 0)

    def test_sl_pct_ge_one_forced_back(self):
        # A3 保險: sl_pct = 1.0 使 SL = 0（非正）→ 強制退回預設 8%
        tp, sl = pt.recompute_tp_sl({'sl_pct': 1.0}, 100.0, None)
        self.assertAlmostEqual(sl, 92.0)
        self.assertGreater(sl, 0)

    def test_normal_atr_path_unchanged(self):
        # 正常 ATR 路徑不應被破壞：tp = 100 + 5×4 = 120, sl = 100 - 5×3 = 85
        tp, sl = pt.recompute_tp_sl({}, 100.0, 5.0)
        self.assertAlmostEqual(tp, 120.0)
        self.assertAlmostEqual(sl, 85.0)


class TestBuyLimitLifecycle(unittest.TestCase):
    """PLAN_simulation.md Task 4: 訊號日收盤限價，次日開盤 <= 限價成交，否則 09:30 撤單。"""

    def _run_open(self, bar_open, limit_price=150.0, bar_low=148.0, bar_high=155.0, atr=None, bar_date=None):
        today = pt.date.today().isoformat()
        if bar_date is None:
            bar_date = today
        data = {
            'start_date': '2026-01-01',
            'initial_capital': 200_000,
            'capital': 200_000,
            'positions': {},
            'pending_orders': [{
                'ticker': '3231',
                'entry': limit_price,
                'tp': limit_price * 1.2,
                'sl': limit_price * 0.8,
                'reference_close': limit_price,
                'atr': atr,
                'gap_limit_atr': 1.5,
                'execution_date': None,
                'max_hold_days': 20,
                'position_size': 0.10,
                'regime_scale': 1.0,
                'tp_atr_mult': None,
                'sl_atr_mult': None,
                'tp_pct': None,
                'sl_pct': None,
            }],
            'closed_trades': [],
            'equity_curve': [],
            'daily_signals': [],
            'order_events': [],
        }
        bars = {'3231': {'open': bar_open, 'close': limit_price, 'high': bar_high, 'low': bar_low, 'date': bar_date}}
        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, 'get_current_bars', return_value=bars), \
             mock.patch.object(pt, 'extract_signals_from_report', return_value=[]), \
             mock.patch.object(pt.xcals, 'get_calendar', return_value=mock_cal), \
             mock.patch.object(pt, 'save_data'), \
             mock.patch.object(pt, 'generate_html'):
            pt.update_tracker(data)
        return data

    def test_open_none_cancels_without_close_fallback(self):
        # open 缺漏 → 09:30 撤單，不得以收盤價補開倉
        data = self._run_open(None)
        self.assertNotIn('3231', data['positions'])
        self.assertEqual(data['pending_orders'], [])
        self.assertEqual(data['order_events'][-1]['status'], 'CANCELLED_NO_OPEN_PRICE')

    def test_bar_stale_date_cancels_as_no_open_price(self):
        # bar['date'] 不是 today（幻影成交防護）→ open_price 設為 None，走 CANCELLED_NO_OPEN_PRICE 撤單
        data = self._run_open(149.0, limit_price=150.0, bar_date='2020-01-01')
        self.assertNotIn('3231', data['positions'])
        self.assertEqual(data['pending_orders'], [])
        self.assertEqual(data['order_events'][-1]['status'], 'CANCELLED_NO_OPEN_PRICE')

    def test_non_trading_day_skips_update(self):
        # 非交易日直接 return 不處理
        data = {
            'start_date': '2026-01-01',
            'initial_capital': 200_000,
            'capital': 200_000,
            'positions': {},
            'pending_orders': [{'ticker': '3231', 'entry': 100.0}],
            'closed_trades': [],
            'equity_curve': [],
            'daily_signals': [],
            'order_events': [],
        }
        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = False
        with mock.patch.object(pt.xcals, 'get_calendar', return_value=mock_cal), \
             mock.patch.object(pt, 'get_current_bars') as mock_bars:
            pt.update_tracker(data)
            mock_bars.assert_not_called()
        self.assertEqual(len(data['pending_orders']), 1)
        self.assertEqual(data['order_events'], [])

    def test_open_below_limit_fills_at_open(self):
        # open 149 < limit 150 → 以較佳的 149 成交
        data = self._run_open(149.0, limit_price=150.0)
        pos = data['positions'].get('3231')
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos['entry'], 149.0)
        self.assertEqual(data['order_events'][-1]['status'], 'FILLED')
        self.assertAlmostEqual(data['order_events'][-1]['fill_price'], 149.0)

    def test_open_equal_limit_fills(self):
        # open == limit → 必須成交（含等號）
        data = self._run_open(150.0, limit_price=150.0)
        pos = data['positions'].get('3231')
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos['entry'], 150.0)
        self.assertEqual(data['order_events'][-1]['status'], 'FILLED')

    def test_open_above_limit_cancels_even_if_low_touches_limit(self):
        # open 151 > limit 150 → 09:30 撤單；當日 low 145 觸及限價也不得回推成交
        data = self._run_open(151.0, limit_price=150.0, bar_low=145.0)
        self.assertNotIn('3231', data['positions'])
        self.assertEqual(data['pending_orders'], [])
        self.assertEqual(data['order_events'][-1]['status'], 'CANCELLED_OPEN_ABOVE_LIMIT')
        self.assertIsNone(data['order_events'][-1]['fill_price'])

    def test_small_atr_does_not_block_fill_below_limit(self):
        # ATR 很小、open 130 仍低於 limit 150 → 照樣成交，不受 ATR gap filter 影響
        data = self._run_open(130.0, limit_price=150.0, atr=0.5)
        pos = data['positions'].get('3231')
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos['entry'], 130.0)

    def test_due_order_never_persists_to_pending(self):
        # 不論成交或撤單，執行過的 due order 都不得留在 pending_orders
        for open_price in (None, 149.0, 150.0, 151.0):
            data = self._run_open(open_price)
            self.assertEqual(data['pending_orders'], [])

    def test_invalid_limit_records_cancelled_invalid_limit_event(self):
        # limit <= 0 / nan / inf / None -> 產生 CANCELLED_INVALID_LIMIT 事件，且離開 pending_orders
        today = pt.date.today().isoformat()
        for bad_limit in (0.0, -10.0, float('nan'), float('inf')):
            data = {
                'start_date': '2026-01-01',
                'initial_capital': 200_000,
                'capital': 200_000,
                'positions': {},
                'pending_orders': [{
                    'ticker': '3231',
                    'entry': bad_limit,
                    'limit_price': bad_limit,
                    'tp': 100.0,
                    'sl': 80.0,
                    'reference_close': bad_limit,
                    'atr': 2.0,
                    'gap_limit_atr': 1.5,
                    'execution_date': None,
                    'max_hold_days': 20,
                    'position_size': 0.10,
                    'regime_scale': 1.0,
                }],
                'closed_trades': [],
                'equity_curve': [],
                'daily_signals': [],
                'order_events': [],
            }
            bars = {'3231': {'open': 100.0, 'close': 100.0, 'high': 105.0, 'low': 95.0, 'date': today}}
            mock_cal = mock.MagicMock()
            mock_cal.is_session.return_value = True
            with mock.patch.object(pt, 'get_current_bars', return_value=bars), \
                 mock.patch.object(pt, 'extract_signals_from_report', return_value=[]), \
                 mock.patch.object(pt.xcals, 'get_calendar', return_value=mock_cal), \
                 mock.patch.object(pt, 'save_data'), \
                 mock.patch.object(pt, 'generate_html'):
                pt.update_tracker(data)

            self.assertNotIn('3231', data['positions'])
            self.assertEqual(data['pending_orders'], [])
            self.assertTrue(len(data['order_events']) > 0)
            self.assertEqual(data['order_events'][-1]['status'], 'CANCELLED_INVALID_LIMIT')
            self.assertIsNone(data['order_events'][-1]['fill_price'])

    def test_future_deferred_orders_not_executed_today(self):
        # execution_date > today 的 deferred order 不會在今日執行，且保留在 pending_orders
        today = pt.date.today().isoformat()
        tomorrow = '2099-01-01'
        data = {
            'start_date': '2026-01-01',
            'initial_capital': 200_000,
            'capital': 200_000,
            'positions': {},
            'pending_orders': [{
                'ticker': '3231',
                'entry': 100.0,
                'limit_price': 100.0,
                'tp': 120.0,
                'sl': 80.0,
                'reference_close': 100.0,
                'execution_date': tomorrow,
                'max_hold_days': 20,
                'position_size': 0.10,
                'regime_scale': 1.0,
            }],
            'closed_trades': [],
            'equity_curve': [],
            'daily_signals': [],
            'order_events': [],
        }
        bars = {'3231': {'open': 95.0, 'close': 100.0, 'high': 105.0, 'low': 90.0, 'date': today}}
        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, 'get_current_bars', return_value=bars), \
             mock.patch.object(pt, 'extract_signals_from_report', return_value=[]), \
             mock.patch.object(pt.xcals, 'get_calendar', return_value=mock_cal), \
             mock.patch.object(pt, 'save_data'), \
             mock.patch.object(pt, 'generate_html'):
            pt.update_tracker(data)

        self.assertNotIn('3231', data['positions'])
        self.assertEqual(len(data['pending_orders']), 1)
        self.assertEqual(data['pending_orders'][0]['ticker'], '3231')
        self.assertEqual(data['order_events'], [])

    def test_submission_slots_limits_new_pending_orders(self):
        # 當已有 5 個持倉時，MAX_POSITIONS=7 剩餘 2 個 submission slots，
        # 新進入 4 個信號應只保留前 2 個進入 pending_orders
        today = pt.date.today().isoformat()
        positions = {f"233{i}": {'entry': 100.0, 'tp': 120.0, 'sl': 80.0, 'entry_date': '2026-01-01', 'shares': 100, 'day_count': 1, 'max_hold_days': 20} for i in range(5)}
        data = {
            'start_date': '2026-01-01',
            'initial_capital': 500_000,
            'capital': 200_000,
            'positions': positions,
            'pending_orders': [],
            'closed_trades': [],
            'equity_curve': [],
            'daily_signals': [],
            'order_events': [],
        }
        new_signals = [
            {'ticker': f'300{i}', 'entry': 50.0, 'limit_price': 50.0, 'tp': 60.0, 'sl': 40.0, 'execution_date': '2099-01-02', 'rank': i+1}
            for i in range(4)
        ]
        bars = {f"233{i}": {'open': 100.0, 'close': 100.0, 'high': 105.0, 'low': 95.0, 'date': today} for i in range(5)}
        mock_cal = mock.MagicMock()
        mock_cal.is_session.return_value = True
        with mock.patch.object(pt, 'get_current_bars', return_value=bars), \
             mock.patch.object(pt, 'extract_signals_from_report', return_value=new_signals), \
             mock.patch.object(pt.xcals, 'get_calendar', return_value=mock_cal), \
             mock.patch.object(pt, 'save_data'), \
             mock.patch.object(pt, 'generate_html'):
            pt.update_tracker(data)

        # 7 - 5 = 2 slots -> pending_orders 應該只有 2 筆
        self.assertEqual(len(data['pending_orders']), 2)
        self.assertEqual([o['ticker'] for o in data['pending_orders']], ['3000', '3001'])


if __name__ == '__main__':
    unittest.main()


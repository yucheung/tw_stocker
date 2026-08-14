#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_tracker.py edge case 加固單元測試（Codex review B1 / A1 / A2 / A3）

執行方式：
    python3 -m unittest test_paper_tracker_edge_cases -v
"""
import unittest
from unittest import mock

import paper_tracker as pt


class TestRecomputeTpSl(unittest.TestCase):
    """A1: NaN/無效 ATR 防護 + A2: 合法 0 值不被 or 覆寫 + A3: SL ≤ 0 sanity check"""

    def test_atr_none_fallback(self):
        # A1: ATR 為 None → 退回百分比 fallback（100 × 1.20 / 100 × 0.80）
        tp, sl = pt.recompute_tp_sl({}, 100.0, None)
        self.assertAlmostEqual(tp, 120.0)
        self.assertAlmostEqual(sl, 80.0)

    def test_atr_nan_fallback(self):
        # A1: ATR 為 NaN → 必須退回百分比 fallback
        #     （NaN <= 0 恆為 False，舊碼 `atr_val <= 0` 擋不住，會產生 NaN 價位）
        tp, sl = pt.recompute_tp_sl({}, 100.0, float('nan'))
        self.assertAlmostEqual(tp, 120.0)
        self.assertAlmostEqual(sl, 80.0)

    def test_atr_zero_and_negative_fallback(self):
        # A1: ATR = 0 或負值 → 退回百分比 fallback
        for bad_atr in (0.0, -3.0):
            tp, sl = pt.recompute_tp_sl({}, 100.0, bad_atr)
            self.assertAlmostEqual(tp, 120.0)
            self.assertAlmostEqual(sl, 80.0)

    def test_tp_sl_pct_zero_not_overridden(self):
        # A2: tp_pct / sl_pct = 0.0 是合法值，不應被 `or default` 覆寫成 0.20
        tp, sl = pt.recompute_tp_sl({'tp_pct': 0.0, 'sl_pct': 0.0}, 100.0, None)
        self.assertAlmostEqual(tp, 100.0)
        self.assertAlmostEqual(sl, 100.0)

    def test_atr_mult_zero_not_overridden(self):
        # A2: tp_atr_mult / sl_atr_mult = 0.0 不應被覆寫成預設 4.0 / 2.0
        order = {'tp_atr_mult': 0.0, 'sl_atr_mult': 0.0}
        tp, sl = pt.recompute_tp_sl(order, 100.0, 5.0)
        self.assertAlmostEqual(tp, 100.0)
        self.assertAlmostEqual(sl, 100.0)

    def test_huge_atr_sl_sanity(self):
        # A3: ATR 過大使 SL 為負（100 - 1000×2 = -1900）→ 退回百分比 fallback 80.0
        tp, sl = pt.recompute_tp_sl({}, 100.0, 1000.0)
        self.assertGreater(tp, 100.0)
        self.assertAlmostEqual(sl, 80.0)
        self.assertGreater(sl, 0)

    def test_sl_pct_ge_one_forced_back(self):
        # A3 保險: sl_pct = 1.0 使 SL = 0（非正）→ 強制退回 20%
        tp, sl = pt.recompute_tp_sl({'sl_pct': 1.0}, 100.0, None)
        self.assertAlmostEqual(sl, 80.0)
        self.assertGreater(sl, 0)

    def test_normal_atr_path_unchanged(self):
        # 正常 ATR 路徑不應被破壞：tp = 100 + 5×4 = 120, sl = 100 - 5×2 = 90
        tp, sl = pt.recompute_tp_sl({}, 100.0, 5.0)
        self.assertAlmostEqual(tp, 120.0)
        self.assertAlmostEqual(sl, 90.0)


class TestOpenPriceNone(unittest.TestCase):
    """B1: bar 存在但 open = None → 開倉不 crash，退回 entry_price"""

    def _run_open(self, bar_open):
        data = {
            'start_date': '2026-01-01',
            'initial_capital': 200_000,
            'capital': 200_000,
            'positions': {},
            'pending_orders': [{
                'ticker': '3231',
                'entry': 150.0,
                'tp': 180.0,
                'sl': 120.0,
                'reference_close': 150.0,
                'atr': None,
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
        }
        bars = {'3231': {'open': bar_open, 'close': 150.0, 'high': 155.0, 'low': 148.0}}
        with mock.patch.object(pt, 'get_current_bars', return_value=bars), \
             mock.patch.object(pt, 'extract_signals_from_report', return_value=[]), \
             mock.patch.object(pt, 'save_data'), \
             mock.patch.object(pt, 'generate_html'):
            pt.update_tracker(data)
        return data

    def test_open_none_uses_entry(self):
        # B1: open=None → 以 entry_price（收盤 150）開倉，不 crash
        data = self._run_open(None)
        pos = data['positions'].get('3231')
        self.assertIsNotNone(pos, 'open=None 時仍應成功開倉')
        self.assertAlmostEqual(pos['entry'], 150.0)
        # TP/SL 以 150 為錨（ATR=None → 百分比 fallback）
        self.assertAlmostEqual(pos['tp'], 180.0)
        self.assertAlmostEqual(pos['sl'], 120.0)

    def test_open_valid_used(self):
        # 對照組: open 正常時以 open 為錨
        data = self._run_open(151.5)
        pos = data['positions']['3231']
        self.assertAlmostEqual(pos['entry'], 151.5)
        self.assertAlmostEqual(pos['tp'], 151.5 * 1.2)
        self.assertAlmostEqual(pos['sl'], 151.5 * 0.8)


if __name__ == '__main__':
    unittest.main()

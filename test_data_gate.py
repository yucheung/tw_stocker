"""P0-1 資料完整性閘門測試。"""

import numpy as np
import pandas as pd
import pytest

from ai_report import enforce_data_integrity


def _panel(n_tickers, n_rows=400, valid=None):
    """建立收盤價矩陣；只有前 `valid` 檔有資料，其餘全 NaN。"""
    valid = n_tickers if valid is None else valid
    idx = pd.bdate_range('2024-01-01', periods=n_rows)
    cols = [f'T{i}' for i in range(n_tickers)]
    df = pd.DataFrame(np.nan, index=idx, columns=cols)
    df.iloc[:, :valid] = 100.0
    return df


def test_full_coverage_passes():
    df = _panel(100)
    enforce_data_integrity(df, list(df.columns))


def test_degraded_download_raises():
    # 116 檔請求，只回來 1 檔 —— 就是 production 靜默故障的情境
    tickers = [f'T{i}' for i in range(116)]
    df = _panel(116, valid=1)
    with pytest.raises(RuntimeError, match='資料完整性不足'):
        enforce_data_integrity(df, tickers)


def test_boundary_at_coverage_threshold():
    tickers = [f'T{i}' for i in range(100)]
    enforce_data_integrity(_panel(100, valid=70), tickers)          # 剛好 70%
    with pytest.raises(RuntimeError):
        enforce_data_integrity(_panel(100, valid=69), tickers)      # 69% 擋下


def test_empty_download_raises():
    with pytest.raises(RuntimeError):
        enforce_data_integrity(pd.DataFrame(), ['2330'])


def test_short_window_not_falsely_rejected():
    """走短窗口（walk-forward fold）時，min_bars 應自動下修而非全數判定無效。"""
    df = _panel(50, n_rows=40)
    enforce_data_integrity(df, list(df.columns))


def test_universe_degradation_raises():
    df = _panel(100)
    mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    mask.iloc[:, :10] = True                       # 平均每日只有 10 檔
    with pytest.raises(RuntimeError, match='Universe 退化'):
        enforce_data_integrity(df, list(df.columns), universe_mask=mask,
                               universe_size=60)


def test_healthy_universe_passes():
    df = _panel(100)
    mask = pd.DataFrame(False, index=df.index, columns=df.columns)
    mask.iloc[:, :60] = True
    enforce_data_integrity(df, list(df.columns), universe_mask=mask,
                           universe_size=60)


def test_static_pool_skips_universe_check():
    df = _panel(1)
    enforce_data_integrity(df, ['3231'], universe_mask=None, universe_size=None)

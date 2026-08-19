import numpy as np
import pandas as pd
import pytest

from strategy.event_backtest import EventDrivenBacktester

WARMUP_DAYS = 60
BASE_PRICE = 50.0
SIGNAL_CLOSE = 100.0
TICKER = "2330"


def _build_single_ticker_frames(next_open, next_low, atr=None):
    n_days = WARMUP_DAYS + 3  # 0..59 warmup, 60 signal, 61 execution, 62 forced time-exit
    dates = pd.bdate_range("2024-01-01", periods=n_days)

    close = pd.DataFrame(BASE_PRICE, index=dates, columns=[TICKER])
    open_ = pd.DataFrame(BASE_PRICE, index=dates, columns=[TICKER])
    high = pd.DataFrame(BASE_PRICE * 1.01, index=dates, columns=[TICKER])
    low = pd.DataFrame(BASE_PRICE * 0.99, index=dates, columns=[TICKER])
    score = pd.DataFrame(0.0, index=dates, columns=[TICKER])
    ma60 = pd.DataFrame(BASE_PRICE, index=dates, columns=[TICKER])

    signal_idx = WARMUP_DAYS
    exec_idx = WARMUP_DAYS + 1
    exit_idx = WARMUP_DAYS + 2

    close.loc[dates[signal_idx], TICKER] = SIGNAL_CLOSE
    score.loc[dates[signal_idx], TICKER] = 3.0
    ma60.loc[dates[signal_idx], TICKER] = BASE_PRICE  # close(100) > ma60(50)

    exec_date = dates[exec_idx]
    open_.loc[exec_date, TICKER] = np.nan if next_open is None else next_open
    low.loc[exec_date, TICKER] = next_low
    settle_close = SIGNAL_CLOSE if next_open is None else next_open
    high.loc[exec_date, TICKER] = max(settle_close, SIGNAL_CLOSE) * 1.02
    close.loc[exec_date, TICKER] = settle_close

    exit_date = dates[exit_idx]
    close.loc[exit_date, TICKER] = settle_close
    open_.loc[exit_date, TICKER] = settle_close
    high.loc[exit_date, TICKER] = settle_close * 1.01
    low.loc[exit_date, TICKER] = settle_close * 0.99

    atr_df = pd.DataFrame(atr, index=dates, columns=[TICKER]) if atr is not None else None

    return close, open_, high, low, score, ma60, atr_df


@pytest.fixture
def run_case():
    def _run(next_open, next_low, atr=None, slippage=0.01, gap_filter_atr=1.5):
        close, open_, high, low, score, ma60, atr_df = _build_single_ticker_frames(
            next_open, next_low, atr=atr
        )
        bt = EventDrivenBacktester(
            max_hold_days=1, position_size=0.5, tp_sl_mode="fixed",
            tp_pct=0.50, sl_pct=0.50, slippage=slippage,
            regime_filter=False, gap_filter_atr=gap_filter_atr,
        )
        trades, equity = bt.run(
            score, close, open_, high, low, ma60,
            top_k=3, threshold=2.0, atr_df=atr_df,
        )
        return trades, equity

    return _run


def test_gap_down_fills_at_better_open(run_case):
    trades, _ = run_case(next_open=95.0, next_low=90.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(95.0)


def test_equal_open_fills_at_limit(run_case):
    trades, _ = run_case(next_open=100.0, next_low=99.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(100.0)


def test_gap_up_does_not_fill_even_if_daily_low_touches_limit(run_case):
    trades, _ = run_case(next_open=105.0, next_low=95.0)
    assert trades.empty


def test_large_gap_down_is_not_rejected_by_old_atr_gap_filter(run_case):
    trades, _ = run_case(next_open=70.0, next_low=65.0, atr=5.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(70.0)


def test_missing_next_open_cancels_without_close_fallback(run_case):
    trades, _ = run_case(next_open=None, next_low=90.0)
    assert trades.empty


def test_three_candidates_two_slots_no_backfill_after_gap_up():
    n_days = WARMUP_DAYS + 3
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    tickers = ["T1", "T2", "T3"]

    close = pd.DataFrame(BASE_PRICE, index=dates, columns=tickers)
    open_ = pd.DataFrame(BASE_PRICE, index=dates, columns=tickers)
    high = pd.DataFrame(BASE_PRICE * 1.01, index=dates, columns=tickers)
    low = pd.DataFrame(BASE_PRICE * 0.99, index=dates, columns=tickers)
    score = pd.DataFrame(0.0, index=dates, columns=tickers)
    ma60 = pd.DataFrame(BASE_PRICE, index=dates, columns=tickers)

    signal_idx = WARMUP_DAYS
    exec_idx = WARMUP_DAYS + 1
    exit_idx = WARMUP_DAYS + 2
    exec_date = dates[exec_idx]
    exit_date = dates[exit_idx]

    close.loc[dates[signal_idx], tickers] = SIGNAL_CLOSE
    ma60.loc[dates[signal_idx], tickers] = BASE_PRICE
    score.loc[dates[signal_idx], "T1"] = 5.0  # rank 1: will gap up -> cancelled
    score.loc[dates[signal_idx], "T2"] = 4.0  # rank 2: gaps down -> filled
    score.loc[dates[signal_idx], "T3"] = 3.0  # rank 3: excluded by slot cap, never submitted

    next_opens = {"T1": 110.0, "T2": 95.0, "T3": 60.0}
    for t in tickers:
        o = next_opens[t]
        open_.loc[exec_date, t] = o
        low.loc[exec_date, t] = o * 0.98
        high.loc[exec_date, t] = max(o, SIGNAL_CLOSE) * 1.02
        close.loc[exec_date, t] = o

        close.loc[exit_date, t] = o
        open_.loc[exit_date, t] = o
        high.loc[exit_date, t] = o * 1.01
        low.loc[exit_date, t] = o * 0.99

    bt = EventDrivenBacktester(
        max_hold_days=1, position_size=0.5, tp_sl_mode="fixed",
        tp_pct=0.50, sl_pct=0.50, slippage=0.0,
        regime_filter=False, gap_filter_atr=0,
    )
    trades, _ = bt.run(score, close, open_, high, low, ma60, top_k=3, threshold=2.0)

    assert set(trades["Ticker"]) == {"T2"}

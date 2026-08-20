#!/usr/bin/env python3
"""MR20 順勢回檔策略 (Trend Pullback Mean Reversion Strategy) — v1.0

Design & Architecture: PLAN_new_strategy.md

篩選核心邏輯（5 大條件）：
1. 流動性：過去 20 日平均成交額位於全市場前 50 名 (Close * Volume 20MA)
2. 中期多頭：MA20 > MA60 且 Close > MA60
3. 已回檔：Close < MA20
4. 短線超賣：RSI(5) <= 35 (Wilder 算法)
5. 止跌確認：Close[t] > Close[t-1]

排名規則：
score = 100 - RSI(5)，依 score 由高到低排序（即 RSI 越低越優先），
同分時依 20 日平均成交額排序，再相同則以股票代號由小到大排序。每日輸出前 7 名。

訂單產出直接相容於 independent_sim.py schema v1。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# 嘗試載入 exchange_calendars
try:
    import exchange_calendars as xcals
    TW_CALENDAR = xcals.get_calendar("XTAI")
    HAS_EXCHANGE_CAL = True
except ImportError:
    TW_CALENDAR = None
    HAS_EXCHANGE_CAL = False

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class MR20Config:
    """MR20 順勢回檔策略參數設定。"""

    liquidity_top_n: int = 50
    liquidity_lookback: int = 20
    ma_short: int = 20
    ma_long: int = 60
    min_history_days: int = 60
    rsi_period: int = 5
    rsi_max: float = 40.0
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    ma20_dist_min: Optional[float] = None
    ma20_dist_max: Optional[float] = None
    max_candidates: int = 7
    tp_atr_mult: float = 4.0
    sl_atr_mult: float = 3.0
    max_hold_days: int = 20
    position_size: float = 0.45
    entry_model: str = "signal_close_limit_next_open_v1"
    time_in_force: str = "DAY_UNTIL_0930"
    cancel_time: str = "09:30:00"
    timezone: str = "Asia/Taipei"
    model_version: str = "mr20_pullback_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =====================================================================
# Calendar Helpers
# =====================================================================

def get_calendar(name: str = "XTAI") -> Optional[xcals.ExchangeCalendar]:
    """取得交易日曆。"""
    if HAS_EXCHANGE_CAL:
        return xcals.get_calendar(name)
    return None


def is_trading_day(dt_str: str, calendar: Optional[xcals.ExchangeCalendar] = None) -> bool:
    """判斷指定日期 (YYYY-MM-DD) 是否為有效 XTAI 交易日。"""
    cal = calendar or get_calendar("XTAI")
    if cal is not None:
        try:
            return bool(cal.is_session(dt_str))
        except Exception:
            return False
    try:
        ts = pd.Timestamp(dt_str)
        return ts.weekday() < 5
    except Exception:
        return False


def get_next_trading_day(dt_str: str, calendar: Optional[xcals.ExchangeCalendar] = None) -> str:
    """計算 dt_str 後的次一有效交易日。"""
    cal = calendar or get_calendar("XTAI")
    if cal is not None:
        try:
            if cal.is_session(dt_str):
                nxt = cal.next_session(dt_str)
            else:
                nxt = cal.date_to_session(dt_str, direction="next")
            return nxt.strftime("%Y-%m-%d")
        except Exception:
            pass

    # Fallback: 跳過週末的次一營業日近似
    cur = pd.Timestamp(dt_str)
    nxt = cur + timedelta(days=1)
    while nxt.weekday() >= 5:  # 5: Sat, 6: Sun
        nxt += timedelta(days=1)
    return nxt.strftime("%Y-%m-%d")


# =====================================================================
# Technical Indicators
# =====================================================================

def compute_wilder_rsi(prices: pd.Series | pd.DataFrame, period: int = 5) -> pd.Series | pd.DataFrame:
    """
    計算標準 Wilder's 平滑 RSI (Relative Strength Index)。

    算法規則 (P0-1 & P2-2)：
    - 保留 NaN，只在有連續有效資料時計算 RSI，NaN 區間的 RSI 應為 NaN（不計算，避免停牌/資料缺口被當平盤）
    - 連續有效資料區間的前 period 期以 SMA 初始化 (平均上漲 avg_gain 與平均下跌 avg_loss)
    - 後續期數採 Wilder 平滑：
        avg_gain[t] = (avg_gain[t-1] * (period - 1) + gain[t]) / period
        avg_loss[t] = (avg_loss[t-1] * (period - 1) + loss[t]) / period
    - RS = avg_gain / avg_loss
    - RSI = 100 - 100 / (1 + RS)

    Parameters
    ----------
    prices : pd.Series or pd.DataFrame
        價格序列或 DataFrame
    period : int
        RSI 週期（預設 5）

    Returns
    -------
    rsi : pd.Series or pd.DataFrame
        RSI 指標 (0 ~ 100)
    """
    is_series = isinstance(prices, pd.Series)
    df_in = prices.to_frame() if is_series else prices

    vals = df_in.to_numpy(dtype=float)
    num_rows, num_cols = vals.shape
    rsi_out = np.full((num_rows, num_cols), np.nan, dtype=float)

    if num_rows <= period:
        if is_series:
            return pd.Series(rsi_out[:, 0], index=prices.index, name=prices.name)
        return pd.DataFrame(rsi_out, index=prices.index, columns=prices.columns)

    def _to_rsi(g: float, l: float) -> float:
        if np.isnan(g) or np.isnan(l):
            return np.nan
        if l == 0.0 and g == 0.0:
            return 50.0
        if l == 0.0:
            return 100.0
        if g == 0.0:
            return 0.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    for col_idx in range(num_cols):
        col_data = vals[:, col_idx]
        valid_mask = ~np.isnan(col_data)
        if not np.any(valid_mask):
            continue

        # 找出連續非 NaN 資料段 (contiguous valid segments)
        d = np.diff(valid_mask.astype(np.int8))
        starts = np.where(d == 1)[0] + 1
        ends = np.where(d == -1)[0] + 1

        if valid_mask[0]:
            starts = np.r_[0, starts]
        if valid_mask[-1]:
            ends = np.r_[ends, num_rows]

        for start, end in zip(starts, ends):
            seg_len = end - start
            if seg_len <= period:
                continue

            seg_vals = col_data[start:end]
            diffs = np.diff(seg_vals)
            gains = np.where(diffs > 0, diffs, 0.0)
            losses = np.where(diffs < 0, -diffs, 0.0)

            # 1. 前 period 筆 diffs 以 SMA 初始化
            ag = float(np.mean(gains[:period]))
            al = float(np.mean(losses[:period]))
            rsi_out[start + period, col_idx] = _to_rsi(ag, al)

            # 2. 後續以 Wilder Smoothing 平滑
            for i in range(period + 1, seg_len):
                cg = gains[i - 1]
                cl = losses[i - 1]
                ag = (ag * (period - 1) + cg) / period
                al = (al * (period - 1) + cl) / period
                rsi_out[start + i, col_idx] = _to_rsi(ag, al)

    if is_series:
        return pd.Series(rsi_out[:, 0], index=prices.index, name=prices.name)
    return pd.DataFrame(rsi_out, index=prices.index, columns=prices.columns)


def compute_v85_atr20(close: pd.Series | pd.DataFrame, period: int = 20) -> pd.Series | pd.DataFrame:
    """
    計算 tw_stocker v8.5 close-based 20 日 ATR。
    ATR20 = mean(abs(Close.pct_change()), 20) * Close
    """
    pct_changes = close.pct_change().abs()
    mean_pct = pct_changes.rolling(period).mean()
    return mean_pct * close


def compute_mr20_indicators(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    config: Optional[MR20Config] = None,
) -> dict[str, pd.DataFrame]:
    """計算 MR20 策略所需的全部指標。"""
    cfg = config or MR20Config()

    ma_short = close_df.rolling(cfg.ma_short).mean()
    ma_long = close_df.rolling(cfg.ma_long).mean()
    rsi = compute_wilder_rsi(close_df, period=cfg.rsi_period)
    atr = compute_v85_atr20(close_df, period=20)
    turnover = (close_df * vol_df).rolling(cfg.liquidity_lookback).mean()

    return {
        "ma_short": ma_short,
        "ma_long": ma_long,
        "rsi": rsi,
        "atr": atr,
        "turnover": turnover,
    }


# =====================================================================
# Filtering & Ranking Logic
# =====================================================================

def filter_mr20_candidates(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    as_of_date: Optional[str] = None,
    config: Optional[MR20Config] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
) -> list[dict[str, Any]]:
    """
    依據 5 大核心條件篩選與排名 MR20 候選個股。

    Parameters
    ----------
    close_df : pd.DataFrame
        歷史收盤價矩陣 (日期 x 股票代號)
    vol_df : pd.DataFrame
        歷史成交量矩陣 (日期 x 股票代號)
    as_of_date : str, optional
        訊號日期 (YYYY-MM-DD)，若未提供則預設為 close_df 最後一筆日期
    config : MR20Config, optional
        策略參數設定
    calendar : xcals.ExchangeCalendar, optional
        交易日曆

    Returns
    -------
    candidates : list[dict]
        符合條件並依 score 排序的候選個股列表（最多 max_candidates 檔）
    """
    cfg = config or MR20Config()

    if close_df.empty or vol_df.empty:
        return []

    cal = calendar or get_calendar("XTAI")

    if as_of_date is not None:
        # P0-5: 非交易日處理：非 XTAI session 應拒絕，不靜默降級
        if cal is not None and not cal.is_session(as_of_date):
            raise ValueError(f"Specified as-of date '{as_of_date}' is not a valid XTAI trading session.")
        target_dt = pd.Timestamp(as_of_date)
        if target_dt not in close_df.index:
            raise ValueError(f"As-of date '{as_of_date}' not found in price data index.")
    else:
        target_dt = close_df.index[-1]
        as_of_date = target_dt.strftime("%Y-%m-%d")
        if cal is not None and not cal.is_session(as_of_date):
            raise ValueError(f"Latest price date '{as_of_date}' is not a valid XTAI trading session.")

    t_loc = close_df.index.get_loc(target_dt)
    if isinstance(t_loc, (slice, np.ndarray)):
        t_loc = t_loc[-1] if hasattr(t_loc, "__len__") else t_loc.stop - 1

    # 至少需要前一日資料以確認止跌 (t_loc >= 1)
    if t_loc < 1:
        return []

    # P0-3: 先建立 min_history_days (預設 60 日) 歷史門檻的 universe，再做 Top-50 流動性排序
    eligible_tickers = []
    for ticker in close_df.columns:
        series_up_to_t = close_df[ticker].iloc[: t_loc + 1].dropna()
        if len(series_up_to_t) >= cfg.min_history_days:
            eligible_tickers.append(ticker)

    if not eligible_tickers:
        return []

    indicators = compute_mr20_indicators(close_df, vol_df, config=cfg)
    ma_short_df = indicators["ma_short"]
    ma_long_df = indicators["ma_long"]
    rsi_df = indicators["rsi"]
    atr_df = indicators["atr"]
    turnover_df = indicators["turnover"]

    # 條件 1: 在符合 60 日歷史門檻的 universe 內，取 20 日平均成交額 Top-N
    turnover_series = turnover_df.loc[target_dt, eligible_tickers].dropna()
    valid_turnovers = turnover_series[turnover_series > 0]
    if valid_turnovers.empty:
        return []

    top_n_thresh = min(cfg.liquidity_top_n, len(valid_turnovers))
    top_liquid_tickers = set(
        valid_turnovers.nlargest(top_n_thresh).index
    )

    passed_candidates = []

    for ticker in top_liquid_tickers:
        c_t = close_df[ticker].iloc[t_loc]
        c_prev = close_df[ticker].iloc[t_loc - 1]
        ma_s = ma_short_df[ticker].iloc[t_loc]
        ma_l = ma_long_df[ticker].iloc[t_loc]
        rsi_val = rsi_df[ticker].iloc[t_loc]
        atr_val = atr_df[ticker].iloc[t_loc]
        turnover_val = turnover_df[ticker].iloc[t_loc]

        if pd.isna(c_t) or pd.isna(c_prev) or pd.isna(ma_s) or pd.isna(ma_l) or pd.isna(rsi_val):
            continue

        # 條件 2: 中期多頭 (MA20 > MA60 且 Close > MA60)
        if not (ma_s > ma_l and c_t > ma_l):
            continue

        # 條件 3: 已回檔 (Close < MA20)
        if not (c_t < ma_s):
            continue

        # 條件 4: 短線超賣 (RSI(5) <= rsi_max)
        if not (rsi_val <= cfg.rsi_max):
            continue

        # 條件 5: 止跌確認 (Close[t] > Close[t-1])
        if not (c_t > c_prev):
            continue

        # 可選價格區間過濾
        if cfg.min_price is not None and c_t < cfg.min_price:
            continue
        if cfg.max_price is not None and c_t > cfg.max_price:
            continue

        # 可選 MA20 距離區間過濾
        ma20_dist = (c_t - ma_s) / ma_s
        if cfg.ma20_dist_min is not None and ma20_dist < cfg.ma20_dist_min:
            continue
        if cfg.ma20_dist_max is not None and ma20_dist > cfg.ma20_dist_max:
            continue

        score = 100.0 - float(rsi_val)
        passed_candidates.append({
            "ticker": str(ticker),
            "score": score,
            "rsi": float(rsi_val),
            "turnover": float(turnover_val),
            "close": float(c_t),
            "atr": float(atr_val) if not pd.isna(atr_val) else 0.0,
            "ma20": float(ma_s),
            "ma60": float(ma_l),
            "ma20_dist": float(ma20_dist),
        })

    # 排序規則：
    # 1. score 由高到低 (即 RSI 越低越優先)
    # 2. turnover 由大到小
    # 3. ticker 由小到大
    passed_candidates.sort(
        key=lambda x: (-x["score"], -x["turnover"], x["ticker"])
    )

    # 取前 max_candidates 檔並標註 rank (1-indexed)
    selected = passed_candidates[: cfg.max_candidates]
    for idx, cand in enumerate(selected, 1):
        cand["rank"] = idx

    return selected


# =====================================================================
# Order Generation & Artifacts
# =====================================================================

def build_mr20_order_record(
    signal_date: str,
    execution_date: str,
    ticker: str,
    rank: int,
    score: float,
    reference_close: float,
    atr: float,
    config: Optional[MR20Config] = None,
) -> dict[str, Any]:
    """建立符合 independent_sim.py 規格之 MR20 單筆委託字典。"""
    cfg = config or MR20Config()
    ref_close = round(float(reference_close), 4)
    atr_val = round(float(atr), 4)
    tp_price = round(ref_close + cfg.tp_atr_mult * atr_val, 4)
    sl_price = round(ref_close - cfg.sl_atr_mult * atr_val, 4)

    return {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "ticker": ticker,
        "side": "buy",
        "order_type": "limit",
        "limit_price": ref_close,
        "reference_close": ref_close,
        "entry_model": cfg.entry_model,
        "time_in_force": cfg.time_in_force,
        "cancel_time": cfg.cancel_time,
        "timezone": cfg.timezone,
        "rank": rank,
        "score": round(float(score), 4),
        "atr": atr_val,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "tp_atr_mult": float(cfg.tp_atr_mult),
        "sl_atr_mult": float(cfg.sl_atr_mult),
        "max_hold_days": int(cfg.max_hold_days),
        "position_size": float(cfg.position_size),
        "tp_sl_mode": "atr",
        "model_version": cfg.model_version,
    }


def generate_mr20_orders(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    signal_date: Optional[str] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
    config: Optional[MR20Config] = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    針對指定訊號日產生 MR20 訂單結構。

    Returns
    -------
    dict: {"orders": [order_1, order_2, ...]}
    """
    cfg = config or MR20Config()
    cal = calendar or get_calendar("XTAI")

    if signal_date is None:
        if close_df.empty:
            return {"orders": []}
        signal_date = close_df.index[-1].strftime("%Y-%m-%d")

    # P0-5: 非交易日處理：非 XTAI session 應拒絕，不靜默降級
    if cal is not None and not cal.is_session(signal_date):
        raise ValueError(f"Specified signal date '{signal_date}' is not a valid XTAI trading session.")

    candidates = filter_mr20_candidates(close_df, vol_df, as_of_date=signal_date, config=cfg, calendar=cal)
    exec_date = get_next_trading_day(signal_date, calendar=cal)

    orders = []
    for cand in candidates:
        order = build_mr20_order_record(
            signal_date=signal_date,
            execution_date=exec_date,
            ticker=cand["ticker"],
            rank=cand["rank"],
            score=cand["score"],
            reference_close=cand["close"],
            atr=cand["atr"],
            config=cfg,
        )
        orders.append(order)

    return {"orders": orders}


def save_orders_to_json(orders_dict: dict[str, Any], file_path: Path | str) -> Path:
    """將訂單存成 JSON 檔案。"""
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(orders_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


# =====================================================================
# CLI Entry Point
# =====================================================================

def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="MR20 順勢回檔策略 (Trend Pullback Mean Reversion) 選股與訂單產生器"
    )
    parser.add_argument(
        "--as-of",
        "--signal-date",
        dest="as_of",
        type=str,
        default=None,
        help="訊號基準日期 (YYYY-MM-DD)，預設為最新交易日",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="指定回測或選股個股代號清單（若未指定則自動載入全體上市普通股）",
    )
    parser.add_argument(
        "--pool",
        choices=["full", "legacy"],
        default="full",
        help="股票池來源：'full' 為 TWSE 上市普通股清單，'legacy' 為舊版清單",
    )
    parser.add_argument(
        "--output",
        "--orders-out",
        dest="output",
        type=str,
        default=None,
        help="訂單 JSON 輸出路徑 (例如: artifacts/mr20/orders_mr20_20260819.json)",
    )
    parser.add_argument(
        "--top-n",
        dest="top_n",
        type=int,
        default=50,
        help="流動性 Universe 大小 (預設 50)",
    )
    parser.add_argument(
        "--rsi-max",
        dest="rsi_max",
        type=float,
        default=35.0,
        help="RSI(5) 超賣上限門檻 (預設 35.0)",
    )
    parser.add_argument(
        "--max-candidates",
        dest="max_candidates",
        type=int,
        default=7,
        help="每日最多輸出候選筆數 (預設 7)",
    )
    parser.add_argument(
        "--min-price",
        dest="min_price",
        type=float,
        default=None,
        help="最低股價門檻 (可選)",
    )
    parser.add_argument(
        "--max-price",
        dest="max_price",
        type=float,
        default=None,
        help="最高股價門檻 (可選)",
    )
    parser.add_argument(
        "--dist-ma20-min",
        dest="dist_ma20_min",
        type=float,
        default=None,
        help="距離 MA20 最小乖離率 (例如: -0.15 表示 -15%%)",
    )
    parser.add_argument(
        "--dist-ma20-max",
        dest="dist_ma20_max",
        type=float,
        default=None,
        help="距離 MA20 最大乖離率 (例如: -0.05 表示 -5%%)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="independent_sim_data_mr20",
        help="independent_sim.py 資料目錄 (預設 independent_sim_data_mr20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="僅輸出篩選結果，不寫入檔案",
    )
    return parser.parse_args(args)


def main():
    args = parse_args()
    cfg = MR20Config(
        liquidity_top_n=args.top_n,
        rsi_max=args.rsi_max,
        max_candidates=args.max_candidates,
        min_price=args.min_price,
        max_price=args.max_price,
        ma20_dist_min=args.dist_ma20_min,
        ma20_dist_max=args.dist_ma20_max,
    )

    cal = get_calendar("XTAI")

    # 處理訊號日期
    if args.as_of:
        signal_date = args.as_of
        # P0-5: 非交易日處理：非 XTAI session 應拒絕，不靜默降級
        if cal is not None and not cal.is_session(signal_date):
            raise ValueError(f"Specified as-of date '{signal_date}' is not a valid XTAI trading session.")
        elif cal is None and pd.Timestamp(signal_date).weekday() >= 5:
            raise ValueError(f"Specified as-of date '{signal_date}' is not a valid trading day (weekend).")
    else:
        # 若未指定，以台北時間判斷最新交易日 (P2-1)
        now_taipei = datetime.now(TAIPEI_TZ)
        today_str = now_taipei.strftime("%Y-%m-%d")
        if cal is not None:
            if cal.is_session(today_str) and now_taipei.hour >= 14:
                signal_date = today_str
            elif cal.is_session(today_str):
                signal_date = cal.previous_session(today_str).strftime("%Y-%m-%d")
            else:
                signal_date = cal.date_to_session(today_str, direction="previous").strftime("%Y-%m-%d")
        else:
            cur = pd.Timestamp(today_str)
            if cur.weekday() < 5 and now_taipei.hour >= 14:
                signal_date = today_str
            else:
                prev = cur - timedelta(days=1) if cur.weekday() < 5 else cur
                while prev.weekday() >= 5:
                    prev -= timedelta(days=1)
                signal_date = prev.strftime("%Y-%m-%d")

    # P0-2: yfinance 日期邊界
    # yfinance 的 end 參數不包含指定日期。改為：end = signal_date + 1 day（多抓一天）
    end_fetch_dt = pd.Timestamp(signal_date) + timedelta(days=1)
    end_date_str = end_fetch_dt.strftime("%Y-%m-%d")

    # 載入股池與歷史資料
    from strategy.ai_strategy import fetch_panel_data
    from strategy.universe import get_twse_common_stocks

    if args.tickers:
        tickers = args.tickers
    elif args.pool == "full":
        tickers = get_twse_common_stocks()
    else:
        from ai_report import LEGACY_EXTENDED_TICKERS
        tickers = LEGACY_EXTENDED_TICKERS

    print(f"🎯 MR20 順勢回檔策略 (股池規模: {len(tickers)} 檔, 訊號日: {signal_date})...")
    close_df, open_df, high_df, low_df, vol_df = fetch_panel_data(
        tickers, days=180, end_date=end_date_str
    )

    # 確保資料切齊至 signal_date
    target_dt = pd.Timestamp(signal_date)
    if target_dt in close_df.index:
        close_df = close_df.loc[:target_dt]
        vol_df = vol_df.loc[:target_dt]

    # P0-2: 確認最後一筆資料的日期 == signal_date
    if close_df.empty or close_df.index[-1] != target_dt:
        actual_last = close_df.index[-1].strftime("%Y-%m-%d") if not close_df.empty else "None"
        raise ValueError(
            f"Fetched data last date ({actual_last}) does not match expected signal date ({signal_date})."
        )

    orders_dict = generate_mr20_orders(close_df, vol_df, signal_date=signal_date, calendar=cal, config=cfg)
    orders = orders_dict["orders"]

    print(f"\n📊 [{signal_date}] MR20 選股結果 (共 {len(orders)} 檔入選):")
    for o in orders:
        print(
            f"  #{o['rank']} 代號: {o['ticker']:<6} 分數: {o['score']:.2f} "
            f"收盤/限價: {o['reference_close']:.2f} ATR: {o['atr']:.2f} "
            f"TP: {o['tp_price']:.2f} SL: {o['sl_price']:.2f}"
        )

    if not args.dry_run:
        # P0-4: 預設輸出路徑改為 artifacts/mr20/orders_mr20_YYYYMMDD.json，避免被 paper_tracker glob 抓到
        out_path = args.output or f"artifacts/mr20/orders_mr20_{signal_date.replace('-', '')}.json"
        saved = save_orders_to_json(orders_dict, out_path)
        print(f"\n💾 訂單已儲存至: {saved}")


if __name__ == "__main__":
    main()

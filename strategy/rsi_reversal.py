#!/usr/bin/env python3
"""RSI 反轉策略 (RSI Reversal Mean Reversion Strategy) — v1.0

Design & Architecture: docs/INVESTMENT_STRATEGY.md §2.3 (Strategy C)

篩選核心邏輯（3 大條件）：
1. 短線超賣：RSI(5) <= 20.0 (Wilder 算法)
2. 放量確認：當日成交量 > 20 日均量 1.5 倍 (Volume > 1.5 * Volume_MA20)
3. 趨勢保護：收盤價 > 60MA (Close > MA60，排除結構性空頭)

流動性池：TWSE 上市普通股 20 日平均成交額 Top-80 (寬池提高觸發率)
持倉週期：3-5 天 (預設 5 天)
目標報酬：+5~8% (預設 +6.0%)
停損門檻：-3.0%
單倉比例：15%

排名規則：
score = 100 - RSI(5)，依 score 由高到低排序 (RSI 越低越超賣)，
同分時依放量倍數 (Volume / Volume_MA20) 由大到小排序，再依成交額排序，
再相同以代號由小到大排序。每日輸出前 5 名。

訂單產出相容於 independent_sim.py schema v1。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import math
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

# 確保專案根目錄在 sys.path 中
_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from strategy.mr20_strategy import compute_v85_atr20, compute_wilder_rsi


@dataclass(frozen=True)
class RSIReversalConfig:
    """RSI 反轉策略參數設定。"""

    liquidity_top_n: int = 80          # 流動性 Top-80
    liquidity_lookback: int = 20       # 20日均額
    ma_trend: int = 60                 # 60日均線 (股價 > 60MA)
    min_history_days: int = 60         # 最少歷史天數
    rsi_period: int = 5                # RSI(5)
    rsi_max: float = 20.0              # RSI(5) <= 20.0
    vol_ma_period: int = 20            # 20日均量
    min_volume_ratio: float = 1.5      # 成交量 > 20日均量 1.5倍
    max_candidates: int = 5            # 每日最多輸出 5 檔
    tp_pct: float = 0.06               # 目標 +6.0% (+5~8%)
    sl_pct: float = 0.03               # 停損 -3.0%
    max_hold_days: int = 5             # 持有 3-5 天 (預設 5)
    position_size: float = 0.15        # 單倉比例 15%
    tp_sl_mode: str = "fixed_pct"      # 固定百分比停損停利
    entry_model: str = "signal_close_limit_next_open_v1"
    time_in_force: str = "DAY_UNTIL_0930"
    cancel_time: str = "09:30:00"
    timezone: str = "Asia/Taipei"
    model_version: str = "rsi_reversal_v1"

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

    cur = pd.Timestamp(dt_str)
    nxt = cur + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.strftime("%Y-%m-%d")


# =====================================================================
# Technical Indicators
# =====================================================================

def compute_rsi_reversal_indicators(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    config: Optional[RSIReversalConfig] = None,
) -> dict[str, pd.DataFrame]:
    """計算 RSI 反轉策略所需的全部技術指標。"""
    cfg = config or RSIReversalConfig()

    ma_trend = close_df.rolling(cfg.ma_trend).mean()
    rsi = compute_wilder_rsi(close_df, period=cfg.rsi_period)
    vol_ma = vol_df.rolling(cfg.vol_ma_period).mean()
    vol_ratio = vol_df / vol_ma.replace(0, np.nan)
    turnover = (close_df * vol_df).rolling(cfg.liquidity_lookback).mean()
    atr = compute_v85_atr20(close_df, period=20)

    return {
        "ma_trend": ma_trend,
        "rsi": rsi,
        "vol_ma": vol_ma,
        "vol_ratio": vol_ratio,
        "turnover": turnover,
        "atr": atr,
    }


# =====================================================================
# Filtering & Ranking Logic
# =====================================================================

def filter_rsi_reversal_candidates(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    as_of_date: Optional[str] = None,
    config: Optional[RSIReversalConfig] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
) -> list[dict[str, Any]]:
    """
    依據 3 大核心條件篩選與排名 RSI 反轉候選個股。

    條件：
    1. 流動性：20 日平均成交額 Top-80
    2. 趨勢：Close > MA60
    3. 超賣：RSI(5) <= 20.0
    4. 放量：Volume > 1.5 * Volume_MA20
    """
    cfg = config or RSIReversalConfig()

    if close_df.empty or vol_df.empty:
        return []

    cal = calendar or get_calendar("XTAI")

    if as_of_date is not None:
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

    if t_loc < 1:
        return []

    # 歷史天數過濾 (>= 60 日)
    eligible_tickers = []
    for ticker in close_df.columns:
        series_up_to_t = close_df[ticker].iloc[: t_loc + 1].dropna()
        if len(series_up_to_t) >= cfg.min_history_days:
            eligible_tickers.append(ticker)

    if not eligible_tickers:
        return []

    indicators = compute_rsi_reversal_indicators(close_df, vol_df, config=cfg)
    ma_trend_df = indicators["ma_trend"]
    rsi_df = indicators["rsi"]
    vol_ratio_df = indicators["vol_ratio"]
    turnover_df = indicators["turnover"]
    atr_df = indicators["atr"]

    # 條件 1: 在符合 60 日歷史門檻內取 20 日均額 Top-N (預設 80)
    turnover_series = turnover_df.loc[target_dt, eligible_tickers].dropna()
    valid_turnovers = turnover_series[turnover_series > 0]
    if valid_turnovers.empty:
        return []

    top_n_thresh = min(cfg.liquidity_top_n, len(valid_turnovers))
    top_liquid_tickers = set(valid_turnovers.nlargest(top_n_thresh).index)

    passed_candidates = []

    for ticker in top_liquid_tickers:
        c_t = close_df[ticker].iloc[t_loc]
        v_t = vol_df[ticker].iloc[t_loc]
        ma_t = ma_trend_df[ticker].iloc[t_loc]
        rsi_val = rsi_df[ticker].iloc[t_loc]
        vol_ratio_val = vol_ratio_df[ticker].iloc[t_loc]
        turnover_val = turnover_df[ticker].iloc[t_loc]
        atr_val = atr_df[ticker].iloc[t_loc]

        if pd.isna(c_t) or pd.isna(ma_t) or pd.isna(rsi_val) or pd.isna(vol_ratio_val):
            continue

        # 條件 2: 股價 > 60MA (趨勢保護)
        if not (c_t > ma_t):
            continue

        # 條件 3: RSI(5) <= rsi_max (20.0)
        if not (rsi_val <= cfg.rsi_max):
            continue

        # 條件 4: 成交量 > 20日均量 1.5倍
        if not (vol_ratio_val >= cfg.min_volume_ratio):
            continue

        score = 100.0 - float(rsi_val)
        passed_candidates.append({
            "ticker": str(ticker),
            "score": score,
            "rsi": float(rsi_val),
            "vol_ratio": float(vol_ratio_val),
            "turnover": float(turnover_val),
            "close": float(c_t),
            "volume": float(v_t),
            "ma60": float(ma_t),
            "atr": float(atr_val) if not pd.isna(atr_val) else 0.0,
        })

    # 排序規則：
    # 1. score 由高到低 (RSI 越低越優先)
    # 2. vol_ratio 由大到小 (放量倍數越大越優先)
    # 3. turnover 由大到小
    # 4. ticker 由小到大
    passed_candidates.sort(
        key=lambda x: (-x["score"], -x["vol_ratio"], -x["turnover"], x["ticker"])
    )

    selected = passed_candidates[: cfg.max_candidates]
    for idx, cand in enumerate(selected, 1):
        cand["rank"] = idx

    return selected


# =====================================================================
# Order Generation & Artifacts
# =====================================================================

def build_rsi_reversal_order_record(
    signal_date: str,
    execution_date: str,
    ticker: str,
    rank: int,
    score: float,
    reference_close: float,
    atr: float,
    config: Optional[RSIReversalConfig] = None,
) -> dict[str, Any]:
    """建立符合 independent_sim.py 規格之 RSI 反轉單筆委託字典。"""
    cfg = config or RSIReversalConfig()
    ref_close = round(float(reference_close), 4)
    atr_val = round(float(atr), 4)
    tp_price = round(ref_close * (1.0 + cfg.tp_pct), 4)
    sl_price = round(ref_close * (1.0 - cfg.sl_pct), 4)

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
        "tp_pct": float(cfg.tp_pct),
        "sl_pct": float(cfg.sl_pct),
        "max_hold_days": int(cfg.max_hold_days),
        "position_size": float(cfg.position_size),
        "tp_sl_mode": cfg.tp_sl_mode,
        "model_version": cfg.model_version,
    }


def generate_rsi_reversal_orders(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    signal_date: Optional[str] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
    config: Optional[RSIReversalConfig] = None,
) -> dict[str, list[dict[str, Any]]]:
    """針對指定訊號日產生 RSI 反轉策略訂單結構。"""
    cfg = config or RSIReversalConfig()
    cal = calendar or get_calendar("XTAI")

    if signal_date is None:
        if close_df.empty:
            return {"orders": []}
        signal_date = close_df.index[-1].strftime("%Y-%m-%d")

    if cal is not None and not cal.is_session(signal_date):
        raise ValueError(f"Specified signal date '{signal_date}' is not a valid XTAI trading session.")

    candidates = filter_rsi_reversal_candidates(close_df, vol_df, as_of_date=signal_date, config=cfg, calendar=cal)
    exec_date = get_next_trading_day(signal_date, calendar=cal)

    orders = []
    for cand in candidates:
        order = build_rsi_reversal_order_record(
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
        description="RSI 反轉策略 (RSI Reversal Mean Reversion) 選股與訂單產生器"
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
        "--output",
        "--orders-out",
        dest="output",
        type=str,
        default=None,
        help="訂單 JSON 輸出路徑 (例如: artifacts/rsi_reversal/orders_rsi_reversal_20260821.json)",
    )
    parser.add_argument(
        "--top-n",
        dest="top_n",
        type=int,
        default=80,
        help="流動性 Universe 大小 (預設 80)",
    )
    parser.add_argument(
        "--rsi-max",
        dest="rsi_max",
        type=float,
        default=20.0,
        help="RSI(5) 超賣上限門檻 (預設 20.0)",
    )
    parser.add_argument(
        "--min-vol-ratio",
        dest="min_vol_ratio",
        type=float,
        default=1.5,
        help="成交量爆量倍數門檻 (預設 1.5)",
    )
    parser.add_argument(
        "--max-candidates",
        dest="max_candidates",
        type=int,
        default=5,
        help="每日最多輸出候選筆數 (預設 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="僅輸出篩選結果，不寫入檔案",
    )
    return parser.parse_args(args)


def main():
    args = parse_args()
    cfg = RSIReversalConfig(
        liquidity_top_n=args.top_n,
        rsi_max=args.rsi_max,
        min_volume_ratio=args.min_vol_ratio,
        max_candidates=args.max_candidates,
    )

    cal = get_calendar("XTAI")

    # 處理訊號日期
    if args.as_of:
        signal_date = args.as_of
        if cal is not None and not cal.is_session(signal_date):
            raise ValueError(f"Specified as-of date '{signal_date}' is not a valid XTAI trading session.")
        elif cal is None and pd.Timestamp(signal_date).weekday() >= 5:
            raise ValueError(f"Specified as-of date '{signal_date}' is not a valid trading day (weekend).")
    else:
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

    end_fetch_dt = pd.Timestamp(signal_date) + timedelta(days=1)
    end_date_str = end_fetch_dt.strftime("%Y-%m-%d")

    from strategy.ai_strategy import fetch_panel_data
    from strategy.universe import get_twse_common_stocks

    tickers = args.tickers or get_twse_common_stocks()

    print(f"🎯 RSI 反轉策略 (股池規模: {len(tickers)} 檔, 訊號日: {signal_date})...")
    close_df, open_df, high_df, low_df, vol_df = fetch_panel_data(
        tickers, days=180, end_date=end_date_str
    )

    target_dt = pd.Timestamp(signal_date)
    if target_dt in close_df.index:
        close_df = close_df.loc[:target_dt]
        vol_df = vol_df.loc[:target_dt]

    if close_df.empty or close_df.index[-1] != target_dt:
        actual_last = close_df.index[-1].strftime("%Y-%m-%d") if not close_df.empty else "None"
        raise ValueError(
            f"Fetched data last date ({actual_last}) does not match expected signal date ({signal_date})."
        )

    orders_dict = generate_rsi_reversal_orders(close_df, vol_df, signal_date=signal_date, calendar=cal, config=cfg)
    orders = orders_dict["orders"]

    print(f"\n📊 [{signal_date}] RSI 反轉選股結果 (共 {len(orders)} 檔入選):")
    for o in orders:
        print(
            f"  #{o['rank']} 代號: {o['ticker']:<6} 分數: {o['score']:.2f} "
            f"收盤/限價: {o['reference_close']:.2f} ATR: {o['atr']:.2f} "
            f"TP (+{o['tp_pct']*100:.1f}%): {o['tp_price']:.2f} SL (-{o['sl_pct']*100:.1f}%): {o['sl_price']:.2f}"
        )

    if not args.dry_run:
        out_path = args.output or f"artifacts/rsi_reversal/orders_rsi_reversal_{signal_date.replace('-', '')}.json"
        saved = save_orders_to_json(orders_dict, out_path)
        print(f"\n💾 訂單已儲存至: {saved}")


if __name__ == "__main__":
    main()

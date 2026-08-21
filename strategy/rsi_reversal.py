#!/usr/bin/env python3
"""RSI 反轉策略 (RSI Reversal Mean Reversion Strategy) — v1.0

Design & Architecture: docs/INVESTMENT_STRATEGY.md §2.3 (Strategy C)

篩選核心邏輯（4 大條件）：
1. 流動性：TWSE 上市普通股 20 日平均成交額 Top-80 (寬池提高觸發率)
2. 長期趨勢未死：收盤價 > MA200 (Close > MA200，年線之上排除結構性下跌股)
3. 短線超賣觸發（三種擇一）：
   a. 放量超賣：RSI(14) <= 28.0 且 Volume > 1.5 * Volume_MA20
   b. 布林下軌超賣：Close < BB_Lower(20, 2σ) 且 Close > MA200
   c. 急跌反彈：5 日累計跌幅 <= -12% ((Close_t - Close_{t-5}) / Close_{t-5} <= -0.12)
4. 止跌確認：Close[t] > Open[t] (收紅 K，若無 Open 資料則依收盤價防護)

持倉週期：5 個交易日 (均值回歸最佳持有期)
停利停損：Entry ± 2.0 * ATR14 (ATR 對稱風報比)
單倉比例：15%
每日最多候選：5 檔

排名規則：
Score = 加權排序：
  40% * (RSI 超賣深度: 100 - RSI(14)，RSI 越低越超賣)
  30% * (5 日跌幅幅度: -return_5d * 100，跌幅越大分數越高)
  30% * (流動性排名)
排序：Score 由高到低，同分時依放量倍數排序，再依成交額排序，再依代號排序。每日輸出前 5 名。

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

from strategy.mr20_strategy import compute_wilder_rsi


def compute_v85_atr(close: pd.Series | pd.DataFrame, period: int = 14) -> pd.Series | pd.DataFrame:
    """
    計算 close-based ATR (預設 14 日)。
    ATR = mean(abs(Close.pct_change()), period) * Close
    """
    pct_changes = close.pct_change().abs()
    mean_pct = pct_changes.rolling(period).mean()
    return mean_pct * close


# Backward-compatible alias
compute_v85_atr20 = compute_v85_atr


@dataclass(frozen=True)
class RSIReversalConfig:
    """RSI 反轉策略參數設定 (對齊 INVESTMENT_STRATEGY.md §2.3 / §4.3)。"""

    liquidity_top_n: int = 80          # 流動性池 Top-80
    liquidity_lookback: int = 20       # 20 日均額
    ma_long: int = 200                 # 年線 200MA (Close > MA200)
    min_history_days: int = 200        # 最少歷史天數 (計算 200MA)
    rsi_period: int = 14               # 標準 14 日 RSI
    rsi_max: float = 28.0              # 深度超賣門檻 RSI(14) <= 28.0
    vol_ma_period: int = 20            # 20 日均量
    min_volume_ratio: float = 1.5      # 放量確認：Volume > 1.5 * Volume_MA20
    bb_period: int = 20                # 布林通道週期
    bb_std: float = 2.0                # 布林通道標準差倍數
    drop_5d_threshold: float = -0.12   # 5 日累計跌幅門檻 <= -12%
    atr_period: int = 14               # ATR 週期 14
    tp_atr_mult: float = 2.0           # 停利：Entry + 2.0 * ATR14
    sl_atr_mult: float = 2.0           # 停損：Entry - 2.0 * ATR14
    max_hold_days: int = 5             # 最大持倉：5 個交易日
    position_size: float = 0.15        # 單倉比例：15%
    max_candidates: int = 5            # 每日最多輸出 5 檔
    tp_sl_mode: str = "atr"            # ATR 停利停損
    entry_model: str = "signal_close_limit_next_open_v1"
    time_in_force: str = "DAY_UNTIL_0930"
    cancel_time: str = "09:30:00"
    timezone: str = "Asia/Taipei"
    model_version: str = "rsi_reversal_v1"

    # Backward compatibility aliases / optional overrides
    ma_trend: int = 200                # alias for ma_long
    tp_pct: Optional[float] = None     # optional fixed pct override
    sl_pct: Optional[float] = None     # optional fixed pct override

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
    ma_period = cfg.ma_long if cfg.ma_long is not None else cfg.ma_trend

    ma_long = close_df.rolling(ma_period).mean()
    rsi = compute_wilder_rsi(close_df, period=cfg.rsi_period)
    vol_ma = vol_df.rolling(cfg.vol_ma_period).mean()
    vol_ratio = vol_df / vol_ma.replace(0, np.nan)
    turnover = (close_df * vol_df).rolling(cfg.liquidity_lookback).mean()
    atr = compute_v85_atr(close_df, period=cfg.atr_period)

    bb_mid = close_df.rolling(cfg.bb_period).mean()
    bb_std = close_df.rolling(cfg.bb_period).std()
    bb_lower = bb_mid - cfg.bb_std * bb_std
    return_5d = close_df.pct_change(5)

    return {
        "ma_long": ma_long,
        "ma_trend": ma_long,
        "rsi": rsi,
        "vol_ma": vol_ma,
        "vol_ratio": vol_ratio,
        "turnover": turnover,
        "atr": atr,
        "bb_lower": bb_lower,
        "return_5d": return_5d,
    }


# =====================================================================
# Filtering & Ranking Logic
# =====================================================================

def filter_rsi_reversal_candidates(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    open_df: Optional[pd.DataFrame] = None,
    as_of_date: Optional[str] = None,
    config: Optional[RSIReversalConfig] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
) -> list[dict[str, Any]]:
    """
    依據 INVESTMENT_STRATEGY.md §2.3 條件篩選與排名 RSI 反轉候選個股。

    條件：
    1. 流動性：20 日平均成交額 Top-80
    2. 長期趨勢未死：Close > MA200
    3. 短線超賣觸發（三種擇一）：
       a. 放量超賣：RSI(14) <= 28.0 且 Volume > 1.5 * Volume_MA20
       b. 布林下軌超賣：Close < BB_Lower(20, 2σ) 且 Close > MA200
       c. 急跌反彈：5 日累計跌幅 <= -12%
    4. 止跌確認：Close[t] > Open[t] (收紅 K 陽線，缺 Open 資料則剔除)
    """
    # 支援 positional argument: filter_rsi_reversal_candidates(close, vol, as_of_date, config, calendar)
    if isinstance(open_df, str):
        calendar = config  # type: ignore
        config = as_of_date  # type: ignore
        as_of_date = open_df
        open_df = None

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

    # 歷史天數過濾 (>= min_history_days)
    eligible_tickers = []
    for ticker in close_df.columns:
        series_up_to_t = close_df[ticker].iloc[: t_loc + 1].dropna()
        if len(series_up_to_t) >= cfg.min_history_days:
            eligible_tickers.append(ticker)

    if not eligible_tickers:
        return []

    indicators = compute_rsi_reversal_indicators(close_df, vol_df, config=cfg)
    ma_long_df = indicators["ma_long"]
    rsi_df = indicators["rsi"]
    vol_ratio_df = indicators["vol_ratio"]
    turnover_df = indicators["turnover"]
    atr_df = indicators["atr"]
    bb_lower_df = indicators["bb_lower"]
    return_5d_df = indicators["return_5d"]

    # 條件 1: 在符合歷史門檻內取 20 日均額 Top-N (預設 80)
    turnover_series = turnover_df.loc[target_dt, eligible_tickers].dropna()
    valid_turnovers = turnover_series[turnover_series > 0]
    if valid_turnovers.empty:
        return []

    # Sort ties deterministically by turnover desc, ticker asc before selecting top N
    sorted_tickers = sorted(valid_turnovers.index, key=lambda tk: (-float(valid_turnovers[tk]), str(tk)))
    top_n_thresh = min(cfg.liquidity_top_n, len(sorted_tickers))
    top_liquid_tickers = sorted_tickers[:top_n_thresh]
    top_liquid_series = valid_turnovers.loc[top_liquid_tickers]
    total_liquid = len(top_liquid_tickers)

    # Tie-aware liquidity ranking (method='min'): equal turnovers receive identical rank & score
    min_ranks = top_liquid_series.rank(ascending=False, method="min") - 1.0
    turnover_rank_map = min_ranks.to_dict()

    passed_candidates = []

    for ticker in top_liquid_tickers:
        c_t = close_df[ticker].iloc[t_loc]
        v_t = vol_df[ticker].iloc[t_loc]
        ma_t = ma_long_df[ticker].iloc[t_loc]
        rsi_val = rsi_df[ticker].iloc[t_loc]
        vol_ratio_val = vol_ratio_df[ticker].iloc[t_loc]
        turnover_val = turnover_df[ticker].iloc[t_loc]
        atr_val = atr_df[ticker].iloc[t_loc]
        bb_low_val = bb_lower_df[ticker].iloc[t_loc]
        ret_5d_val = return_5d_df[ticker].iloc[t_loc]

        if pd.isna(c_t) or pd.isna(ma_t) or pd.isna(rsi_val):
            continue

        # 條件 2: 股價 > MA200 (長期趨勢保護)
        if not (c_t > ma_t):
            continue

        # 條件 3: 短線超賣觸發（三種擇一）：
        # a. 放量超賣：RSI(14) <= 28 且 Volume > 1.5 * Volume_MA20
        # b. 布林下軌超賣：Close < BB_Lower(20, 2σ)
        # c. 急跌反彈：5 日累計跌幅 <= -12%
        trig_a = (rsi_val <= cfg.rsi_max) and (not pd.isna(vol_ratio_val) and vol_ratio_val > cfg.min_volume_ratio)
        trig_b = (not pd.isna(bb_low_val)) and (c_t < bb_low_val)
        trig_c = (not pd.isna(ret_5d_val)) and (ret_5d_val <= cfg.drop_5d_threshold)

        if not (trig_a or trig_b or trig_c):
            continue

        # 條件 4: 止跌確認 Close[t] > Open[t] (收紅 K 陽線，缺 Open 或非紅 K 則剔除)
        if open_df is None or ticker not in open_df.columns:
            continue
        o_t = open_df[ticker].iloc[t_loc]
        if pd.isna(o_t) or not (c_t > o_t):
            continue

        ret_5d_clean = float(ret_5d_val) if not pd.isna(ret_5d_val) else 0.0
        rsi_clean = float(rsi_val)
        vol_ratio_clean = float(vol_ratio_val) if not pd.isna(vol_ratio_val) else 1.0
        turnover_clean = float(turnover_val) if not pd.isna(turnover_val) else 0.0

        # 加權評分：
        # 40% RSI 超賣深度 (100 - RSI)
        # 30% 5 日跌幅 (-return_5d * 100)
        # 30% 流動性排名 (依據流動性股池內成交額排名標準化評分)
        rsi_score = 100.0 - rsi_clean
        drop_score = max(0.0, -ret_5d_clean * 100.0)
        t_rank = turnover_rank_map.get(ticker, total_liquid - 1)
        turnover_score = (1.0 - (t_rank / total_liquid)) * 100.0 if total_liquid > 0 else 0.0
        score = 0.40 * rsi_score + 0.30 * drop_score + 0.30 * turnover_score

        passed_candidates.append({
            "ticker": str(ticker),
            "score": score,
            "rsi": rsi_clean,
            "vol_ratio": vol_ratio_clean,
            "turnover": turnover_clean,
            "close": float(c_t),
            "volume": float(v_t) if not pd.isna(v_t) else 0.0,
            "ma200": float(ma_t),
            "bb_lower": float(bb_low_val) if not pd.isna(bb_low_val) else 0.0,
            "return_5d": ret_5d_clean,
            "atr": float(atr_val) if not pd.isna(atr_val) else 0.0,
            "trigger_a": bool(trig_a),
            "trigger_b": bool(trig_b),
            "trigger_c": bool(trig_c),
        })

    # 排序規則：
    # 1. score 由高到低 (加權分數越高越優先)
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

    if cfg.tp_sl_mode == "atr":
        tp_price = round(ref_close + cfg.tp_atr_mult * atr_val, 4)
        sl_price = round(ref_close - cfg.sl_atr_mult * atr_val, 4)
    else:
        tp_pct = cfg.tp_pct if cfg.tp_pct is not None else 0.06
        sl_pct = cfg.sl_pct if cfg.sl_pct is not None else 0.03
        tp_price = round(ref_close * (1.0 + tp_pct), 4)
        sl_price = round(ref_close * (1.0 - sl_pct), 4)

    record = {
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
        "tp_sl_mode": cfg.tp_sl_mode,
        "model_version": cfg.model_version,
    }
    if cfg.tp_pct is not None:
        record["tp_pct"] = float(cfg.tp_pct)
    if cfg.sl_pct is not None:
        record["sl_pct"] = float(cfg.sl_pct)
    return record


def generate_rsi_reversal_orders(
    close_df: pd.DataFrame,
    vol_df: pd.DataFrame,
    open_df: Optional[pd.DataFrame] = None,
    signal_date: Optional[str] = None,
    calendar: Optional[xcals.ExchangeCalendar] = None,
    config: Optional[RSIReversalConfig] = None,
) -> dict[str, list[dict[str, Any]]]:
    """針對指定訊號日產生 RSI 反轉策略訂單結構。"""
    # 支援 positional argument: generate_rsi_reversal_orders(close, vol, signal_date, calendar, config)
    if isinstance(open_df, str):
        config = calendar  # type: ignore
        calendar = signal_date  # type: ignore
        signal_date = open_df
        open_df = None

    cfg = config or RSIReversalConfig()
    cal = calendar or get_calendar("XTAI")

    if signal_date is None:
        if close_df.empty:
            return {"orders": []}
        signal_date = close_df.index[-1].strftime("%Y-%m-%d")

    if cal is not None and not cal.is_session(signal_date):
        raise ValueError(f"Specified signal date '{signal_date}' is not a valid XTAI trading session.")

    candidates = filter_rsi_reversal_candidates(
        close_df, vol_df, open_df=open_df, as_of_date=signal_date, config=cfg, calendar=cal
    )
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
        default=28.0,
        help="RSI(14) 超賣上限門檻 (預設 28.0)",
    )
    parser.add_argument(
        "--min-vol-ratio",
        dest="min_vol_ratio",
        type=float,
        default=1.5,
        help="成交量爆量倍數門檻 (預設 1.5)",
    )
    parser.add_argument(
        "--tp-atr",
        dest="tp_atr",
        type=float,
        default=2.0,
        help="ATR 停利倍數 (預設 2.0)",
    )
    parser.add_argument(
        "--sl-atr",
        dest="sl_atr",
        type=float,
        default=2.0,
        help="ATR 停損倍數 (預設 2.0)",
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
        tp_atr_mult=args.tp_atr,
        sl_atr_mult=args.sl_atr,
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
        tickers, days=365, end_date=end_date_str
    )

    target_dt = pd.Timestamp(signal_date)
    if target_dt in close_df.index:
        close_df = close_df.loc[:target_dt]
        vol_df = vol_df.loc[:target_dt]
        if not open_df.empty and target_dt in open_df.index:
            open_df = open_df.loc[:target_dt]

    if close_df.empty or close_df.index[-1] != target_dt:
        actual_last = close_df.index[-1].strftime("%Y-%m-%d") if not close_df.empty else "None"
        raise ValueError(
            f"Fetched data last date ({actual_last}) does not match expected signal date ({signal_date})."
        )

    orders_dict = generate_rsi_reversal_orders(
        close_df, vol_df, open_df=open_df, signal_date=signal_date, calendar=cal, config=cfg
    )
    orders = orders_dict["orders"]

    print(f"\n📊 [{signal_date}] RSI 反轉選股結果 (共 {len(orders)} 檔入選):")
    for o in orders:
        print(
            f"  #{o['rank']} 代號: {o['ticker']:<6} 分數: {o['score']:.2f} "
            f"收盤/限價: {o['reference_close']:.2f} ATR: {o['atr']:.2f} "
            f"TP (ATR×{o['tp_atr_mult']}): {o['tp_price']:.2f} SL (ATR×{o['sl_atr_mult']}): {o['sl_price']:.2f}"
        )

    if not args.dry_run:
        out_path = args.output or f"artifacts/rsi_reversal/orders_rsi_reversal_{signal_date.replace('-', '')}.json"
        saved = save_orders_to_json(orders_dict, out_path)
        print(f"\n💾 訂單已儲存至: {saved}")


if __name__ == "__main__":
    main()

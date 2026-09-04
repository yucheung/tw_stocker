#!/usr/bin/env python3
"""Meal Money v2（早盤餐費策略）paper testing CLI。

指令：
  backtest — 用 data/intraday 下本地 5 分 K 回測 strategy/meal_money.py 的邏輯
  signal   — 用最新一天的本地資料產生下一個交易日的候選觀察名單，
             並把候選（依 max_trades_per_day 截取）記錄成 meal_money_state.json 的 pending_orders
  status   — 顯示 meal_money_state.json 目前的部位、待成交委託、已平倉績效與權益曲線

本腳本只負責產生信號與記錄 pending_orders，尚未實作自動撮合/收盤結算；
真正的委託成交判斷（是否真的在 09:15 用觀察名單價位成交）留待下一階段的
paper trading engine 處理。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from strategy.meal_money import (
    MealMoneyConfig,
    build_latest_watchlist,
    read_intraday_csv,
    run_backtest,
)

DEFAULT_DATA_DIR = Path("data/intraday")
DEFAULT_STATE_PATH = Path("meal_money_state.json")
DEFAULT_ARTIFACTS_DIR = Path("artifacts/meal_money_paper")
INITIAL_CAPITAL = 100_000.0
SCHEMA_VERSION = 1
STRATEGY_ID = "meal_money_v2"
MIN_REQUIRED_DAYS = 20  # rolling rank 至少需要約 60 天資料才穩定，<20 天視為資料量不足


# =====================================================================
# 本地資料載入（相容 fetch_intraday.py 的 {ticker}_5m.csv 命名）
# =====================================================================

def load_intraday_dir(data_dir: Path, tickers: Optional[list[str]] = None) -> dict[str, pd.DataFrame]:
    ticker_set = {str(t) for t in tickers} if tickers else None
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(Path(data_dir).glob("*_5m.csv")):
        ticker = path.stem[:-3] if path.stem.endswith("_5m") else path.stem
        if ticker_set is not None and ticker not in ticker_set:
            continue
        try:
            df = read_intraday_csv(path)
        except Exception as exc:
            print(f"{ticker}: 讀取失敗 ({exc})")
            continue
        if not df.empty:
            out[ticker] = df
    return out


def max_available_days(intraday: dict[str, pd.DataFrame]) -> int:
    """回傳本地資料涵蓋的最大交易日數（取各檔股票中最多天數者）。"""
    if not intraday:
        return 0
    return max(df.index.normalize().nunique() for df in intraday.values())


def check_min_days(intraday: dict[str, pd.DataFrame], min_days: int = MIN_REQUIRED_DAYS) -> bool:
    """資料天數不足時印出警告並回傳 False，避免 rolling rank 產生誤導性的空候選結果。"""
    days = max_available_days(intraday)
    if days < min_days:
        print(f"本地資料僅有 {days} 個交易日（需要至少 {min_days} 天，rolling rank 建議約 60 天），"
              f"資料量不足，請先用 fetch_intraday.py 累積更多天數再執行。")
        return False
    return True


# =====================================================================
# Paper 狀態管理
# =====================================================================

def new_state(config: MealMoneyConfig) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "created_at": datetime.now().isoformat(),
        "config": config.to_dict(),
        "cash": INITIAL_CAPITAL,
        "positions": {},
        "pending_orders": [],
        "closed_trades": [],
        "equity_curve": [],
    }


def load_state(path: Path, config: MealMoneyConfig) -> dict:
    if not path.exists():
        return new_state(config)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# =====================================================================
# 共用參數
# =====================================================================

def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="本地 5 分鐘 CSV 目錄（預設 data/intraday）")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH),
                        help="paper 狀態檔路徑（預設 meal_money_state.json）")
    parser.add_argument("--tickers", default=None,
                        help="逗號分隔的股票代碼子集；預設讀取 data-dir 下所有檔案")
    parser.add_argument("--trade-capital", type=float, default=INITIAL_CAPITAL)
    parser.add_argument("--max-trade-capital", type=float, default=INITIAL_CAPITAL)
    parser.add_argument("--target-min", type=float, default=500.0)
    parser.add_argument("--target-max", type=float, default=800.0)


def make_config(args: argparse.Namespace) -> MealMoneyConfig:
    return MealMoneyConfig(
        trade_capital=args.trade_capital,
        max_trade_capital=args.max_trade_capital,
        target_min=args.target_min,
        target_max=args.target_max,
    )


def resolve_tickers(args: argparse.Namespace) -> Optional[list[str]]:
    if not args.tickers:
        return None
    return [t.strip() for t in args.tickers.split(",") if t.strip()]


# =====================================================================
# backtest
# =====================================================================

def cmd_backtest(args: argparse.Namespace) -> int:
    config = make_config(args)
    intraday = load_intraday_dir(Path(args.data_dir), resolve_tickers(args))
    if not intraday:
        print(f"{args.data_dir} 下沒有可用的 5 分鐘資料，請先執行 fetch_intraday.py")
        return 1
    if not check_min_days(intraday):
        return 1

    trades_df, daily_df, summary = run_backtest(
        intraday, config, start_date=args.start_date, end_date=args.end_date,
    )

    print("Meal Money v2 回測結果")
    print(f"  Active days:              {summary['active_days']}")
    print(f"  Success days:             {summary['success_days']}")
    print(f"  Daily success rate:       {summary['daily_success_rate'] * 100:.1f}%")
    print(f"  Total trades:             {summary['total_trades']}")
    print(f"  Win rate:                 {summary['win_rate'] * 100:.1f}%")
    print(f"  Total net PnL:            {summary['total_pnl']:+,.0f}")
    print(f"  Avg trade net PnL:        {summary['avg_trade_pnl']:+,.0f}")
    print(f"  Exit >= 09:40 violations: {summary['cutoff_violations']}")

    DEFAULT_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    trades_path = DEFAULT_ARTIFACTS_DIR / f"trades_{stamp}.csv"
    daily_path = DEFAULT_ARTIFACTS_DIR / f"daily_{stamp}.csv"
    meta_path = DEFAULT_ARTIFACTS_DIR / f"summary_{stamp}.json"
    trades_df.to_csv(trades_path, index=False)
    daily_df.to_csv(daily_path, index=False)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.now().isoformat(), "summary": summary}, f,
                  indent=2, ensure_ascii=False)
    print(f"Saved: {trades_path}")
    print(f"Saved: {daily_path}")
    print(f"Saved: {meta_path}")
    return 0


# =====================================================================
# signal
# =====================================================================

def cmd_signal(args: argparse.Namespace) -> int:
    config = make_config(args)
    intraday = load_intraday_dir(Path(args.data_dir), resolve_tickers(args))
    if not intraday:
        print(f"{args.data_dir} 下沒有可用的 5 分鐘資料，請先執行 fetch_intraday.py")
        return 1
    if not check_min_days(intraday):
        return 1

    watchlist = build_latest_watchlist(intraday, config)
    if watchlist.empty:
        print("今日沒有符合條件的 Meal Money 候選。")
        return 0

    print(watchlist.to_string(index=False))

    DEFAULT_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out_csv = DEFAULT_ARTIFACTS_DIR / f"watchlist_{stamp}.csv"
    watchlist.to_csv(out_csv, index=False)
    print(f"Saved: {out_csv}")

    state_path = Path(args.state_file)
    state = load_state(state_path, config)
    signal_date = str(watchlist.iloc[0]["Signal_Date"])
    picked = watchlist.head(config.max_trades_per_day).to_dict(orient="records")
    # 注意：pending_orders 內儲存的 key 是小寫 signal_date（見下方 append），
    # 這裡必須用同樣的 key 比對，否則永遠比對不到既有紀錄，導致重複寫入。
    existing_dates = {o.get("signal_date") for o in state.get("pending_orders", [])}
    if signal_date in existing_dates:
        print(f"{signal_date} 的候選已經記錄在 pending_orders，略過重複寫入。")
    else:
        for row in picked:
            state["pending_orders"].append({
                "signal_date": row["Signal_Date"],
                "ticker": row["Ticker"],
                "reference_close": row["Reference_Close"],
                "entry_bid": row["One_Tick_Edge_Bid"],
                "valid_entry_min": row["Valid_Entry_Min"],
                "valid_entry_max": row["Valid_Entry_Max"],
                "estimated_shares": row["Estimated_Shares"],
                "estimated_target_exit": row["Estimated_Target_Exit"],
                "force_exit_time": config.force_exit_time,
                "status": "PENDING",
                "created_at": datetime.now().isoformat(),
            })
        save_state(state, state_path)
        print(f"已寫入 {len(picked)} 筆 pending_orders 至 {state_path}")
    return 0


# =====================================================================
# status
# =====================================================================

def cmd_status(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    if not state_path.exists():
        print(f"找不到狀態檔 {state_path}，尚未執行過 signal 指令。")
        return 1

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    print(f"策略: {state.get('strategy_id')}")
    print(f"建立時間: {state.get('created_at')}")
    print(f"現金: {state.get('cash', 0):,.0f}")

    positions = state.get("positions", {})
    print(f"持倉: {len(positions)} 筆")
    for ticker, pos in positions.items():
        print(f"  {ticker}: {pos}")

    pending = state.get("pending_orders", [])
    print(f"待成交委託: {len(pending)} 筆")
    for order in pending[-5:]:
        print(f"  {order}")

    closed = state.get("closed_trades", [])
    total_pnl = sum(float(t.get("pnl", 0)) for t in closed)
    print(f"已平倉交易: {len(closed)} 筆，累積淨損益: {total_pnl:+,.0f}")

    equity_curve = state.get("equity_curve", [])
    if equity_curve:
        latest = equity_curve[-1]
        print(f"最新權益 ({latest.get('date')}): {latest.get('equity', 0):,.0f}")
    return 0


# =====================================================================
# CLI
# =====================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Meal Money v2 paper testing CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="用本地 5 分 K 回測")
    add_common_args(backtest_parser)
    backtest_parser.add_argument("--start-date", default=None)
    backtest_parser.add_argument("--end-date", default=None)
    backtest_parser.set_defaults(func=cmd_backtest)

    signal_parser = subparsers.add_parser("signal", help="產生下一交易日候選觀察名單")
    add_common_args(signal_parser)
    signal_parser.set_defaults(func=cmd_signal)

    status_parser = subparsers.add_parser("status", help="顯示目前 paper 狀態")
    status_parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
    status_parser.set_defaults(func=cmd_status)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

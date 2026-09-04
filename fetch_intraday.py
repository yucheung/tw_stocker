#!/usr/bin/env python3
"""下載台股 5 分鐘 K 線資料，供 Meal Money 策略回測與訊號使用。

輸出檔案：data/intraday/{ticker}_5m.csv（欄位：Datetime, Open, High, Low, Close, Volume）。
與既有 CSV 合併存檔，而非整批覆寫，因為 yfinance 的 5 分鐘資料只提供最近 60 天，
每天執行一次即可逐步累積出超過 60 天的本地歷史。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_DATA_DIR = Path("data/intraday")
MAX_5M_DAYS = 59  # yfinance 5 分鐘資料上限為 60 天，保留 1 天緩衝
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下載台股 5 分鐘 K 線並合併存檔")
    parser.add_argument("--tickers", required=True,
                        help="逗號分隔的股票代碼（不含 .TW），例如 2330,2317,2454")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--days", type=int, default=MAX_5M_DAYS,
                        help="下載天數，最多 60 天（yfinance 限制）")
    parser.add_argument("--as-of", default=None,
                        help="標記本次下載對應的交易日 YYYY-MM-DD；僅用於輸出訊息，"
                             "不會改變 yfinance 實際查詢到的資料範圍")
    return parser.parse_args()


def flatten_columns(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """新版 yfinance 有時會回傳 MultiIndex 欄位，這裡統一攤平成單層。"""
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.xs(symbol, axis=1, level=-1)
        except KeyError:
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
    return df


def fetch_one(ticker: str, days: int) -> pd.DataFrame:
    import yfinance as yf

    symbol = f"{ticker}.TW"
    period = f"{max(1, min(days, MAX_5M_DAYS))}d"
    df = yf.download(symbol, period=period, interval="5m", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = flatten_columns(df, symbol)
    df = df.rename_axis("Datetime").reset_index()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol} 回傳資料缺少欄位: {missing}")
    df = df[["Datetime", *OHLCV_COLUMNS]].copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
    for col in OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Open", "High", "Low", "Close"])


def merge_with_existing(path: Path, new_df: pd.DataFrame) -> pd.DataFrame:
    if not path.exists():
        return new_df.sort_values("Datetime").reset_index(drop=True)
    old_df = pd.read_csv(path)
    old_df["Datetime"] = pd.to_datetime(old_df["Datetime"], utc=True, errors="coerce")
    old_df = old_df.dropna(subset=["Datetime"])
    merged = pd.concat([old_df, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset="Datetime", keep="last")
    return merged.sort_values("Datetime").reset_index(drop=True)


def main() -> int:
    args = parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("未指定任何股票代碼")
        return 1

    if args.as_of:
        try:
            datetime.strptime(args.as_of, "%Y-%m-%d")
        except ValueError:
            print(f"--as-of 格式錯誤: {args.as_of}，需為 YYYY-MM-DD")
            return 1

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    ok, failed = [], []
    for ticker in tickers:
        try:
            new_df = fetch_one(ticker, args.days)
        except Exception as exc:
            print(f"{ticker}: 下載失敗 ({exc})", file=sys.stderr)
            failed.append(ticker)
            continue
        if new_df.empty:
            # yfinance 回傳空資料時不寫檔，避免留下空的/損毀的 CSV
            print(f"{ticker}: 無資料回傳（yfinance 5 分鐘資料僅提供最近 60 天）", file=sys.stderr)
            failed.append(ticker)
            continue
        out_path = data_dir / f"{ticker}_5m.csv"
        merged = merge_with_existing(out_path, new_df)
        merged.to_csv(out_path, index=False)
        span = f"{merged['Datetime'].min()} ~ {merged['Datetime'].max()}"
        print(f"{ticker}: 寫入 {out_path}（共 {len(merged)} 筆，{span}）")
        ok.append(ticker)

    stamp = args.as_of or datetime.now().strftime("%Y-%m-%d")
    print(f"完成（as_of={stamp}）：成功 {len(ok)}，失敗 {len(failed)}")
    if failed:
        print(f"失敗清單: {', '.join(failed)}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Incremental, resumable data cache for the isolated Strategy C simulator."""

from __future__ import annotations

import json
import os
import pickle
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

from strategy.multifactor_c import BENCHMARK_SYMBOL


def market_symbols_from_info(info: pd.DataFrame) -> list[str]:
    """Mirror engine v3's TWSE/TPEx common-stock universe filter."""
    symbols = []
    for _, row in info.iterrows():
        stock_id = str(row["stock_id"]).strip()
        market = str(row["type"]).strip().lower()
        suffix = {"twse": ".TW", "tpex": ".TWO"}.get(market)
        if suffix is None or len(stock_id) != 4 or not stock_id.isdigit() or stock_id.startswith("00"):
            continue
        symbols.append(f"{stock_id}{suffix}")
    return sorted(set(symbols))


class MfcDataStore:
    """Load and incrementally update prices, revenue, and institutional data.

    External providers are injected so tests can exercise cache behavior without
    importing or calling yfinance/FinMind. Revenue and institutional checkpoints
    are tracked per symbol and month; successful symbols survive process restarts.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        price_provider: Optional[Callable[[list[str], str, str], Dict[str, pd.DataFrame]]] = None,
        revenue_provider: Optional[Callable[[str, str], pd.DataFrame]] = None,
        institutional_provider: Optional[Callable[[str, str], pd.DataFrame]] = None,
        info_provider: Optional[Callable[[], pd.DataFrame]] = None,
        throttle_seconds: float = 0.5,
        save_every: int = 15,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.price_provider = price_provider or self._yfinance_prices
        self.revenue_provider = revenue_provider or self._finmind_revenue
        self.institutional_provider = institutional_provider or self._finmind_institutional
        self.info_provider = info_provider or self._finmind_stock_info
        self.throttle_seconds = max(0.0, float(throttle_seconds))
        self.save_every = max(1, int(save_every))
        self.sleep_fn = sleep_fn
        self._data_loader = None

        self.price_path = self.cache_dir / "market_prices.pkl"
        self.revenue_path = self.cache_dir / "market_revenue.pkl"
        self.institutional_path = self.cache_dir / "market_institutional.pkl"
        self.status_path = self.cache_dir / "fetch_status.json"
        self.symbols_path = self.cache_dir / "market_symbols.json"

    def _load_pickle(self, path: Path) -> dict[str, pd.DataFrame]:
        if not path.exists():
            return {}
        with path.open("rb") as handle:
            data = pickle.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Invalid MFC cache format: {path}")
        return data

    @staticmethod
    def _atomic_pickle(path: Path, value: dict[str, pd.DataFrame]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, path)

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _load_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {"revenue_month_by_symbol": {}, "institutional_month_by_symbol": {}}
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        status.setdefault("revenue_month_by_symbol", {})
        status.setdefault("institutional_month_by_symbol", {})
        return status

    def _save_fundamental_progress(
        self,
        revenue: dict[str, pd.DataFrame],
        institutional: dict[str, pd.DataFrame],
        status: dict[str, Any],
    ) -> None:
        self._atomic_pickle(self.revenue_path, revenue)
        self._atomic_pickle(self.institutional_path, institutional)
        self._atomic_json(self.status_path, status)

    def _get_data_loader(self):
        if self._data_loader is None:
            from FinMind.data import DataLoader

            self._data_loader = DataLoader()
        return self._data_loader

    def _finmind_stock_info(self) -> pd.DataFrame:
        return self._get_data_loader().taiwan_stock_info()

    def _finmind_revenue(self, stock_id: str, start_date: str) -> pd.DataFrame:
        return self._get_data_loader().taiwan_stock_month_revenue(stock_id=stock_id, start_date=start_date)

    def _finmind_institutional(self, stock_id: str, start_date: str) -> pd.DataFrame:
        return self._get_data_loader().taiwan_stock_institutional_investors(
            stock_id=stock_id,
            start_date=start_date,
        )

    @staticmethod
    def _extract_ticker_frame(download: pd.DataFrame, ticker: str, ticker_count: int) -> pd.DataFrame:
        if download is None or download.empty:
            return pd.DataFrame()
        if not isinstance(download.columns, pd.MultiIndex):
            return download.copy() if ticker_count == 1 else pd.DataFrame()
        for level in range(download.columns.nlevels):
            values = download.columns.get_level_values(level)
            if ticker in values:
                return download.xs(ticker, axis=1, level=level).copy()
        return pd.DataFrame()

    @staticmethod
    def _yfinance_prices(tickers: list[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        import yfinance as yf

        downloaded = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        return {
            ticker: MfcDataStore._extract_ticker_frame(downloaded, ticker, len(tickers))
            for ticker in tickers
        }

    def get_market_symbols(self, refresh: bool = False, max_age_days: int = 30) -> list[str]:
        """Load a cached v3 universe snapshot or fetch it from FinMind once a month."""
        cached: Optional[dict[str, Any]] = None
        if self.symbols_path.exists():
            cached = json.loads(self.symbols_path.read_text(encoding="utf-8"))
            try:
                age = (date.today() - date.fromisoformat(cached["fetched_at"])).days
            except (KeyError, ValueError):
                age = max_age_days + 1
            if not refresh and age <= max_age_days:
                return list(cached["symbols"])

        try:
            info = self.info_provider()
            symbols = market_symbols_from_info(info)
            if BENCHMARK_SYMBOL not in symbols:
                symbols.append(BENCHMARK_SYMBOL)
            payload = {
                "fetched_at": date.today().isoformat(),
                "symbols": sorted(set(symbols)),
            }
            self._atomic_json(self.symbols_path, payload)
            return payload["symbols"]
        except Exception:
            if cached is not None:
                return list(cached["symbols"])
            raise

    def update_prices(
        self,
        symbols: list[str],
        as_of_date: str,
        initial_start_date: str = "2025-01-01",
        chunk_size: int = 25,
    ) -> dict[str, pd.DataFrame]:
        """Fetch missing daily bars only, then merge and persist each ticker batch."""
        prices = self._load_pickle(self.price_path)
        as_of = pd.Timestamp(as_of_date)
        starts: dict[str, str] = {}
        for symbol in dict.fromkeys(symbols):
            existing = prices.get(symbol)
            if existing is None or existing.empty:
                starts[symbol] = initial_start_date
                continue
            latest = pd.Timestamp(existing.index.max()).tz_localize(None)
            starts[symbol] = max(initial_start_date, (latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))

        end_exclusive = (as_of + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        refresh_symbols = [symbol for symbol, start in starts.items() if start < end_exclusive]
        for offset in range(0, len(refresh_symbols), max(1, chunk_size)):
            chunk = refresh_symbols[offset : offset + max(1, chunk_size)]
            chunk_start = min(starts[symbol] for symbol in chunk)
            fetched = self.price_provider(chunk, chunk_start, end_exclusive) or {}
            for symbol in chunk:
                new = fetched.get(symbol)
                if new is None or new.empty:
                    continue
                new = new.copy()
                new.index = pd.to_datetime(new.index)
                old = prices.get(symbol)
                merged = pd.concat([old, new]) if old is not None and not old.empty else new
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                merged = merged[pd.Index(merged.index).tz_localize(None) <= as_of]
                if "Close" in merged and "Volume" in merged:
                    merged["Turnover"] = merged["Close"] * merged["Volume"]
                    merged["Turnover_MA20"] = merged["Turnover"].rolling(window=20).mean()
                    merged["MA20"] = merged["Close"].rolling(window=20).mean()
                    merged["MA60"] = merged["Close"].rolling(window=60).mean()
                prices[symbol] = merged
            self._atomic_pickle(self.price_path, prices)
        if not refresh_symbols:
            self._atomic_pickle(self.price_path, prices)
        return prices

    @staticmethod
    def _merge_rows(existing: Optional[pd.DataFrame], incoming: Optional[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
        if incoming is None or incoming.empty:
            return existing.copy() if existing is not None else pd.DataFrame()
        frame = incoming.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        if existing is not None and not existing.empty:
            previous = existing.copy()
            previous["date"] = pd.to_datetime(previous["date"])
            frame = pd.concat([previous, frame], ignore_index=True, sort=False)
        frame = frame.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)
        return frame

    @staticmethod
    def _revenue_factors(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        frame = frame.sort_values("date").reset_index(drop=True)
        frame["revenue_YoY"] = frame["revenue"].ffill().pct_change(12, fill_method=None) * 100.0
        frame["revenue_3m_avg"] = frame["revenue"].rolling(3).mean()
        frame["revenue_3m_YoY"] = frame["revenue_3m_avg"].ffill().pct_change(12, fill_method=None) * 100.0
        return frame

    def _update_finmind_dataset(
        self,
        symbols: list[str],
        as_of_date: str,
        initial_start_date: str,
        dataset: str,
    ) -> dict[str, pd.DataFrame]:
        if pd.Timestamp(as_of_date).day < 11:
            return self._load_pickle(self.revenue_path if dataset == "revenue" else self.institutional_path)

        data_path = self.revenue_path if dataset == "revenue" else self.institutional_path
        data = self._load_pickle(data_path)
        status = self._load_status()
        month = as_of_date[:7]
        status_key = f"{dataset}_month_by_symbol"
        checkpoint = status.setdefault(status_key, {})
        provider = self.revenue_provider if dataset == "revenue" else self.institutional_provider
        processed = 0

        for symbol in dict.fromkeys(symbols):
            if symbol == BENCHMARK_SYMBOL or checkpoint.get(symbol) == month:
                continue
            existing = data.get(symbol)
            if existing is not None and not existing.empty:
                last_date = pd.to_datetime(existing["date"]).max()
                start_date = max(initial_start_date, last_date.strftime("%Y-%m-%d"))
            else:
                start_date = initial_start_date
            stock_id = symbol.split(".", 1)[0]
            try:
                incoming = provider(stock_id, start_date)
                keys = ["date"] if dataset == "revenue" else ["date", "name"]
                merged = self._merge_rows(existing, incoming, keys)
                if dataset == "revenue":
                    merged = self._revenue_factors(merged)
                elif not merged.empty and "net_buy" not in merged.columns:
                    merged["net_buy"] = merged["buy"] - merged["sell"]
                data[symbol] = merged
                checkpoint[symbol] = month
            except Exception as exc:
                print(f"[MFC] {dataset} fetch failed for {symbol}: {exc}")
            finally:
                processed += 1
                if self.throttle_seconds:
                    self.sleep_fn(self.throttle_seconds)
                if processed % self.save_every == 0:
                    self._save_fundamental_progress(
                        data if dataset == "revenue" else self._load_pickle(self.revenue_path),
                        data if dataset == "institutional" else self._load_pickle(self.institutional_path),
                        status,
                    )

        self._save_fundamental_progress(
            data if dataset == "revenue" else self._load_pickle(self.revenue_path),
            data if dataset == "institutional" else self._load_pickle(self.institutional_path),
            status,
        )
        return data

    def update_revenue(
        self,
        symbols: list[str],
        as_of_date: str,
        initial_start_date: str = "2020-01-01",
    ) -> dict[str, pd.DataFrame]:
        return self._update_finmind_dataset(symbols, as_of_date, initial_start_date, "revenue")

    def update_institutional(
        self,
        symbols: list[str],
        as_of_date: str,
        initial_start_date: str = "2020-01-01",
    ) -> dict[str, pd.DataFrame]:
        return self._update_finmind_dataset(symbols, as_of_date, initial_start_date, "institutional")

    def load_all(self) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        return (
            self._load_pickle(self.price_path),
            self._load_pickle(self.revenue_path),
            self._load_pickle(self.institutional_path),
        )

import pandas as pd

from strategy.multifactor_c_data import MfcDataStore, market_symbols_from_info


def test_revenue_updates_resume_throttle_and_persist_each_checkpoint(tmp_path):
    calls = []
    sleeps = []
    failed_once = {"2222"}

    def revenue_provider(stock_id, start_date):
        calls.append((stock_id, start_date))
        if stock_id in failed_once:
            failed_once.remove(stock_id)
            raise RuntimeError("temporary FinMind failure")
        dates = pd.date_range("2025-10-01", periods=12, freq="MS")
        return pd.DataFrame({"date": dates, "revenue": range(100, 112)})

    store = MfcDataStore(
        tmp_path,
        revenue_provider=revenue_provider,
        sleep_fn=sleeps.append,
        throttle_seconds=0.25,
        save_every=1,
    )

    first = store.update_revenue(["1111.TW", "2222.TW", "3333.TW"], "2026-09-11")
    resumed = MfcDataStore(
        tmp_path,
        revenue_provider=revenue_provider,
        sleep_fn=sleeps.append,
        throttle_seconds=0.25,
        save_every=1,
    ).update_revenue(["1111.TW", "2222.TW", "3333.TW"], "2026-09-11")

    assert set(first) == {"1111.TW", "3333.TW"}
    assert set(resumed) == {"1111.TW", "2222.TW", "3333.TW"}
    assert [stock_id for stock_id, _ in calls].count("1111") == 1
    assert [stock_id for stock_id, _ in calls].count("2222") == 2
    assert [stock_id for stock_id, _ in calls].count("3333") == 1
    assert sleeps == [0.25] * 4
    assert (tmp_path / "market_revenue.pkl").exists()


def test_revenue_is_deferred_until_publication_window_and_cached_once_monthly(tmp_path):
    calls = []

    def revenue_provider(stock_id, start_date):
        calls.append(stock_id)
        return pd.DataFrame({"date": [pd.Timestamp("2026-01-01")], "revenue": [100]})

    store = MfcDataStore(tmp_path, revenue_provider=revenue_provider, save_every=1)

    store.update_revenue(["1111.TW"], "2026-09-10")
    store.update_revenue(["1111.TW"], "2026-09-11")
    store.update_revenue(["1111.TW"], "2026-09-30")

    assert calls == ["1111"]


def test_institutional_updates_add_net_buy_and_resume_from_monthly_checkpoint(tmp_path):
    calls = []

    def institutional_provider(stock_id, start_date):
        calls.append((stock_id, start_date))
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-10", "2026-09-10"]),
                "name": ["Investment_Trust", "Foreign_Investor"],
                "buy": [150, 200],
                "sell": [40, 80],
            }
        )

    store = MfcDataStore(
        tmp_path,
        institutional_provider=institutional_provider,
        throttle_seconds=0,
        save_every=1,
    )

    first = store.update_institutional(["1111.TW"], "2026-09-11")
    resumed = MfcDataStore(
        tmp_path,
        institutional_provider=institutional_provider,
        throttle_seconds=0,
        save_every=1,
    ).update_institutional(["1111.TW"], "2026-09-11")

    assert calls == [("1111", "2020-01-01")]
    assert dict(zip(resumed["1111.TW"]["name"], resumed["1111.TW"]["net_buy"])) == {
        "Investment_Trust": 110,
        "Foreign_Investor": 120,
    }
    assert set(first["1111.TW"]["name"]) == {"Investment_Trust", "Foreign_Investor"}
    assert (tmp_path / "market_institutional.pkl").exists()


def test_price_updates_start_after_cached_session_and_merge_turnover(tmp_path):
    calls = []

    def prices_provider(symbols, start_date, end_date):
        calls.append((symbols, start_date, end_date))
        if start_date == "2026-09-10":
            dates = pd.to_datetime(["2026-09-10"])
            close = [100.0]
        else:
            dates = pd.to_datetime(["2026-09-11"])
            close = [101.0]
        return {
            symbol: pd.DataFrame({"Close": close, "Volume": [1000]}, index=dates)
            for symbol in symbols
        }

    store = MfcDataStore(tmp_path, price_provider=prices_provider)
    store.update_prices(["1111.TW"], "2026-09-10", initial_start_date="2026-09-10")
    prices = MfcDataStore(tmp_path, price_provider=prices_provider).update_prices(
        ["1111.TW"], "2026-09-11", initial_start_date="2026-09-10"
    )

    assert calls == [
        (["1111.TW"], "2026-09-10", "2026-09-11"),
        (["1111.TW"], "2026-09-11", "2026-09-12"),
    ]
    assert prices["1111.TW"]["Close"].tolist() == [100.0, 101.0]
    assert prices["1111.TW"]["Turnover"].tolist() == [100_000.0, 101_000.0]


def test_market_info_excludes_etfs_and_non_common_stock_types():
    info = pd.DataFrame(
        {
            "stock_id": ["2330", "8299", "0050", "ABCD", "1234"],
            "type": ["twse", "tpex", "twse", "twse", "emerging"],
        }
    )

    assert market_symbols_from_info(info) == ["2330.TW", "8299.TWO"]

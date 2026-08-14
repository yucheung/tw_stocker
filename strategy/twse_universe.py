"""
TWSE 上市普通股清單（取代硬編碼股池，修復前視選股偏差）。

背景
----
`ai_report.EXTENDED_TICKERS` 是一份手工維護的 116 檔名單，選的是「今天已知
表現亮眼」的權值股 / AI 股 / 航運股。在這份名單上跑回測，等於在事後篩出的贏家
池裡再選贏家，所有績效數字被系統性灌水；`build_liquid_universe` 的 Top-N 流動性
篩選也因此形同虛設。

本模組改以 TWSE OpenAPI「上市公司基本資料」(t187ap03_L) 取得**全體上市公司**
的普通股代號（~1089 檔），讓 `build_liquid_universe` 真正在全市場上做每日
Top-N 流動性篩選——這才是「動態 Universe」原本應有的意思。

偏差說明（誠實揭露，勿高估修復程度）
------------------------------------
- ✅ **選擇偏差已消除**：清單是全市場列舉，不含任何人為挑選。
- ⚠️ **存活者偏差仍在**：TWSE OpenAPI 只提供「當前」上市清單，回測期間內下市
  的公司不會出現。因此結果仍略偏樂觀，但偏差量級遠小於原本的手選名單。
- ✅ **不引入新的 look-ahead**：清單只決定「下載哪些代號」。未上市 / 未上市前的
  期間在 yfinance 回傳 NaN，`build_liquid_universe` 的
  `close_df.notna() & (close_df > 0)` 會將其排除；且 turnover 需要 20 根 K 線
  才進得了 universe，新股實際上是上市約一個月後才可能被選中，方向偏保守。
- 清單快照會連同抓取日期寫入本地 cache 並納入版控，讓歷史 run 可重現。
"""

import json
import os
import urllib.request
from datetime import datetime, timezone

TWSE_LISTED_API = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'

_DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'twse_listed_common_stocks.json',
)

# 快照超過此天數就嘗試重抓（抓不到仍沿用舊快照）
DEFAULT_MAX_AGE_DAYS = 30


def _parse_listing_date(raw):
    """t187ap03_L 的上市日期為 'YYYYMMDD' 字串；格式異常則回傳 None。"""
    raw = (raw or '').strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, '%Y%m%d').strftime('%Y-%m-%d')
    except ValueError:
        return None


def fetch_from_twse(timeout=30):
    """
    直接向 TWSE OpenAPI 抓取上市公司清單。

    Returns
    -------
    dict
        {'fetched_at': ISO 日期, 'source': url, 'stocks': [{code, name, listing_date}, ...]}
    """
    req = urllib.request.Request(
        TWSE_LISTED_API,
        headers={'User-Agent': 'tw_stocker/8.5 (+universe-builder)'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode('utf-8'))

    stocks = []
    for row in payload:
        code = (row.get('公司代號') or '').strip()
        # 只取 4 碼數字的普通股代號。ETF / 權證 / 特別股 / 受益證券不在此 API，
        # 少數外國企業註冊代號含英文字母者一併排除。
        if len(code) != 4 or not code.isdigit():
            continue
        stocks.append({
            'code': code,
            'name': (row.get('公司簡稱') or '').strip(),
            'listing_date': _parse_listing_date(row.get('上市日期')),
        })

    stocks.sort(key=lambda s: s['code'])
    if len(stocks) < 500:
        raise RuntimeError(f"TWSE 清單異常：只取得 {len(stocks)} 檔，疑似 API 格式變更")

    return {
        'fetched_at': datetime.now(timezone.utc).date().isoformat(),
        'source': TWSE_LISTED_API,
        'count': len(stocks),
        'stocks': stocks,
    }


def _cache_age_days(snapshot):
    try:
        fetched = datetime.strptime(snapshot['fetched_at'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return None
    return (datetime.now(timezone.utc).date() - fetched).days


def load_twse_snapshot(cache_path=None, max_age_days=DEFAULT_MAX_AGE_DAYS,
                       refresh=False, verbose=True):
    """
    取得上市公司清單快照，優先用本地 cache，過期或不存在時才連網。

    連網失敗且有 cache → 沿用 cache（附警告）。連網失敗且無 cache → RuntimeError。
    """
    cache_path = cache_path or _DEFAULT_CACHE
    cached = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError):
            cached = None

    age = _cache_age_days(cached) if cached else None
    fresh_enough = cached is not None and age is not None and age <= max_age_days

    if fresh_enough and not refresh:
        if verbose:
            print(f"   📋 使用本地上市清單快照: {cached['count']} 檔 "
                  f"(擷取於 {cached['fetched_at']}, {age} 天前)")
        return cached

    try:
        snapshot = fetch_from_twse()
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1)
        if verbose:
            print(f"   🌐 已更新 TWSE 上市清單: {snapshot['count']} 檔 "
                  f"({snapshot['fetched_at']})")
        return snapshot
    except Exception as exc:
        if cached is not None:
            print(f"   ⚠️ TWSE 清單抓取失敗（{exc}），沿用 {age} 天前的本地快照")
            return cached
        raise RuntimeError(f"無法取得 TWSE 上市清單且無本地快照: {exc}") from exc


def get_twse_common_stocks(cache_path=None, max_age_days=DEFAULT_MAX_AGE_DAYS,
                           refresh=False, listed_before=None, verbose=True):
    """
    回傳 TWSE 全體上市普通股代號列表。

    Parameters
    ----------
    listed_before : str or datetime-like, optional
        若提供，排除在該日期之後才上市的個股。純粹是為了少下載一批注定全 NaN 的
        代號（省時間），對結果無影響——這些股票本來就會被 universe mask 濾掉。
    """
    snapshot = load_twse_snapshot(cache_path=cache_path, max_age_days=max_age_days,
                                  refresh=refresh, verbose=verbose)
    stocks = snapshot['stocks']

    if listed_before is not None:
        cutoff = str(listed_before)[:10]
        kept = [s for s in stocks
                if s.get('listing_date') is None or s['listing_date'] <= cutoff]
        if verbose and len(kept) < len(stocks):
            print(f"   ✂️ 排除 {len(stocks) - len(kept)} 檔於 {cutoff} 後才上市者")
        stocks = kept

    return [s['code'] for s in stocks]

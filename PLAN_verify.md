# 每日策略驗證腳本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增可獨立執行的 `verify_daily_strategy.py`，在台股交易日收盤後，以 `paper_equity.json` 與 yfinance 市場資料驗證 v8.5 的六項策略規則，將 Telegram 可直接推播的文字寫到 stdout，並把結構化結果追加到 `verify_log.json`。

**Architecture:** 採單檔 CLI、內部分層的設計：先判斷 XTAI 交易日，再載入與驗證 paper snapshot，批次取得一次市場資料，接著以六個無副作用的 validator 產生統一 `CheckResult`，最後分別交給文字 renderer 與原子化 JSON logger。市場下載、時鐘及檔案來源皆以函式參數注入，讓測試不依賴網路或真實日期。

**Tech Stack:** Python 3.13、標準函式庫、pandas、yfinance、exchange_calendars、pytest（測試）。

## Global Constraints

- 不修改 `paper_tracker.py` 或 `paper_equity.json`。
- 正式腳本固定放在 repo 根目錄：`/root/work/tw_stocker/verify_daily_strategy.py`。
- 預設執行方式必須是 `python3 verify_daily_strategy.py`，不需 API key 或付費服務。
- 預設 paper 來源為 `https://raw.githubusercontent.com/yucheung/tw_stocker/main/paper_equity.json`；遠端不可用時可退回同目錄的唯讀本地檔並產生資料來源警告。
- 市場 OHLCV 必須由 yfinance 取得，所有下載都明確指定 `auto_adjust=False`、`progress=False`，避免調整價與進場原價混算或污染 stdout。
- 非交易日 stdout、stderr 與 `verify_log.json` 都不得改變，程序以 exit code 0 結束。
- 規則違規不修改部位、不自動下單，只報告與記錄。
- `verify_log.json` 是執行產物，不是 `paper_equity.json` 的替代資料源。
- 第一版不修改 GitHub Actions 與 Telegram 發送流程；整合只需擷取腳本 stdout，另案處理。

---

## 1. 範圍與關鍵決策

### 1.1 驗證基準時間

- `run_date`：Asia/Taipei 的今天，用於判斷是否為 XTAI session。
- `snapshot_date`：`paper_equity.json.equity_curve[-1].date`；所有部位、訊號與市場價格的共同 as-of date。
- 在交易日執行但 `snapshot_date != run_date` 時，不使用較新的盤中資料補出假象；輸出 `🚨 CRITICAL` 的 stale snapshot，仍對可驗證的舊 snapshot 執行其餘檢查。
- yfinance 偶爾在收盤後尚未提供當日 bar。若最新完整 bar 早於 `snapshot_date`，相關規則為 `⚠️ WARNING / INDETERMINATE`，不可標為 PASS。

### 1.2 ATR 的時間點

TP/SL 在進場時即固定，因此不能以「執行驗證當天的 ATR」回算，否則 ATR 每日變動會製造假違規。驗證器雖然在執行時透過 yfinance 即時下載資料，回算時必須截到進場當時可用的資料：

- 進場採 next-open 模型。
- `entry_session` 為 `entry_date` 對應的 XTAI session。
- `atr_asof` 為 `entry_session` 前一個 XTAI session，避免使用進場當日尚未完成的 high/low 造成 look-ahead。
- ATR 使用截至 `atr_asof` 的 20 根完整日 K。
- 若 paper 未來新增 `entry_atr` 或 `signal_date`，應優先使用明確保存的進場 ATR，並以 yfinance 回算值做第二層交叉檢查；第一版不得自行修改 paper schema。

### 1.3 嚴重度與不可判定

| 等級 | 意義 | 例子 |
|---|---|---|
| `🚨 CRITICAL` | 已有足夠證據證明違反策略或輸入結構無法信任 | TP/SL 超出容差、持倉超過 20 天、曝險超限、持倉 8 檔 |
| `⚠️ WARNING` | 資料不足、外部服務降級、邊界狀態，不能證明違規也不能宣告通過 | 某 ticker 無 yfinance bar、今日 gap 尚待下一交易日開盤 |
| `✅ PASS` | 規則所需資料完整且明確符合 | 7 檔以下且欄位有效 |

總體狀態取最嚴重結果：`CRITICAL > WARNING > PASS`。資料不足一律 fail-open 於「只讀稽核流程」，但 fail-closed 於判定：仍完成其他檢查，不因一檔資料失敗中止整份報告，也絕不把未驗證項目算成 PASS。

## 2. 檔案與責任

| 路徑 | 動作 | 責任 |
|---|---|---|
| `verify_daily_strategy.py` | 新增 | CLI、交易日 gate、資料載入、yfinance 批次下載、六個 validators、文字輸出、JSON log |
| `tests/test_verify_daily_strategy.py` | 新增 | 純函式、錯誤處理、非交易日靜默、CLI 輸出與 JSON schema 測試 |
| `verify_log.json` | 執行時產生 | append-only 稽核歷史；使用暫存檔＋`os.replace` 原子更新 |
| `requirements.txt` | 原則上不改 | `yfinance`、`pandas`、`exchange_calendars` 已存在；只有 pytest 未被開發環境提供時才另行處理測試依賴，不把 pytest 加進 runtime requirements |

腳本可重用 `strategy.sector_flow.classify_sector()`、`strategy.universe.get_twse_common_stocks()`、`strategy.ai_strategy.build_liquid_universe()` 與 `engineer_features()`，但不得 import `paper_tracker.py`，避免載入追蹤器行為或未來 side effect。若重用函式失敗，該檢查降為 WARNING，不改用不同公式偷偷通過。

## 3. 輸入／處理／輸出流程

```text
python3 verify_daily_strategy.py
        │
        ├─ 取得 Asia/Taipei run_date
        ├─ exchange_calendars.XTAI.is_session(run_date)?
        │      └─ 否：exit 0，完全靜默，不寫 log
        │
        ├─ HTTP GET raw paper_equity.json
        │      └─ 失敗：讀本地 paper_equity.json + WARNING
        ├─ schema / snapshot freshness gate
        ├─ 建立所需 ticker 集合與日期窗口
        ├─ yfinance 批次抓 OHLCV
        │      ├─ 上市：<ticker>.TW
        │      └─ 缺漏再試上櫃：<ticker>.TWO
        ├─ 正規化時區、排序、去重、只保留完整 session
        ├─ 執行 6 個 validator + input health check
        ├─ 彙總 Overall status
        ├─ render Telegram plain-text report → stdout
        ├─ append structured run → verify_log.json（原子寫入）
        └─ exit 0（規則違規也為 0）；僅無法建立任何報告的程式級錯誤 exit 2
```

### 3.1 paper snapshot schema gate

必要頂層欄位：

```python
{
    "capital": int | float,
    "positions": dict[str, Position],
    "closed_trades": list,
    "equity_curve": list[dict],
    "daily_signals": list[dict],
}
```

每個 `Position` 至少需要 `entry`、`tp`、`sl`、`entry_date`、`shares`、`day_count`。數字必須 finite，`entry/tp/sl/shares > 0`，日期須為 ISO `YYYY-MM-DD`。缺少整個核心欄位是 CRITICAL；單一 position 損壞時該 position 為 CRITICAL，其餘 position 繼續驗證。

`snapshot_date` 優先取最後一筆 equity curve 日期。若 equity curve 空白，退回最後一筆 daily signal 日期並加 WARNING；兩者皆無日期則輸出 fatal CRITICAL 並以 exit 2 結束。

### 3.2 市場資料下載

分兩組下載，避免重複 request：

1. **稽核小集合**：所有持倉、最新訊號、`0050.TW`；取足以涵蓋最早 entry 前 30 個曆日到 snapshot 後一個 session 的 OHLCV。
2. **score universe**：由現有 TWSE 普通股快照取得母體，至少下載 90 個交易日 OHLCV，以 20 日成交額建立 snapshot 當日 Top-60 流動性池，再做橫截面排名。應沿用現有 batch/fallback 實作概念，且設定合理 timeout/retry（每批最多 200 檔、最多 3 次，指數退避 1/2 秒）。

回傳統一 `MarketData`，validators 不直接呼叫網路：

```python
@dataclass(frozen=True)
class MarketData:
    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
```

## 4. 統一結果介面

```python
@dataclass(frozen=True)
class CheckResult:
    rule_id: str
    title: str
    severity: Literal["PASS", "WARNING", "CRITICAL"]
    summary: str
    details: list[str]
    metrics: dict[str, object]
```

預定函式邊界：

```python
def validate_tp_sl(positions, market, calendar) -> CheckResult: ...
def validate_time_rule(positions, snapshot_date, calendar) -> CheckResult: ...
def validate_regime_exposure(data, market, snapshot_date) -> CheckResult: ...
def validate_sector_concentration(positions, prices) -> CheckResult: ...
def validate_signal_consistency(data, market, snapshot_date) -> CheckResult: ...
def validate_position_count(positions, limit=7) -> CheckResult: ...
def render_report(context, results) -> str: ...
def append_verify_log(path, context, results) -> None: ...
def run_verification(config, now=None, fetcher=None) -> str: ...
```

## 5. 六項驗證的具體邏輯

### 5.1 TP/SL 價格驗證

20 日 ATR 定義與 production event backtester 對齊：

```text
TR[t]    = max(
             High[t] - Low[t],
             abs(High[t] - Close[t-1]),
             abs(Low[t]  - Close[t-1])
           )
ATR20[t] = SMA(TR[t-19:t])

expected_tp = entry + 4.0 × ATR20[atr_asof]
expected_sl = entry - 3.0 × ATR20[atr_asof]
```

判定：

- 必須至少有 21 根連續有效 OHLC（第一根只供 previous close）。
- 每個 expected price 的允許誤差為 `max(0.01 TWD, expected_price × 0.001)`，涵蓋 JSON 小數截斷與 yfinance 微幅資料修訂；誤差只用於比對，不修改公式。
- `abs(stored_tp - expected_tp) <= tolerance_tp` 且 `abs(stored_sl - expected_sl) <= tolerance_sl` 才通過。
- 任一可計算部位不符即整項 CRITICAL，details 列出 ticker、entry、ATR 日期/值、stored/expected/delta。
- OHLC 不足或 ticker 無法解析時，該 ticker 為 WARNING；若同時有其他 ticker 確定違規，整項仍為 CRITICAL。
- 額外 sanity check：`sl < entry < tp`，不成立時無須等待 yfinance，直接 CRITICAL。

### 5.2 20 天 TIME rule

不能只信任可能 stale 的 `day_count`，要交叉計算 XTAI session：

```text
calendar_days = XTAI sessions in (entry_date, snapshot_date] 的數量
effective_days = max(position.day_count, calendar_days)
```

- `effective_days > 20`：CRITICAL，持倉應已強制出場。
- `effective_days <= 20`：PASS。
- `day_count != calendar_days`：附加 WARNING，因 tracker 計數與交易日曆不一致；但 `effective_days > 20` 仍提升為 CRITICAL。
- `entry_date > snapshot_date`、負數 day_count 或無效日期：CRITICAL schema violation。
- 本規格依使用者文字採嚴格的 `> 20`；第 20 天仍屬允許邊界。若日後要與目前 tracker 的 `>= max_hold_days` 完全一致，應先由策略 owner 決定並同時更新此規格與 production，不能在 verifier 中靜默改門檻。

### 5.3 Regime 曝險比例

大盤代理使用 `0050.TW`，在 `snapshot_date` 的最後完整收盤價計算 SMA20/SMA60：

| 0050 狀態 | 曝險上限 |
|---|---:|
| `close > MA60` 且 `close > MA20` | 100% |
| `close > MA60` 且 `close <= MA20` | 70% |
| `close <= MA60` 且 `close > MA20` | 40% |
| `close <= MA60` 且 `close <= MA20` | 10% |

使用同一 snapshot date 的未調整收盤價 mark-to-market：

```text
position_value = Σ(latest_close[ticker] × shares[ticker])
total_equity   = capital + position_value
exposure       = position_value / total_equity
```

- `total_equity <= 0` 直接 CRITICAL。
- `exposure > allowed + 0.005`（0.5 個百分點 operational tolerance）為 CRITICAL；否則 PASS。
- tolerance 只防止收盤資料與 paper rounding 的極小差異，不可把 70.8% 放寬成 PASS。
- 若任一部位缺價或 0050 少於 60 根資料，整項 WARNING/不可判定，不用部分部位估算後宣告 PASS。
- 報告同時顯示 close、MA20、MA60、regime cap、position value、equity 與實際曝險。
- 此處只驗證題目指定的四段式市場 regime，不混入 production 的 breadth/macro 可選縮放；若未來要驗證那些功能，須新增獨立 rule，避免無法解釋 cap 來源。

### 5.4 Sector concentration

板塊分類沿用 `strategy.sector_flow.classify_sector(ticker)`，不能在 verifier 維護第二份 mapping。以**持倉市值**而非檔數計算：

```text
sector_value[s] = Σ(close[ticker] × shares[ticker]) for sector(ticker) == s
sector_pct[s]   = sector_value[s] / Σ(all position values)
```

- 無持倉：PASS，顯示 `0%`。
- 任一 `sector_pct > 75% + 0.005`：CRITICAL。
- 剛好 75%（含 0.5 個百分點容差）為 PASS。
- 任一持倉缺價：整項 WARNING/不可判定，仍在 details 列出可計算的暫估分布，但不得以暫估值 PASS。
- 無法辨識的合法 ticker 依既有函式歸入 `traditional`；非合法 ticker 已由 schema/data health 報警。

### 5.5 Top-7 信號一致性

只選 `daily_signals` 中 `date == snapshot_date` 的最後一筆；同日多筆是 WARNING（使用最後一筆並列出 duplicate count）。每個 ticker 驗證三個條件：

#### A. Score ≥ 2.0

精確重建 v8.5 baseline：

```text
turnover20       = SMA20(close × volume)
universe         = snapshot_date turnover20 Top-60
momentum20       = close[t] / close[t-20]
trend_bias       = close[t] / SMA60(close)[t]
rank_momentum    = universe 內橫截面 percentile rank(momentum20)
rank_trend       = universe 內橫截面 percentile rank(trend_bias)
score            = 3 × rank_momentum + 1 × rank_trend
valid_score      = score >= 2.0
```

訊號 ticker 不在當日 Top-60 universe、score 缺值或 score < 2.0 均為 CRITICAL；母體下載 coverage < 70% 或當日 universe < 48 檔時，score 子項為 WARNING/不可判定，禁止用「只對七檔互排」的錯誤近似。

#### B. 股價 > 60MA

```text
valid_trend = Close[signal_date] > SMA60(Close)[signal_date]
```

等於 MA60 不算通過。資料足夠且不符為 CRITICAL；少於 60 bars 為 WARNING。

#### C. Gap < 1.5 × ATR

Gap 是 next-open 執行條件，不是 signal close 當下可知的條件：

```text
gap       = abs(Open[next_session] - Close[signal_date])
gap_limit = 1.5 × ATR20[signal_date]
valid_gap = gap < gap_limit
```

- 若 next session 尚未發生，顯示 WARNING `PENDING：待下一交易日開盤驗證`，不是違規。
- 若已有 next-session open 且 `gap >= gap_limit`，訊號若仍被視為應進場則 CRITICAL。
- `daily_signals` 代表選股訊號，不等同成交清單；因此 matured gap failure 應在 details 說明「此訊號不得成交」，若 paper position/pending order 能證明已成交才加上「實際違規成交」。第一版不得憑 daily signal 存在就聲稱已成交。

Top-7 排名一致性：

- `len(tickers) > 7` 直接 CRITICAL。
- 必須無 duplicate；duplicate 為 CRITICAL。
- 所有可計算候選按 score 降冪後，訊號應屬符合 threshold/MA 的前七名。若 universe 資料完整且選入較低分 ticker、遺漏更高分合格 ticker，CRITICAL 並列出 expected/actual。
- 如果訊號不足七檔，只要市場上確實沒有更多合格候選即可 PASS；不能要求固定七檔。

### 5.6 持倉數量

```text
position_count = len(positions)
valid = position_count <= 7
```

- 0～7：PASS。
- >7：CRITICAL，列出 count、limit 與全部 ticker。
- 此規則計算實際持倉，不把 `pending_orders` 或 daily signals 算入；若未來需要「持倉＋待成交」容量控制，新增獨立檢查。

## 6. 人類可讀輸出格式

stdout 只含最終報告，不印下載進度、Python warning 或 debug log，方便直接作為 Telegram message body。範例：

```text
📋 tw_stocker v8.5 每日策略驗證
日期：2026-08-19（snapshot: 2026-08-19）
總結：🚨 CRITICAL｜2 critical / 2 warning / 2 pass

🚨 CRITICAL TP/SL 價格
  2330 SL=920.00，預期 905.40（entry=1000.00, ATR20=31.5333, Δ=14.60）
✅ PASS 20 天 TIME rule｜最長持倉 12 天
🚨 CRITICAL Regime 曝險｜實際 73.2% > 上限 70.0%
  0050 close=198.40, MA20=201.12, MA60=192.88
⚠️ WARNING Sector concentration｜2454 缺收盤價，無法完整判定
⚠️ WARNING Top-7 信號一致性｜score/MA 通過；Gap 待 2026-08-20 開盤驗證
✅ PASS 持倉數量｜6 / 7

資料來源：GitHub raw + yfinance（未調整日 K）
稽核 ID：20260819T173012+0800
```

全數通過範例：

```text
📋 tw_stocker v8.5 每日策略驗證
日期：2026-08-19（snapshot: 2026-08-19）
總結：✅ PASS｜6 pass

✅ PASS TP/SL 價格｜6/6 符合 ATR 4×/3×
✅ PASS 20 天 TIME rule｜最長持倉 8 天
✅ PASS Regime 曝險｜實際 66.4% <= 上限 70.0%
✅ PASS Sector concentration｜最高半導體 41.8% <= 75.0%
✅ PASS Top-7 信號一致性｜7/7 score 與 MA 合格；Gap 已驗證
✅ PASS 持倉數量｜6 / 7
```

若同一項同時含缺資料與確定違規，標題顯示 CRITICAL，details 仍保留缺資料 WARNING，確保最嚴重問題不被降級。

## 7. `verify_log.json` 格式與寫入

```json
{
  "schema_version": 1,
  "runs": [
    {
      "run_id": "20260819T173012+0800",
      "run_at": "2026-08-19T17:30:12+08:00",
      "run_date": "2026-08-19",
      "snapshot_date": "2026-08-19",
      "overall_status": "CRITICAL",
      "source": {
        "paper": "github_raw",
        "paper_url": "https://raw.githubusercontent.com/yucheung/tw_stocker/main/paper_equity.json",
        "market": "yfinance",
        "auto_adjust": false
      },
      "summary": {"critical": 2, "warning": 2, "pass": 2},
      "checks": [
        {
          "rule_id": "tp_sl",
          "title": "TP/SL 價格",
          "severity": "CRITICAL",
          "summary": "1/6 部位不符",
          "details": ["2330 SL delta=14.60"],
          "metrics": {"checked": 6, "violations": 1, "unknown": 0}
        }
      ]
    }
  ]
}
```

寫入規則：

1. 讀取既有檔；不存在則建立 schema。
2. JSON 損壞時不覆蓋原檔：先將本次報告標記 log write CRITICAL，stdout 說明人工處理路徑，程序 exit 2。
3. append 本次 run，不覆寫同日舊 run，保留重跑軌跡。
4. 在同目錄建立具唯一名稱的暫存檔、`flush`＋`fsync` 後 `os.replace`。
5. 非交易日完全不進入 logger。
6. 不無限塞入完整 OHLC；只存驗證所需 scalar metrics 與錯誤摘要。

## 8. 錯誤處理策略

| 失敗 | 行為 | 狀態 |
|---|---|---|
| XTAI 判定為非交易日 | 立即結束，不下載、不輸出、不寫 log | exit 0 |
| calendar 套件自身異常 | 不猜測交易日；輸出 operational CRITICAL 並寫 log | exit 2 |
| GitHub raw timeout/非 200 | 嘗試本地 paper；成功則繼續 | WARNING |
| 遠端與本地 paper 都不可用 | 產生最小 fatal report；若可則寫 log | CRITICAL, exit 2 |
| paper JSON decode/schema error | 不執行依賴該欄位的檢查；保留可執行項 | CRITICAL |
| yfinance 全部失敗 | 持倉數量與純 paper TIME rule 照常執行；市場型規則不可判定 | WARNING；若 snapshot 也不可用則 exit 2 |
| 單一 ticker 失敗 | 其他 ticker 繼續；相關整項不可宣告 PASS | WARNING |
| `.TW` 無資料 | 對該 ticker 重試 `.TWO`；仍無資料才 WARNING | WARNING |
| OHLC 有 NaN／重複日期／時區 | 正規化後驗證有效 bar 數；不足則不可判定 | WARNING |
| snapshot stale | 以 snapshot date 稽核舊狀態，不混用今日狀態 | CRITICAL |
| `verify_log.json` 無法原子寫入 | 報告仍輸出，但清楚標記歷史未落盤 | CRITICAL, exit 2 |
| validator 非預期 exception | 捕捉在 rule 邊界，該 rule 產生 WARNING，其他 rules 繼續；最外層保留 traceback 到 stderr 僅限真正程式錯誤 | WARNING/exit 0 |

HTTP request timeout 建議 connect/read 合計 20 秒；yfinance 每批最多重試 3 次。正常報告與規則違規 exit 0，讓後續 Telegram step 能照常推播；只有沒有可信報告或無法保存稽核紀錄的 operational fatal 使用 exit 2。

## 9. 測試策略與 edge cases

所有測試以固定 `now`、假 calendar、fixture paper 與 in-memory `MarketData` 執行，禁止真的連 GitHub/yfinance。至少涵蓋下列案例：

1. **非交易日靜默**：週日或 XTAI 假日；assert stdout/stderr 都是空字串、fetcher 未呼叫、log 不存在/mtime 不變、exit 0。
2. **ATR 邊界與時間點**：目前 ATR 已改變，但 entry 前一 session ATR 可推得 stored TP/SL；應 PASS，證明沒有錯用 current ATR。再將 SL 改超過 tolerance，應 CRITICAL。
3. **TIME rule 日曆差異**：`day_count=18` 但實際已有 21 個 XTAI sessions；應 CRITICAL。另測 20 天 PASS、21 天 CRITICAL。
4. **Regime 四象限**：參數化測試四種 close 相對 MA20/MA60 組合，分別得到 100/70/40/10%；曝險剛好 cap PASS，超過 0.5 個百分點 CRITICAL。
5. **Sector 75% 邊界**：單一 sector 恰為 75% PASS，75.6% CRITICAL；其中一檔缺價時必須 WARNING 而非依剩餘股票誤判 PASS。
6. **Top-7 資料不足**：只有七檔訊號自身資料、沒有完整 universe；score 必須 WARNING，不能在七檔內互排後 PASS。
7. **Gap 尚未成熟與剛好門檻**：沒有 next-session open 時 WARNING/PENDING；`gap == 1.5×ATR` 因規則是 `<` 而 CRITICAL；略小則 PASS。
8. **持倉上限與重複訊號**：8 positions 為 CRITICAL；7 positions PASS。signals 內重複 ticker 即使 list 長度 7 仍 CRITICAL。
9. **部分 yfinance 失敗**：一檔 `.TW` 無資料但 `.TWO` 成功應可驗證；兩者皆失敗時市場相關 rule WARNING，TIME/count 仍有結果。
10. **log 原子性與歷史**：連續兩次同日執行產生兩個不同 run_id；模擬 replace 失敗時舊 JSON 必須完整保留。

核心測試形狀：

```python
def test_tp_sl_uses_atr_available_before_entry():
    positions = {
        "2330": {
            "entry": 100.0,
            "tp": 108.0,
            "sl": 94.0,
            "entry_date": "2026-08-19",
            "shares": 10,
            "day_count": 0,
        }
    }
    market = market_with_atr20(
        atr_on="2026-08-18", atr=2.0,
        later_atr=5.0,
    )
    result = validate_tp_sl(positions, market, fake_xtai_calendar())
    assert result.severity == "PASS"
```

```python
@pytest.mark.parametrize(
    ("above_60", "above_20", "expected_cap"),
    [(True, True, 1.0), (True, False, 0.7),
     (False, True, 0.4), (False, False, 0.1)],
)
def test_regime_cap_mapping(above_60, above_20, expected_cap):
    assert regime_cap(above_60, above_20) == expected_cap
```

```python
def test_non_trading_day_is_completely_silent(tmp_path, capsys):
    fetcher = Mock()
    report = run_verification(
        config(log_path=tmp_path / "verify_log.json"),
        now=taipei_datetime("2026-08-23 17:30"),
        fetcher=fetcher,
    )
    assert report == ""
    assert capsys.readouterr() == ("", "")
    fetcher.assert_not_called()
    assert not (tmp_path / "verify_log.json").exists()
```

## 10. 實作任務（TDD）

### Task 1: 建立 domain types、交易日 gate 與 paper loader

**Files:**
- Create: `verify_daily_strategy.py`
- Create: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Produces: `CheckResult`, `MarketData`, `is_trading_day()`, `load_paper_snapshot()`, `validate_snapshot()`。
- Consumes: GitHub raw URL、本地 fallback path、XTAI calendar。

- [ ] 先寫非交易日完全靜默、遠端失敗退本地、schema 缺欄位的 failing tests。
- [ ] 執行 `python3 -m pytest tests/test_verify_daily_strategy.py -v`，確認因模組/函式未存在而 FAIL。
- [ ] 實作最小 domain types、calendar gate、timeout HTTP loader 與 schema gate。
- [ ] 重跑同一命令，確認 Task 1 tests PASS。
- [ ] Commit: `git commit -m "feat: scaffold daily strategy verifier"`。

### Task 2: 市場資料正規化與 TP/SL、TIME validators

**Files:**
- Modify: `verify_daily_strategy.py`
- Modify: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Produces: `compute_atr20()`, `validate_tp_sl()`, `validate_time_rule()`。
- Consumes: validated positions、MarketData、XTAI calendar。

- [ ] 先寫 ATR entry-as-of、20/21 天邊界、缺 OHLC 與 day_count 不一致 tests。
- [ ] 執行指定 test nodes，確認 FAIL。
- [ ] 實作 TR/ATR20、entry previous-session slicing、容差與 session counting。
- [ ] 重跑指定 tests 與完整 test file，確認 PASS。
- [ ] Commit: `git commit -m "feat: verify exits and holding duration"`。

### Task 3: Regime、sector 與 position-count validators

**Files:**
- Modify: `verify_daily_strategy.py`
- Modify: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Produces: `regime_cap()`, `validate_regime_exposure()`, `validate_sector_concentration()`, `validate_position_count()`。
- Consumes: paper capital/positions、snapshot close、0050 series、既有 sector classifier。

- [ ] 先寫 regime 四象限、曝險 tolerance、sector 75% 邊界、missing price、7/8 positions tests。
- [ ] 執行 tests，確認 FAIL。
- [ ] 依第 5.3、5.4、5.6 節公式實作最小 validators。
- [ ] 重跑 tests，確認 PASS。
- [ ] Commit: `git commit -m "feat: verify portfolio exposure limits"`。

### Task 4: Top-7 信號精確重算

**Files:**
- Modify: `verify_daily_strategy.py`
- Modify: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Produces: `compute_v85_scores()`, `validate_signal_consistency()`。
- Consumes: daily_signals、全市場 OHLCV、Top-60 universe、XTAI next session。

- [ ] 先寫 score threshold、MA equality、universe coverage、Top-7 排名、duplicate、gap pending/equality tests。
- [ ] 執行 tests，確認 FAIL。
- [ ] 重用現有 universe/feature helpers，實作 coverage gate、score/MA/ranking 與 matured gap 判定。
- [ ] 重跑 tests，確認 PASS，並核對 verifier score 與一份既有 `artifacts/orders_*.json` 的 score 在浮點容差內相同。
- [ ] Commit: `git commit -m "feat: verify top seven signal consistency"`。

### Task 5: Renderer、JSON logger 與 CLI orchestration

**Files:**
- Modify: `verify_daily_strategy.py`
- Modify: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Produces: `render_report()`, `append_verify_log()`, `run_verification()`, `main()`。
- Consumes: 所有 CheckResult 與 run context。

- [ ] 先寫全 PASS、混合嚴重度、同日 append、corrupt log、atomic replace failure 與 CLI exit-code tests。
- [ ] 執行 tests，確認 FAIL。
- [ ] 實作純文字 renderer、run schema、原子 append、validator exception boundary 與 `if __name__ == "__main__"`。
- [ ] 重跑完整 verifier tests，確認 PASS。
- [ ] Commit: `git commit -m "feat: report and log daily verification"`。

### Task 6: 最終驗證與文件核對

**Files:**
- Verify only: `verify_daily_strategy.py`, `tests/test_verify_daily_strategy.py`, `PLAN_verify.md`

**Interfaces:**
- Produces: 可交付的測試與執行證據。
- Consumes: 完成的腳本與 fixture。

- [ ] Run: `python3 -m pytest tests/test_verify_daily_strategy.py -v`；Expected: 全部 PASS、exit 0。
- [ ] Run: `python3 -m py_compile verify_daily_strategy.py`；Expected: 無輸出、exit 0。
- [ ] 在注入的非交易日 fixture 執行 CLI；Expected: 0 bytes stdout/stderr、log 不變。
- [ ] 在交易日 fixture 執行 CLI；Expected: 六項規則皆出現在文字與 JSON、severity 數量一致。
- [ ] 在允許網路的環境執行 `python3 verify_daily_strategy.py` smoke test；若當日為交易日，Expected: 產生報告與有效 `verify_log.json`；若非交易日，Expected: 完全靜默。
- [ ] Run project-relevant lint/build（若 repo 沒有既定 lint，至少執行 `python3 -m compileall -q verify_daily_strategy.py tests/test_verify_daily_strategy.py`），記錄 exit code。
- [ ] 檢查 `git diff -- paper_tracker.py paper_equity.json` 為空，證明未修改受保護檔案。
- [ ] Commit: `git commit -m "test: verify daily strategy audit workflow"`。

## 11. 驗收條件

- `python3 verify_daily_strategy.py` 可從 repo 根目錄直接執行，不需要 API key。
- XTAI 非交易日為真正 0-byte 靜默且不新增 log。
- 交易日一定產生六項規則結果；缺資料時明確 WARNING，不出現假 PASS。
- TP/SL 以 entry 前一 session 的 ATR20 回算，公式固定 4×/3×。
- regime 四段 cap 固定 100/70/40/10%，sector cap 75%，持倉上限 7。
- Top-7 score 按完整動態 Top-60 universe 精確重建；gap 只在 next open 已知後下判定。
- stdout 可直接作 Telegram 純文字；任何 CRITICAL/WARNING/PASS 均有指定 emoji。
- `verify_log.json` 保留每次交易日 run、合法 JSON、原子更新。
- 所有測試通過，且 `paper_tracker.py`、`paper_equity.json` 無 diff。

## 12. 明確不做

- 不修復或重算 `paper_equity.json`。
- 不自動平倉、調倉或阻擋 paper tracker。
- 不發 Telegram；只提供適合推播的 stdout。
- 不在第一版修改 GitHub Actions。
- 不把 yfinance 當成交易所官方成交確認來源；資料缺漏必須揭露。
- 不用 today ATR 驗證歷史固定 TP/SL，也不用七檔訊號彼此排名冒充全市場 score。

# Independent Top-2 Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改既有 `ai_report.py`、`paper_tracker.py` 或其資料檔的前提下，建立一套可立即開始累積資料的 Top-7 二次篩選策略，以及單一、獨立的模擬交易腳本。

**Architecture:** `independent_sim.py` 只讀 `artifacts/orders_YYYYMMDD.json`，把既有 Top-7 當成不可變的上游訊號，再挑選最多 2 檔。它使用自己的 JSON state、CSV ledger、績效報告與 Telegram 通知，不讀寫 `paper_equity.json`；限價成交的純判定函式則重用 `strategy.order_execution`，避免與 v8.5 的成交規則漂移。

**Tech Stack:** Python 3.13、標準函式庫、pandas、yfinance、exchange_calendars、matplotlib、pytest；全部已在目前專案依賴範圍內。

## Global Constraints

- 不修改現有程式或既有模擬資料；實作只新增 `independent_sim.py`、`tests/test_independent_sim.py`，以及選用的獨立排程檔。
- Top-7 的權威來源是 `artifacts/orders_YYYYMMDD.json`，不是 `signals_YYYYMMDD.csv`。
- 所有日期判定使用 `exchange_calendars` 的 `XTAI` 與 `Asia/Taipei`，不可用自然日或單純 weekday 推算下一交易日。
- 委託、成交、撤單與每日結算必須冪等；同一命令重跑不得產生第二筆事件或重複扣款。
- 策略自開始日向前累積，不用歷史結果補造 paper trades；歷史資料只用於 ATR 與 0050 基準計算。
- 金額以 TWD 計，v1 使用整股 `lot_size=1`，不模擬融資、放空、股利、除權息或部分成交。
- 這是研究用模擬策略，不代表投資建議或實際成交保證。

---

## 1. 策略邏輯

### 1.1 策略名稱與目的

名稱：**Top-2 Score Concentration（Top-2 集中模擬）**。

目的不是再做一個複雜模型，而是回答一個可持續驗證的問題：v8.5 Top-7 中最強的 1–2 檔，是否能在相同進出場規則下，取得優於全自動買進 Top-7 與 0050 的風險調整後報酬。

### 1.2 上游 Top-7 定義

沿用 production v8.5：

1. 流動性母體為 20 日平均成交額 Top-60。
2. `momentum20 = close[t] / close[t-20]`。
3. `trend_bias = close[t] / SMA60(close)[t]`。
4. `score = 3 × percentile_rank(momentum20) + percentile_rank(trend_bias)`。
5. 僅保留 `score >= 2.0` 且 `close > MA60`，依 score 降冪取 Top-7。

對應現況：`strategy/ai_strategy.py:221-241,302-396`、`ai_report.py:491-512`、`verify_daily_strategy.py:744-775,908-930`。

### 1.3 二次篩選規則

預設自動模式：

1. 讀取指定訊號日的 `orders_YYYYMMDD.json`。
2. 驗證 orders 的 `signal_date` 一致，且 `execution_date` 是 XTAI 下一交易日。
3. 依 `rank` 升冪排序；rank 缺漏時才以 `score` 降冪、ticker 升冪排序。
4. 排除已持有、已有 pending order 或 order 必要欄位非法的股票；ATR 是否可得在建立 pending order 前判定。
5. 取前 2 檔；只剩 1 檔就只下 1 檔，沒有合格標的就保留現金。
6. 某一筆次日撤單後，不以原 Top-7 的第 3 名遞補，避免使用 09:00 後資訊重新選股。

這是「既有動量主導 score 的集中版」，不引入新因子，也不每日強制輪動。持倉只有在 TP、SL 或 TIME 出場後才釋出名額。

可選低價模式：`--max-price 200` 先要求 `reference_close <= 200`，再按上述順序取 2 檔。低價模式若不足 2 檔不向高價股遞補，因此它是可獨立比較的策略變體，不應在不同日期任意開關。

手動模式：`--tickers 2330 2454` 允許使用者從當日 Top-7 指定 1–2 檔。腳本必須拒絕非 Top-7、重複 ticker、超過 2 檔與已持有 ticker，並把 `selection_mode=manual` 記入 state，確保績效可稽核。

### 1.4 資金與部位

- 初始資金：`200,000 TWD`，可用 `init --capital` 覆寫一次。
- 最大同時持倉：2 檔。
- 每筆目標投入：下單當下總權益的 45%。
- 最低現金保留：初始資金的 10%。
- 股數：`floor(min(0.45 × current_equity, available_cash) / fill_price)`；再確認買進手續費後仍保有最低現金。
- 只要算出的股數小於 1，就記錄 `CANCELLED_INSUFFICIENT_CASH`。
- 買進成本率 `0.001425`；賣出成本率 `0.004425`；賣出另計現有 paper tracker 的 `0.003` 滑價。所有績效以扣成本後淨值為主。

每檔 45% 是為了讓兩檔滿倉時仍保留 10% 現金，且讓集中策略與 0050 的報酬比較有意義。若要做較保守實驗，應另開新 state 並固定 `--position-size 0.20`，不可在同一績效序列中途改參數。

### 1.5 隔日 09:00–09:30 限價單

- 訊號日 D 收盤後建立委託，`limit_price = reference_close`。
- `execution_date` 為 D 的下一個 XTAI session。
- 委託有效時間標記為 `09:00:00–09:30:00 Asia/Taipei`。
- v1 為了與既有 `signal_close_limit_next_open_v1` 完全一致，採保守的 **open-only** 模型：
  - `open <= limit`：以 open 成交。
  - `open > limit`：09:30 撤單，記 `CANCELLED_OPEN_ABOVE_LIMIT`。
  - open 缺漏、非正數或 bar 日期過期：撤單，記 `CANCELLED_NO_OPEN_PRICE`。
- 開高後即使當日 low 在 09:30 前後碰到 limit，也不得以日線資料回推成交。
- 每筆 due order 必須在當日產生唯一 terminal event，成交或撤單後都離開 pending orders。

純成交判定重用 `strategy.order_execution.evaluate_buy_limit_at_open()`（`strategy/order_execution.py:60-91`）。如果未來真的需要「09:00–09:30 第一個一分鐘 K 觸價」模型，應另立 `entry_model=first_touch_1m_v1` 和獨立績效序列；不能混入本 v1，因為那會改變策略定義與資料需求。

### 1.6 ATR TP / SL 與 20 日 TIME rule

ATR 嚴格跟 v8.5 現況一致，不使用傳統 True Range：

```text
ATR20 = mean(abs(close.pct_change()), 20) × close
TP = fill_price + 4.0 × ATR20(signal_date)
SL = fill_price - 3.0 × ATR20(signal_date)
```

- 優先使用 order 中有限且大於 0 的 `atr`。
- 舊 order 的 `atr` 為 null 時，以截至 signal date 的收盤價重新計算相同 ATR20；不能取得 21 根有效資料就不下單，記 `SKIPPED_NO_ATR`。
- TP/SL 必須以實際 fill price 為錨重算，不能沿用 signal close 算出的 `tp_price/sl_price`。
- 從進場後下一個有效交易日開始檢查出場；同一天 High/Low 同時觸及時採保守順序 `SL > TP > TIME`。
- SL：若 open < SL，以 open 出場；否則以 SL 出場。
- TP：若 open > TP，以 open 出場；否則以 TP 出場。
- TIME：完成 20 個有有效 bar 的持有交易日後，以第 20 日 close 出場。
- 缺 bar 的 session 不增加 holding day，也不以舊 close 補值。

這些規則對齊 `strategy/event_backtest.py:1-22,1100-1109` 與 `paper_tracker.py:318-366,506-517`。

### 1.7 每日操作流程

```text
D 日 18:05  close-and-plan
  ├─ 結算既有持倉的 D 日 TP/SL/TIME
  ├─ 寫入 D 日策略權益與 0050 收盤基準
  └─ 讀 D 日 Top-7 orders，挑 0–2 檔，建立 D+1 pending orders

D+1 日 09:35  open
  ├─ 取得當日且非 stale 的 open
  ├─ open <= limit → 成交、扣款、建立 position
  └─ 其餘 → 09:30 terminal cancel；不遞補
```

第一次啟用時只執行 `init` 與當日收盤後的 `close-and-plan`，不補造過去交易；從下一個交易日開始自然累積。

---

## 2. 獨立腳本架構

### 2.1 新增檔案

| 檔案 | 責任 |
|---|---|
| `independent_sim.py` | 單一 CLI；讀訊號、選股、成交、持倉結算、state/CSV/report、Telegram |
| `tests/test_independent_sim.py` | 純函式、state、冪等、CLI 與報表測試 |
| `independent_sim_data/state.json` | 唯一權威狀態；atomic replace 寫入，不進用戶既有 paper state |
| `independent_sim_data/orders.csv` | 所有入選、略過、成交與撤單事件的平面匯出 |
| `independent_sim_data/trades.csv` | 已平倉交易明細 |
| `independent_sim_data/equity.csv` | 每交易日策略淨值與 0050 normalized equity |
| `independent_sim_data/performance.md` | 人類可讀績效摘要 |
| `independent_sim_data/performance.png` | 策略 vs 0050 normalized equity 與 drawdown 圖 |
| `.github/workflows/independent_sim.yml` | 選用；完全獨立的 09:35 / 18:05 排程，不改既有 workflow |

產出的 data 目錄可由 `--data-dir` 改到持久磁碟；正式自動化時應決定「commit 回 repository」或「外部 volume」其中一種持久化方式，不可同時使用兩個權威來源。

### 2.2 CLI

```bash
# 一次性初始化
python independent_sim.py init --capital 200000

# 收盤後自動挑 score/rank 最強 2 檔
python independent_sim.py close-and-plan --orders artifacts/orders_20260819.json

# 收盤後套低價條件
python independent_sim.py close-and-plan --orders artifacts/orders_20260819.json --max-price 200

# 收盤後由使用者從 Top-7 手選
python independent_sim.py close-and-plan --orders artifacts/orders_20260819.json --tickers 2330 2454

# 次交易日 09:35 結算 09:00–09:30 委託
python independent_sim.py open

# 隨時重建 CSV、Markdown 與 PNG
python independent_sim.py report

# 檢視現金、持倉、pending orders 與最近事件
python independent_sim.py status
```

所有改變 state 的命令都支援 `--as-of YYYY-MM-DD`，但 production 預設必須是台北當日；測試與人工修復才顯式指定日期。

### 2.3 內部函式邊界

```python
def load_orders(path: Path, calendar) -> list[dict]: ...
def select_candidates(orders: list[dict], held: set[str], pending: set[str],
                      max_picks: int = 2, max_price: float | None = None,
                      manual_tickers: list[str] | None = None) -> list[dict]: ...
def compute_v85_atr20(closes: pd.Series) -> float | None: ...
def plan_orders(state: dict, selected: list[dict], as_of: date) -> list[dict]: ...
def execute_open_orders(state: dict, bars: dict[str, dict], as_of: date) -> list[dict]: ...
def settle_positions(state: dict, bars: dict[str, dict], as_of: date) -> list[dict]: ...
def mark_equity(state: dict, closes: dict[str, float], benchmark_close: float,
                as_of: date) -> dict: ...
def compute_performance(equity: pd.DataFrame) -> dict[str, float | None]: ...
def export_ledgers(state: dict, data_dir: Path) -> None: ...
def notify_telegram(message: str, token: str, chat_id: str) -> bool: ...
```

`independent_sim.py` 不 import `paper_tracker.py`，避免載入它的檔案常數或副作用；只 import 無副作用的 `DEFAULT_TP_SL` 與 `evaluate_buy_limit_at_open`。

### 2.4 State schema

```json
{
  "schema_version": 1,
  "strategy_id": "top2_score_v1",
  "created_at": "2026-08-19T18:05:00+08:00",
  "config": {
    "initial_capital": 200000.0,
    "max_positions": 2,
    "position_size": 0.45,
    "reserve_ratio": 0.10,
    "max_price": null,
    "tp_atr_mult": 4.0,
    "sl_atr_mult": 3.0,
    "max_hold_days": 20,
    "entry_model": "signal_close_limit_next_open_v1"
  },
  "cash": 200000.0,
  "positions": {},
  "pending_orders": [],
  "order_events": [],
  "closed_trades": [],
  "equity_curve": [],
  "processed_runs": []
}
```

必要識別鍵：

- `order_id = "{strategy_id}:{signal_date}:{ticker}:buy"`
- `run_id = "{command}:{as_of}"`
- order terminal status：`FILLED`、`CANCELLED_OPEN_ABOVE_LIMIT`、`CANCELLED_NO_OPEN_PRICE`、`CANCELLED_NO_CAPACITY`、`CANCELLED_INSUFFICIENT_CASH`、`CANCELLED_INVALID_LIMIT`、`CANCELLED_EXPIRED`、`SKIPPED_NO_ATR`。

State 先寫同目錄 temporary file、`flush + fsync`，再以 `os.replace` 原子替換；執行期間使用 lock file，避免 morning/evening job 或人工重跑同時改檔。

### 2.5 CSV 欄位

`orders.csv`：

```text
order_id,signal_date,execution_date,event_time,ticker,upstream_rank,score,
selection_mode,reference_close,limit_price,open_price,status,fill_price,atr,
tp_price,sl_price,shares,message
```

`trades.csv`：

```text
trade_id,ticker,signal_date,entry_date,exit_date,entry_price,exit_price,shares,
atr,tp_price,sl_price,days_held,exit_reason,buy_cost,sell_cost,slippage_cost,
gross_pnl,net_pnl,net_return_pct
```

`equity.csv`：

```text
date,cash,market_value,equity,daily_return,cumulative_return,
benchmark_close,benchmark_equity,benchmark_return,excess_return,drawdown
```

CSV 應由 authoritative JSON state 確定性重建，而不是 append-only 寫入；這讓 crash recovery 與重跑不會產生重複列。

### 2.6 績效 vs 0050

- 基準起點：第一筆 `equity_curve` 的 XTAI session。
- `benchmark_equity = initial_capital × 0050_close / first_0050_close`。
- 0050 使用未調整收盤價的 price return，明確標示「不含股利與交易成本」。策略主曲線使用扣除所有定義成本後的淨權益。
- 報告至少輸出：策略累積報酬、0050 累積報酬、超額報酬、最大回撤、成交率、交易數、勝率、平均盈虧、profit factor。
- 少於 30 個有效 daily returns 時，Sharpe 顯示 `N/A（樣本不足）`；達 30 日後才顯示年化 Sharpe。
- 尚未有成交時仍要輸出合法報告，策略報酬為現金的 0%，交易型統計顯示 N/A。
- 圖表上半部為兩條 normalized equity，下半部為策略 drawdown；不得用雙 Y 軸誇大差異。

### 2.7 Telegram（選用）

只從環境變數讀取：

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

通知時機：選股完成、09:30 成交/撤單、TP/SL/TIME 出場、每日績效摘要。通知失敗只寫 warning，不回滾已完成的 state transaction；token 不得進 log、JSON、CSV 或 exception message。

---

## 3. 實作建議與任務

### Task 1: Top-7 讀取與二次篩選

**Files:**
- Create: `independent_sim.py`
- Create: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: `artifacts/orders_YYYYMMDD.json` 的 `{"orders": [...]}`。
- Produces: `load_orders()` 與 `select_candidates()`，回傳排序穩定且最多 2 筆的 order dict。

- [ ] 先寫失敗測試，固定以下案例：rank 2 檔入選；`--max-price 200` 不向高價股遞補；manual 非 Top-7 被拒絕；held/pending 被排除；同分以 ticker 穩定排序。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "load_orders or select_candidates" -v`，確認因函式尚不存在而失敗。
- [ ] 實作嚴格 schema 驗證、以檔名日期而非 mtime 選檔、XTAI 下一交易日驗證，以及自動/低價/手動三種選擇模式。
- [ ] 重跑相同測試，預期全部 PASS。
- [ ] 建議提交：`feat: add independent top-2 selector`。

### Task 2: State、原子寫入與 CLI 初始化

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: `Path data_dir` 與一次性 config。
- Produces: `load_state()`、`save_state_atomic()`、`init`、`status`；schema 與本文件 2.4 完全一致。

- [ ] 先寫失敗測試：初始化 200,000；第二次 init 不覆蓋既有交易；broken JSON fail-closed；atomic replace 後無 temporary file；相同 `run_id` 被拒絕重做。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "state or init or idempotent" -v`，確認失敗原因符合預期。
- [ ] 實作 state validation、lock、temporary-file + fsync + replace，並加入 argparse subcommands。
- [ ] 重跑測試，預期全部 PASS；再跑 `python independent_sim.py --help`，預期列出 `init/open/close-and-plan/report/status`。
- [ ] 建議提交：`feat: add isolated simulation state`。

### Task 3: 收盤選股、ATR 與 pending orders

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: Task 1 selected orders、Task 2 state、截至 signal date 的 close series。
- Produces: `compute_v85_atr20()`、`plan_orders()` 與 `close-and-plan`。

- [ ] 先寫失敗測試：ATR20 等於 `pct_change().abs().rolling(20).mean() * close`；order ATR null 時正確重算；資料不足記 `SKIPPED_NO_ATR`；TP/SL 尚未以 signal close 固化；同日重跑不重複 pending/event。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "atr or plan" -v`，確認 RED。
- [ ] 實作 ATR fallback、slot/cash 前置檢查、order_id、selection audit fields 與 pending order 建立。
- [ ] 重跑測試，預期全部 PASS。
- [ ] 建議提交：`feat: plan next-session top-2 orders`。

### Task 4: 09:30 限價成交與撤單

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: pending orders、當日 fresh open bars、`evaluate_buy_limit_at_open()`。
- Produces: `execute_open_orders()`、position、唯一 terminal order event。

- [ ] 先寫參數化失敗測試：open 小於/等於 limit 成交；open 大於 limit 撤單；None/NaN/0/stale bar 撤單；第一檔撤單不遞補；資金不足撤單；due order 執行後不殘留。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "open or fill or cancel" -v`，確認 RED。
- [ ] 用共用純函式實作 open-only 判定；成交後以 fill +4 ATR / fill -3 ATR 重算 TP/SL，再扣股款與買進成本。
- [ ] 重跑測試，預期全部 PASS；另跑 `pytest tests/test_order_execution.py tests/test_event_backtest_limit_orders.py -v`，確保沒有成交語意漂移。
- [ ] 建議提交：`feat: simulate opening limit orders`。

### Task 5: TP/SL/TIME、交易成本與每日權益

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: positions 與當日 fresh OHLC。
- Produces: `settle_positions()`、`mark_equity()`、closed trade records。

- [ ] 先寫失敗測試：同日 TP/SL 皆碰時 SL 優先；SL/TP gap 以 open 出場；第 20 個有效 session 以 close TIME；缺 bar 不增加天數；成本後 cash/equity 可精確對帳。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "tp or sl or time or equity or cost" -v`，確認 RED。
- [ ] 實作出場優先序、20-session counter、買賣成本、sell slippage 與每日 mark-to-market。
- [ ] 重跑測試，預期全部 PASS。
- [ ] 建議提交：`feat: track exits and daily equity`。

### Task 6: CSV、0050 對比與圖表

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`

**Interfaces:**
- Consumes: authoritative state 與 0050 close series。
- Produces: 2.5 所列 CSV、`performance.md`、`performance.png`。

- [ ] 先寫失敗測試：CSV 欄位固定且無重複；0050 首日 normalized equity 等於 initial capital；最大回撤範例正確；少於 30 日 Sharpe 為 None；零交易仍能產報。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "csv or benchmark or performance or report" -v`，確認 RED。
- [ ] 實作 deterministic export、benchmark alignment、績效統計與 matplotlib 圖。
- [ ] 重跑測試，預期全部 PASS；以 fixtures 執行 `python independent_sim.py report --data-dir <temp-dir>`，確認四個輸出檔可重建。
- [ ] 建議提交：`feat: report top-2 performance vs 0050`。

### Task 7: Telegram 與獨立排程

**Files:**
- Modify: `independent_sim.py`
- Modify: `tests/test_independent_sim.py`
- Create: `.github/workflows/independent_sim.yml`（選用 GitHub Actions 時）

**Interfaces:**
- Consumes: command result summary 與 Telegram secrets。
- Produces: fail-soft 通知，以及 09:35 `open`、18:05 `close-and-plan` 的 XTAI-guarded automation。

- [ ] 先寫失敗測試：未設 secrets 時不發送；HTTP error 不改 state；訊息不包含 token；同一 run_id 不重複通知。
- [ ] 執行 `pytest tests/test_independent_sim.py -k "telegram or notification" -v`，確認 RED。
- [ ] 使用標準函式庫 HTTP client 實作 `--notify`；workflow secrets 只映射到 environment。
- [ ] 新 workflow 使用與現有流程相同的 `concurrency.group=ai-report-main`，台北週一至六 09:35/18:05 觸發，並在命令前以 XTAI fail-closed 判斷交易日。18:05 job 應先 pull 當日最新 `orders_*.json`，再執行 `close-and-plan`。
- [ ] 跑 workflow YAML lint（若 repository 有 actionlint 就執行 `actionlint .github/workflows/independent_sim.yml`），並以 `workflow_dispatch` 在 dry-run data dir 驗證一次。
- [ ] 建議提交：`feat: automate independent simulation`。

---

## 4. 驗證與停止條件

實作完成前必須取得以下真實輸出，不可只憑推測宣告完成：

```bash
pytest tests/test_independent_sim.py -v
pytest tests/test_order_execution.py tests/test_ai_report_orders.py tests/test_event_backtest_limit_orders.py -v
python independent_sim.py --help
python independent_sim.py init --capital 200000 --data-dir "$(mktemp -d)"
```

離線 fixture smoke test 必須證明：

1. D 日 Top-7 最多只建立 2 筆 pending orders。
2. D+1 open 一筆成交、一筆開高撤單，且沒有第 3 名遞補。
3. 成交 position 的 TP/SL 以 fill price 與 signal-date ATR 重算。
4. 同一 `open` 或 `close-and-plan` 重跑，cash、positions、events、CSV 都不變。
5. 20 個有效持有 session 後 TIME 出場。
6. report 同時顯示策略與 0050，且起點與日期對齊。

停止條件：以上驗證通過、沒有修改既有檔案、從當日開始能成功留下第一筆 selection/order event 即完成 v1。不要在 v1 加入盤中 1 分 K first-touch、動態調參、新聞/籌碼因子、資料庫、Web UI 或實際券商下單；這些都需要獨立策略版本與新驗證計畫。

## 5. 立即使用建議

建議先跑預設 `top2_score_v1` 至少 60 個 XTAI session，不中途調整價格上限、部位或 ATR 倍數。若同時想驗證低價股，應使用另一個 data dir（例如 `independent_sim_data_low200/`）平行跑固定 `--max-price 200`，避免把兩種規則混成無法解釋的一條績效曲線。

最小可行上線順序是 Task 1–5；完成後已能開始累積模擬資料。Task 6 可在同一週補上績效輸出，Task 7 只在需要無人值守與 Telegram 時加入。

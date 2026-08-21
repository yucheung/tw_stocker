# 訊號日收盤限價單模擬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 v8.5 的回測、orders artifact 與 paper tracker 統一為「訊號日收盤後以該收盤價掛買進限價單；次一交易日開盤價不高於限價才以開盤價成交，開高則於 09:30 撤單」的方案 1。

**Architecture:** 在 `strategy/order_execution.py` 建立無副作用的限價單判定函式，讓 `strategy/event_backtest.py` 與 `paper_tracker.py` 共用同一套邊界語意。`ai_report.py` 只負責產生帶有限價與有效期的機器可讀委託；`verify_daily_strategy.py` 只讀驗證訊號、委託與成交是否符合此模型。日 K 模擬不猜測 09:00–09:30 的盤中路徑：開高後即視為未成交，09:30 撤單，當日 low 不得回推成交。

**Tech Stack:** Python 3.13、pandas、yfinance、exchange_calendars、pytest / unittest、JSON orders artifact。

## Global Constraints

- 買進限價 `limit_price` 固定為訊號日未調整收盤價，不加 ATR、tick 或滑價。
- 委託只對訊號日的次一個 XTAI session 有效；不得順延到第二個交易日。
- `open_price <= limit_price` 含等號，以未調整 `open_price` 全數成交；價格改善必須留給買方。
- `open_price > limit_price` 時不成交，狀態記為 09:30 `CANCELLED_OPEN_ABOVE_LIMIT`；當日 high/low/close 不得改寫此結果。
- 開盤價缺漏、NaN、無限大或 `<= 0` 時不可退回收盤價；09:30 撤單並記為 `CANCELLED_NO_OPEN_PRICE`。
- 買進成交價必須等於 raw open，不可以 `open * (1 + slippage)` 超過限價。手續費另計；現有賣出滑價與賣出成本不在本案範圍。
- 移除買進的雙邊 `abs(open - reference_close) > gap_limit * ATR` 成交 gate。方案 1 已完整定義成交條件：大幅跳空下跌仍符合買進限價，不能被舊 gap filter 拒絕。
- 模擬為全數成交，不模擬排隊、部分成交、零股集合競價或 09:00–09:30 盤中觸價；這些需要逐筆或分鐘資料，不得由日 K 臆測。
- 訊號排名與現有 Top-K / max positions 規則維持不變。委託集合必須在開盤前依 rank 選定；開高撤單會留下空 slot，不得在看見開盤價後才用原本未掛出的低排名候選補單。
- TP/SL 仍在實際成交後以 `fill_price` 為錨重算；未成交委託不得建立部位或 TP/SL。
- 保留舊 orders JSON 的讀取相容性，但新 artifact 必須明確標示 `entry_model = "signal_close_limit_next_open_v1"`。舊 `gap_limit_atr` 可被讀取但不參與成交判定。
- 不修改模型評分、regime、position sizing、出場順序、停利停損倍數或最長持有日數。

---

## 1. 限價單狀態與精確成交邏輯

### 1.1 時序

```text
訊號日 t 收盤後
    │
    ├─ 讀取 close[t] = limit_price
    ├─ 建立 BUY LIMIT（execution_date = next XTAI session）
    └─ 狀態 PENDING_OPEN
             │
次一交易日 t+1 09:00
             ├─ open[t+1] <= limit_price
             │      └─ FILLED @ open[t+1]，建立部位
             │
             ├─ open[t+1] > limit_price
             │      └─ 保持未成交，09:30 CANCELLED_OPEN_ABOVE_LIMIT
             │
             └─ open[t+1] 無效/缺漏
                    └─ 09:30 CANCELLED_NO_OPEN_PRICE
```

09:30 是委託生命週期的撤單時點，不是另一個可用日 K 推導的成交點。因此開高後當日 `low <= limit_price` 也不得判成成交；若未來取得可靠的 1 分 K 或委託回報，另開 v2 模型，不在 v1 內混用。

### 1.2 價格與成本

```text
limit_price = Close[signal_session]

if is_finite(Open[execution_session]) and 0 < Open <= limit_price:
    fill_price = Open[execution_session]
    entry_gross = fill_price × shares
    cash_debit = entry_gross × (1 + buy_cost_rate)
else:
    fill_price = None
    cash_debit = 0
```

- `Open == limit_price` 必須成交。
- `Open < limit_price` 必須以較佳的 Open 成交，不可強制以 limit 成交。
- 買進手續費可使現金支出高於 `limit_price × shares`，但不得改變 `fill_price`。
- 現有 paper tracker 的 `slippage = 0.003` 不再加在買進端；否則 `Open == limit` 時會產生高於限價的假成交。

### 1.3 多檔委託與 slot

1. 依訊號日 rank 由小到大處理；缺 rank 的舊單維持 JSON 原始順序。
2. 在訊號日收盤後依當時持倉計算 `submission_slots = max_positions - len(positions)`，只對前 `min(top_k, submission_slots)` 名建立次日委託。
3. 次日開盤只評估前一晚已建立的委託。若其中某單開高撤單，該 slot 當日留空；不得在已知 open 後才補掛第 `top_k + 1` 名。
4. 當日出場若在次日開盤模擬前結算，釋放的 slot 也不能追溯增加前晚的委託；新 slot 只可供當日收盤後的下一批訊號使用。
5. 舊 snapshot 若含有超過 execution day 可用 slot 的 due orders，超出部分以 `CANCELLED_NO_CAPACITY` 結案，不留到下一日。
6. 資金或保留現金不足時以 `CANCELLED_INSUFFICIENT_CASH` 結案，不建立零股數部位。

### 1.4 共用介面

```python
from dataclasses import dataclass
from typing import Literal

OpenDecisionStatus = Literal[
    "FILLED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
]

TerminalOrderStatus = Literal[
    "FILLED",
    "CANCELLED_OPEN_ABOVE_LIMIT",
    "CANCELLED_NO_OPEN_PRICE",
    "CANCELLED_NO_CAPACITY",
    "CANCELLED_INSUFFICIENT_CASH",
]

@dataclass(frozen=True)
class OpenLimitDecision:
    status: OpenDecisionStatus
    limit_price: float
    open_price: float | None
    fill_price: float | None

    @property
    def filled(self) -> bool:
        return self.status == "FILLED"

def evaluate_buy_limit_at_open(
    limit_price: float,
    open_price: float | None,
) -> OpenLimitDecision:
    """Evaluate the opening-auction portion of a one-day buy limit order."""
```

`limit_price` 非 finite 或 `<= 0` 代表內部委託資料已壞，函式應拋 `ValueError`；`open_price` 無效則是外部市場資料不足，回傳 `CANCELLED_NO_OPEN_PRICE`，兩者不可混為同一種錯誤。

## 2. 檔案與責任

| 路徑 | 動作 | 責任 |
|---|---|---|
| `strategy/order_execution.py` | 新增 | 限價單狀態、輸入驗證、開盤成交判定；不 import pandas/yfinance |
| `tests/test_order_execution.py` | 新增 | 純函式邊界、價格改善、缺價與非法 limit 測試 |
| `strategy/event_backtest.py` | 修改 | 訊號日 close 作 limit，依共用函式決定次日 open 是否成交，移除買進 gap gate 與 entry slippage |
| `tests/test_event_backtest_limit_orders.py` | 新增 | 用最小 DataFrame 回歸開低、等價、開高、大跳空下跌、日 low 假觸價與開盤前委託集合預選 |
| `ai_report.py` | 修改 | orders artifact 標示新 entry model、DAY/09:30 有效期，不再輸出會被誤解為成交 gate 的 gap 欄位 |
| `paper_tracker.py` | 修改 | 共用限價判定，禁止 open 缺漏時改用 close，當日結束 due orders，記錄成交/撤單原因 |
| `test_paper_tracker_edge_cases.py` | 修改 | 將現有 `open=None` 預期由「以 close 開倉」改為「撤單且不開倉」，新增開高/等價/開低/不延單測試 |
| `verify_daily_strategy.py` | 修改 | Top-7 進場驗證由 ATR gap 改為 close-limit 成交決策，並核對已成交部位不高於 limit |
| `tests/test_verify_daily_strategy.py` | 修改 | 替換 gap filter 案例，覆蓋預期成交、預期撤單、錯誤成交與尚未到 execution session |
| `README.md` | 修改 | 將「次日開盤成交」更正為完整限價單語意與日 K 模擬限制 |

## 3. orders artifact schema

新產生的買單至少包含：

```json
{
  "signal_date": "2026-08-19",
  "execution_date": "2026-08-20",
  "ticker": "2330",
  "side": "buy",
  "order_type": "limit",
  "limit_price": 1180.0,
  "reference_close": 1180.0,
  "entry_model": "signal_close_limit_next_open_v1",
  "time_in_force": "DAY_UNTIL_0930",
  "cancel_time": "09:30:00",
  "timezone": "Asia/Taipei",
  "rank": 1,
  "model_version": "v8.5"
}
```

不再將 `model_entry_ref = "next_open"` 當成足夠的語意，因為它無法表達限價與未成交。讀取舊 artifact 時，若有 `limit_price`，paper tracker 仍套用新限價判定；若只有 `reference_close` 或 `entry`，依序退回為 limit 並輸出 legacy schema 警示。三者都無效時拒絕該筆單，不自行從當日市價補值。

Paper snapshot 的 `pending_orders` 每筆另保留 `rank` 與上述 execution metadata。為了可稽核未成交，在 snapshot 新增 append-only `order_events`：

```python
{
    "order_id": "2026-08-19:2330:buy",
    "ticker": "2330",
    "signal_date": "2026-08-19",
    "execution_date": "2026-08-20",
    "limit_price": 1180.0,
    "open_price": 1190.0,
    "status": "CANCELLED_OPEN_ABOVE_LIMIT",
    "fill_price": None,
    "event_time": "2026-08-20T09:30:00+08:00"
}
```

`load_data()` 必須對舊 snapshot 以 `setdefault("order_events", [])` 升級，不要求重置 `paper_equity.json`。同一 `order_id` 在重複執行時不得寫入第二個 terminal event。

## 4. 實作任務

### Task 1: 建立共用限價成交核心

**Files:**
- Create: `strategy/order_execution.py`
- Create: `tests/test_order_execution.py`

**Interfaces:**
- Consumes: Python `float | None`
- Produces: `OpenLimitDecision`, `evaluate_buy_limit_at_open(limit_price, open_price)`

- [ ] **Step 1: 先寫六個失敗測試**

```python
import math
import pytest

from strategy.order_execution import evaluate_buy_limit_at_open


@pytest.mark.parametrize(
    ("open_price", "status", "fill_price"),
    [
        (99.0, "FILLED", 99.0),
        (100.0, "FILLED", 100.0),
        (100.01, "CANCELLED_OPEN_ABOVE_LIMIT", None),
        (None, "CANCELLED_NO_OPEN_PRICE", None),
        (float("nan"), "CANCELLED_NO_OPEN_PRICE", None),
        (0.0, "CANCELLED_NO_OPEN_PRICE", None),
    ],
)
def test_evaluate_buy_limit_at_open(open_price, status, fill_price):
    decision = evaluate_buy_limit_at_open(100.0, open_price)
    assert decision.status == status
    assert decision.fill_price == fill_price


@pytest.mark.parametrize("limit", [0.0, -1.0, math.nan, math.inf])
def test_invalid_limit_is_internal_order_error(limit):
    with pytest.raises(ValueError, match="limit_price"):
        evaluate_buy_limit_at_open(limit, 99.0)
```

- [ ] **Step 2: 執行測試並確認因 module 尚未存在而失敗**

Run: `pytest -q tests/test_order_execution.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'strategy.order_execution'`.

- [ ] **Step 3: 實作最小純函式與 frozen dataclass**

`evaluate_buy_limit_at_open()` 先驗證 limit，再驗證 open，最後只使用 `open_price <= limit_price` 分支。函式內不可讀日期、網路、DataFrame 或全域設定。

- [ ] **Step 4: 執行核心測試**

Run: `pytest -q tests/test_order_execution.py`

Expected: `10 passed` and exit code 0.

- [ ] **Step 5: 提交核心決策單元**

```bash
git add strategy/order_execution.py tests/test_order_execution.py
git commit -m "feat: add opening limit fill model"
```

### Task 2: 將 event backtest 切換為訊號收盤限價

**Files:**
- Modify: `strategy/event_backtest.py`
- Create: `tests/test_event_backtest_limit_orders.py`

**Interfaces:**
- Consumes: `evaluate_buy_limit_at_open()` from Task 1, `close_df.iloc[i-1]`, `open_df.iloc[i]`
- Produces: 只含符合 limit 的 trades/equity，成交列 `Entry_Price <= Signal_Limit`

- [ ] **Step 1: 以三個 session 最小資料寫回歸測試**

建立共用 fixture，訊號 session 的 close 為 100、score 為 3、close > MA60，次日的 high/low 可由個別測試改寫。至少斷言：

```python
def test_gap_down_fills_at_better_open(run_case):
    trades, _ = run_case(next_open=95.0, next_low=90.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(95.0)


def test_equal_open_fills_at_limit(run_case):
    trades, _ = run_case(next_open=100.0, next_low=99.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(100.0)


def test_gap_up_does_not_fill_even_if_daily_low_touches_limit(run_case):
    trades, _ = run_case(next_open=105.0, next_low=95.0)
    assert trades.empty


def test_large_gap_down_is_not_rejected_by_old_atr_gap_filter(run_case):
    trades, _ = run_case(next_open=70.0, next_low=65.0, atr=5.0)
    assert trades.iloc[0]["Entry_Price"] == pytest.approx(70.0)
```

測試中將 `slippage` 設為非零，仍要斷言買進 `Entry_Price == Open`，證明限價沒有被買進滑價突破。另建立三檔候選、兩個 submission slots 的案例：前一晚只選第一、二名；次日第一名開高撤單、第二名成交，第三名即使開低也不得在開盤後補單。

- [ ] **Step 2: 執行新測試並確認舊回測的無條件 next-open 行為造成失敗**

Run: `pytest -q tests/test_event_backtest_limit_orders.py`

Expected: 開高案例出現交易、大跳空下跌被 gap filter 排除，測試 FAIL。

- [ ] **Step 3: 改寫候選與成交流程**

在動量與 mean-reversion 多頭分支都以 `prev_close` 當 limit、`entry_price` 當 open。先只用訊號日已知資料完成排名、板塊/相關性過濾與 `min(effective_top_k, submission_slots)` 預選，才對這批已掛委託呼叫共用成交函式。預選階段不得讀取次日 open；某筆開高後不得用未預選的低排名候選補位。

刪除買進 `abs gap` gate 與 `actual_entry = entry_price * (1 + self.slippage)`，改為 `actual_entry = decision.fill_price`。`gap_aware_sizing` 若保留，只能在已成交的開低單上調整 sizing，不得改變 fill eligibility。回測開始訊息將 `Gap(...)` 過濾器文字改為 `BuyLimit(signal close, cancel 09:30)`。

- [ ] **Step 4: 執行新回歸與現有策略測試**

Run: `pytest -q tests/test_event_backtest_limit_orders.py test_evaluation.py test_benchmark.py`

Expected: all passed, exit code 0.

- [ ] **Step 5: 提交回測切換**

```bash
git add strategy/event_backtest.py tests/test_event_backtest_limit_orders.py
git commit -m "feat: enforce signal-close buy limits in backtest"
```

### Task 3: 更新 orders artifact 合約

**Files:**
- Modify: `ai_report.py`
- Create: `tests/test_ai_report_orders.py`

**Interfaces:**
- Consumes: 訊號日 close、next XTAI session、rank 與現有 TP/SL/sizing metadata
- Produces: 符合第 3 節 schema 的 `artifacts/orders_YYYYMMDD.json`

- [ ] **Step 1: 先寫 artifact contract 失敗測試**

將 orders dict 的建立抽成 `build_order_record(...)`，測試不執行完整報表與網路下載：

```python
def test_order_record_declares_close_limit_lifecycle():
    order = build_order_record(
        signal_date="2026-08-19",
        execution_date="2026-08-20",
        ticker="2330",
        rank=1,
        score=3.2,
        reference_close=1180.0,
        strategy_config=BASE_CONFIG,
    )
    assert order["order_type"] == "limit"
    assert order["limit_price"] == 1180.0
    assert order["entry_model"] == "signal_close_limit_next_open_v1"
    assert order["time_in_force"] == "DAY_UNTIL_0930"
    assert order["cancel_time"] == "09:30:00"
    assert order["timezone"] == "Asia/Taipei"
    assert "gap_limit_atr" not in order
```

- [ ] **Step 2: 執行 contract 測試並確認函式/欄位尚未存在**

Run: `pytest -q tests/test_ai_report_orders.py`

Expected: FAIL because `build_order_record` or required fields do not exist.

- [ ] **Step 3: 抽出 builder 並更新報表文案**

將現有 inline `orders.append({...})` 移入 builder。HTML 交易計畫以「今日收盤價 X 掛買進限價；明日開盤 <= X 以開盤價成交，否則 09:30 撤單」取代「明日開盤價附近進場」。

- [ ] **Step 4: 執行 artifact 與語法驗證**

Run: `pytest -q tests/test_ai_report_orders.py && python -m py_compile ai_report.py`

Expected: all passed and exit code 0.

- [ ] **Step 5: 提交 artifact 合約**

```bash
git add ai_report.py tests/test_ai_report_orders.py
git commit -m "feat: publish close-limit order metadata"
```

### Task 4: 將 paper tracker 切換為當日限價單生命週期

**Files:**
- Modify: `paper_tracker.py`
- Modify: `test_paper_tracker_edge_cases.py`

**Interfaces:**
- Consumes: Task 1 決策函式、Task 3 orders schema；legacy fallback `limit_price -> reference_close -> entry`
- Produces: positions、terminal `order_events`、當日清空的 due orders

- [ ] **Step 1: 先將 open 缺漏的舊測試改為正確的失敗預期**

```python
def test_open_none_cancels_without_close_fallback(self):
    data = self._run_open(None)
    self.assertNotIn("3231", data["positions"])
    self.assertEqual(data["pending_orders"], [])
    self.assertEqual(data["order_events"][-1]["status"], "CANCELLED_NO_OPEN_PRICE")
```

再新增：open 149 / limit 150 以 149 成交；open 150 成交；open 151 不成交，即使 low 145 仍撤單；ATR 很小但 open 130 仍成交；執行日後 pending order 不得留存。

- [ ] **Step 2: 執行 paper 邊界測試並確認舊 close fallback / gap filter 使其失敗**

Run: `pytest -q test_paper_tracker_edge_cases.py`

Expected: FAIL on missing-open, gap-up, large-gap-down and event assertions.

- [ ] **Step 3: 升級 snapshot 與 order parser**

`load_data()` 對現有檔案執行 `setdefault("pending_orders", [])` 與 `setdefault("order_events", [])`。`extract_signals_from_orders()` 保留 `rank/order_type/entry_model/time_in_force/cancel_time/timezone`，並以明確 helper 解析 legacy limit；非法 limit 輸出警示並略過，不讓整批中斷。

- [ ] **Step 4: 改寫 due order 執行器**

刪除 `open -> close` fallback 與 ATR gap filter。依 rank 評估每筆 due order；開盤決策為 `FILLED` 候選時才進入 sizing 與現金檢查，資金通過後才建倉並以 `recompute_tp_sl()` 建立出場價，不通過則將最終 status 改為 `CANCELLED_INSUFFICIENT_CASH`。每筆 due order 只 append 一個最終 terminal event。買進現金扣款改為 `gross + gross * buy_cost_rate`，不再加 paper 的 0.3% buy slippage；賣出端維持原狀。

只有 `execution_date > today` 的單可進入 `deferred`。所有 due order 不論成交、開高、缺價、資金不足或 slot 不足，當次執行後都不得回到 `pending_orders`。登錄當日新訊號時，依當日收盤後尚在持有的 positions 計算 submission slots，在寫入 `pending_orders` 前就截斷 rank 較後的候選。

- [ ] **Step 5: 執行 paper 回歸與語法檢查**

Run: `pytest -q test_paper_tracker_edge_cases.py && python -m py_compile paper_tracker.py`

Expected: all passed and exit code 0.

- [ ] **Step 6: 提交 paper lifecycle**

```bash
git add paper_tracker.py test_paper_tracker_edge_cases.py
git commit -m "feat: model paper buy limit lifecycle"
```

### Task 5: 讓每日 verifier 稽核新成交模型

**Files:**
- Modify: `verify_daily_strategy.py`
- Modify: `tests/test_verify_daily_strategy.py`

**Interfaces:**
- Consumes: `daily_signals`, `pending_orders`, `order_events`, positions, next-session open
- Produces: `signal_consistency` 的 PASS/WARNING/CRITICAL 與 limit-order metrics

- [ ] **Step 1: 以新規則替換 ATR gap 驗證測試**

最少覆蓋：

```python
def test_next_open_below_signal_close_is_expected_fill(...):
    assert result.metrics["expected_fills"] == ["2330"]


def test_next_open_above_limit_is_expected_cancel_not_violation(...):
    assert result.metrics["expected_cancellations"] == ["2330"]
    assert result.severity != "CRITICAL"


def test_position_above_limit_is_critical_impossible_fill(...):
    assert result.severity == "CRITICAL"
    assert "entry > limit" in " ".join(result.details)


def test_missing_next_open_is_indeterminate_warning(...):
    assert result.severity == "WARNING"
    assert result.metrics["indeterminate"] is True
```

保留「次一 session 尚未發生」為 WARNING，不可提前宣告撤單或成交。

- [ ] **Step 2: 執行定點測試並確認舊 ATR gap 邏輯造成失敗**

Run: `pytest -q tests/test_verify_daily_strategy.py -k 'signal_consistency or limit'`

Expected: FAIL because metrics and close-limit comparisons do not exist.

- [ ] **Step 3: 更新 validator**

保留 score >= 2.0、Close > MA60、Top-7/排名與資料 coverage 規則，只將進場子規則改為：

```text
limit = signal-session close
next_open <= limit  -> expected FILLED at next_open
next_open > limit   -> expected CANCELLED_OPEN_ABOVE_LIMIT at 09:30
missing next_open   -> WARNING / indeterminate
```

若 `order_events` 存在，核對 status、limit/open/fill；若已有同 ticker、同 signal/execution date 的新 position，核對 `position.entry == next_open` 且 `entry <= limit`。對預期撤單的訊號，只有 snapshot 能證明實際建倉或 event 記為 FILLED 時才列 CRITICAL；`daily_signals` 本身不等於成交。

- [ ] **Step 4: 執行 verifier 全套測試**

Run: `pytest -q tests/test_verify_daily_strategy.py`

Expected: all passed and exit code 0.

- [ ] **Step 5: 提交 verifier 對齊**

```bash
git add verify_daily_strategy.py tests/test_verify_daily_strategy.py
git commit -m "fix: verify close-limit order outcomes"
```

### Task 6: 文件、整合驗證與新舊結果對照

**Files:**
- Modify: `README.md`
- Modify: `PLAN_simulation.md` only to check completed boxes during execution

**Interfaces:**
- Consumes: Tasks 1–5 完成的行為與真實測試輸出
- Produces: 可重現的整合驗證紀錄與使用者說明

- [ ] **Step 1: 更新 README 進場規則**

文件需明記 signal session、limit price、execution session、等號邊界、價格改善、09:30 撤單、不延單，以及「日 K v1 不模擬開高後 30 分鐘內觸價」的限制。不可再使用「開盤價附近」或「隔日必然成交」文案。

- [ ] **Step 2: 執行全套自動測試**

Run: `pytest -q`

Expected: all passed and exit code 0.

- [ ] **Step 3: 執行語法檢查**

Run: `python -m py_compile strategy/order_execution.py strategy/event_backtest.py ai_report.py paper_tracker.py verify_daily_strategy.py`

Expected: no output and exit code 0.

- [ ] **Step 4: 執行固定資料的 A/B 模擬**

使用同一組已緩存 OHLCV 與相同參數各跑一次舊 baseline 與新 limit model，輸出並保留以下對照：訊號數、委託數、成交數、開高撤單數、缺價撤單數、fill rate、總報酬、Sharpe、MDD、總手續費與最大同日新倉數。斷言 `new_trade_count <= submitted_order_count`、每筆新模型買進 `fill_price <= limit_price`、且不存在 execution date 之後才成交的委託。

- [ ] **Step 5: 檢查工作區與提交文件**

Run: `git diff --check && git status --short`

Expected: `git diff --check` exit code 0；status 只出現本計畫列出的檔案。

```bash
git add README.md PLAN_simulation.md
git commit -m "docs: document signal-close limit simulation"
```

## 5. 驗收條件

1. 訊號日 close 100、次日 open 99：回測與 paper 都以 99 成交，且 TP/SL 以 99 為錨。
2. 訊號日 close 100、次日 open 100：以 100 成交。
3. 訊號日 close 100、次日 open 101：不成交，09:30 撤單，不產生部位、費用或 TP/SL。
4. 同上但當日 low 95：日 K v1 結果仍為撤單，不以 low 偽造 09:30 前成交。
5. 訊號日 close 100、ATR 5、次日 open 70：仍以 70 成交，證明舊的雙邊 ATR gap filter 已不再干擾方案 1。
6. 次日 open 缺漏：不使用 close fallback，以 `CANCELLED_NO_OPEN_PRICE` 結案。
7. 買進滑價設定為非零時，任一 `fill_price` 仍不得高於 limit；買進手續費可獨立計入成本。
8. 同日有三個候選、兩個 submission slots：前晚只掛第一、二名；若第一名開高、第二名開低，當日只成交第二名，不得事後補掛第三名。
9. 所有 due order 在 execution session 後都有且只有一個 terminal event，並從 `pending_orders` 移除。
10. 回測、orders artifact、paper tracker、verifier 與 README 對成交條件的描述完全一致，不再出現「無條件 next-open」或「ATR gap 決定買進」的活躍邏輯。

## 6. 明確不在本案範圍

- 串接真實券商 API 或在 09:30 發送外部撤單指令。
- 用 1 分 K、tick 或委託簿模擬開高後、09:30 前的觸價與排隊。
- 部分成交、市場深度、漲跌停排隊、整股/零股不同撮合時點。
- 重新設計 position sizing、regime、Top-K、TP/SL、time exit 或賣出滑價。
- 以新模型的回測績效自動宣告可實盤；A/B 數據只用於顯示成交模型影響。

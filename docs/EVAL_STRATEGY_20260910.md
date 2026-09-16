# 策略成效評估與修復計畫 — MR20 / TOP7

**評估日期**：2026-09-10
**評估對象**：虛擬交易（paper trading）框架下的 MR20 與 TOP7（v8.5）
**評估前提**：使用者要的是模擬交易，不是真實下單。因此本文不談券商 API、風控法規，只談**「模擬出來的數字有多可信」**與**「兩個策略在模擬框架內表現如何」**。
**約束**：本次評估未修改任何程式碼或既有檔案，唯一寫入為本檔。

---

# 第一部分 — 策略成效評估

## 0. 資料來源盤點

| 代號 | 來源檔案 | 產生程式 | 內容 |
|---|---|---|---|
| S1 | `paper_equity.json` | `paper_tracker.py` | TOP7（v8.5）線上虛擬交易帳本 |
| S2 | `independent_sim_data_mr20/{state.json,equity.csv,orders.csv,trades.csv}` | `independent_sim.py -s mr20` | MR20 線上虛擬交易帳本 |
| S3 | `independent_sim_data_top7/{state.json,equity.csv,orders.csv}` | `independent_sim.py -s top2_score_v1` | **名為 top7、實為 top2_score_v1** 的第三條線 |
| S4 | `artifacts/eval_mr20_60d.md` | `eval_trader.py --strategy mr20 --days 60` | MR20 離線 60 日回測 |
| S5 | `artifacts/eval_baseline_60d.md` | `eval_trader.py --strategy baseline --days 60` | 對照組離線 60 日回測 |
| S6 | `BACKTEST_REPORT_20260815.md` | `verify_strategy.py` / walk-forward | TOP7（v8.5）1200 日回測 + 4 段 WF OOS |
| S7 | `artifacts/mr20/orders_mr20_*.json`（17 檔，8/18–9/09） | `ai_report.py` / `run_mr20.sh` | MR20 每日訂單與漏斗診斷 |

> **重要**：S3 不是 TOP7。`independent_sim_data_top7/state.json` 的 `strategy_id` 是 `top2_score_v1`，`config` 為 `max_positions: 2 / position_size: 0.45 / initial_capital: 20000`；`orders.csv` 的 `order_id` 前綴全是 `top2_score_v1:`。TOP7 的真正帳本是 S1（`paper_tracker.py`，`MAX_POSITIONS = 7`，初始 200,000）。**前次「independent_sim_data_top7 跑的不是 TOP7」的發現，本次驗證成立。**

---

## 1. 各自成效

### 1.1 TOP7（v8.5）

#### 線上虛擬交易（來源 S1 `paper_equity.json`）

| 指標 | 數值 | 說明 |
|---|---|---|
| 樣本期間 | 2026-08-20 → 2026-09-03 | `equity_curve` 共 10 個交易日點位 |
| 期末權益 | 199,926 / 初始 200,000 | |
| **累積報酬率** | **−0.04%**（精確 −0.0370%） | |
| **0050 同期** | **+3.01%** | 103.10 (8/20) → 106.20 (9/03)，取自 `independent_sim_data_mr20/equity.csv` 與 `independent_sim_data_top7/equity.csv` 的 `benchmark_close` 欄 |
| 超額報酬 | **−3.05 pp** | |
| **最大回撤** | **−3.67%** | 本次以 `equity_curve` 自算 |
| **Sharpe** | **不可用** | 僅 9 筆日報酬；naive 年化值 0.081，t ≈ 0.02，無統計意義 |
| **交易次數** | **8 筆成交** | `order_events`：FILLED 8 / CANCELLED_OPEN_ABOVE_LIMIT 9 / CANCELLED_EXPIRED 9 / CANCELLED_NO_OPEN_PRICE 2 |
| 成交率 | 28.6%（8 / 28 終態委託） | |
| 已平倉 | 2 筆 | 3008 +43.08%（TP，持 2 日）、8039 −8.00%（SL，持 2 日） |
| **勝率** | **50%（1/2）** | n=2，不具參考價值 |
| 未平倉 | 6 檔（6213, 3653, 2408, 2301, 3406, 3008） | 佔權益約 49% |

> **「8 筆 −0.04% vs 0050 +3.01%」前次發現：驗證成立**，且本次補上了 MDD −3.67%、成交率 28.6%、已平倉僅 2 筆等關鍵細節。
>
> **附帶發現（新）**：`paper_equity.json` 最後一筆權益為 2026-09-03，檔案 mtime 為 09-04，但 MR20 那條線已跑到 09-09。TOP7 的虛擬交易帳本**已停更約 5 個交易日**，目前顯示的「−0.04%」是 9/03 的快照，不是今天的實況。

#### 歷史回測（來源 S6 `BACKTEST_REPORT_20260815.md`）

| 指標 | v8.5 | 0050 |
|---|---|---|
| 期間 | 2023-05-03 → 2026-08-12（1200 日） | 同期 |
| 年化報酬 | +29.52% | **+53.6%** |
| Sharpe | 0.943 | — |
| 最大回撤 | −28.7% | — |
| 交易次數 | 577 筆 | — |
| 勝率 / PF | 50.3% / 1.25 | — |
| 平均持有 | 12.6 日 | — |

Walk-Forward OOS（4 段非重疊）：

| 段 | 期間 | 年化 | Sharpe |
|---|---|---|---|
| 1 | 2022-07 → 2023-07 | +74.2% | 2.613 |
| 2 | 2023-07 → 2024-07 | −3.3% | 0.082 |
| 3 | 2024-07 → 2025-08 | −5.2% | −0.062 |
| 4 | 2025-08 → 2026-08 | +111.0% | 2.114 |

平均 Sharpe 1.187、OOS 衰減比 1.10（無過擬合跡象），但**報酬全部集中在 4 段中的 2 段**，且**回測期間輸給 0050 約 24 pp/年**。這是 TOP7 目前唯一具備樣本外證據的紀錄，也是它比 MR20 值得留下來的唯一理由。

---

### 1.2 MR20

#### 線上虛擬交易（來源 S2 `independent_sim_data_mr20/`）

| 指標 | 數值 |
|---|---|
| 樣本期間 | 2026-08-19 → 2026-09-09（10 個權益日點） |
| 期末權益 | 1,000,000 / 初始 1,000,000 |
| **累積報酬率** | **0.00%** |
| 0050 同期 | +4.53%（104.90 → 109.65） |
| 最大回撤 | 0.00%（無部位） |
| Sharpe | 不可用（無報酬變異） |
| **交易次數** | **0 筆成交、0 筆平倉** |
| 委託紀錄 | 全期僅 1 筆：`mr20:2026-09-07:6446:buy`，limit 1315，開盤 1345 → `CANCELLED_OPEN_ABOVE_LIMIT` |

**MR20 在線上模擬中至今一筆都沒成交。** 三週的權益曲線是一條 1,000,000 的水平線。

訊號側（來源 S7，17 個交易日的訂單檔）：

```
8/18–9/01（11 天）  0 單
9/02  1 單 (3037)   ← state.json 無此紀錄
9/03  1 單 (6446)   ← state.json 無此紀錄
9/04  0 單
9/07  1 單 (6446)   ← 唯一進入模擬的一筆
9/08  1 單 (3037)   ← state.json 無此紀錄
9/09  0 單
```

17 個交易日只產出 4 張訂單（訊號漏斗過窄），**其中 3 張從未進入模擬**（`state.json` 的 `order_events` 只有 9/07 那一筆）。這不是策略問題，是管線漏單。

#### 離線 60 日回測（來源 S4 `artifacts/eval_mr20_60d.md`）

| 指標 | MR20 | baseline 對照（S5） |
|---|---|---|
| 模擬日數 | 60 | 60 |
| 訊號 → 成交 → 撤單 | 63 → **7** → 56 | 167 → 15 → 152 |
| 撤單原因 | 開高 39 / 資金不足 17 | 開高 87 / 資金不足 65 |
| **總報酬** | **−1.83%** | +5.57% |
| 年化報酬 | −7.45% | +25.59% |
| 年化波動 | 12.29% | 17.35% |
| **Sharpe** | **−0.5795** | 1.4212 |
| Sortino / Calmar | −0.6447 / −1.1416 | 1.9287 / 5.7194 |
| **最大回撤** | **−6.52%** | −4.47% |
| **交易次數** | **7 筆** | 15 筆 |
| **勝率** | **57.14%** | 60.00% |
| 平均持有 | 13.29 日 | 7.80 日 |

> **「MR20 7 筆 −1.83%」前次發現：數字驗證成立，但結論必須推翻。** 見 §2.1 — 這個 −1.83% 量到的不是 MR20，而是「MR20 訊號中股價 ≤ 約 150 元的子集，在一組與線上不同的部位設定下」的表現。它不能當作 MR20 的成效。

**統計功效**：60 個交易日 ≈ 0.238 年。Sharpe 的 t 統計量 ≈ `SR × √years` = −0.5795 × 0.488 = **−0.28**（baseline 為 +0.69）。兩者都遠低於顯著門檻（|t| ≈ 2），**這兩個 Sharpe 在統計上與 0 沒有區別**。`independent_sim.py` 自己有「樣本 < 30 日不報 Sharpe」的護欄（`compute_performance`, line 1265），`eval_trader.py` 沒有——同一個專案兩套標準。

---

## 2. 模擬可信度：執行模型讓回測高估還是低估

專案裡目前有**三套各自為政的撮合引擎**，語意互不相同，這本身就是可信度的第一個問題：

| | `independent_sim.py`（線上 MR20/top2） | `paper_tracker.py`（線上 TOP7） | `eval_trader.py`（離線 60 日 eval） |
|---|---|---|---|
| 整股單位 | 1 股（允許零股） | 1 股（允許零股） | **1,000 股（整張）** |
| 最低手續費 | 無 | 無 | **NT$20** |
| 缺價計價 | 以**進場成本**計價 | 以**進場成本**計價 | **從市值中消失** |
| 部位設定 | 2 檔 / 45% / 保留 10% | 7 檔 / 訂單帶入（0.07–0.10） / 保留 20% | **5 檔 / 15% / 保留 20%** |

以下逐項判斷偏誤方向。

### 2.1 整股取整 → 方向相反的兩種錯，兩邊都不可信

**線上兩條線：允許買 1 股（零股），高估可成交性。**

- `independent_sim.py:967` — `shares = math.floor(target_amount / (fill_price * (1.0 + BUY_COST_RATE)))`
- `paper_tracker.py:680` — `shares = int(trade_amount / (fill_price * (1 + buy_cost_rate)))`

兩者都只向下取整到**股**，沒有整張（1,000 股）約束。實際後果就在 `paper_equity.json` 裡：

| 標的 | 進場價 | 股數 | 實投金額 | 目標部位 | 達成率 |
|---|---|---|---|---|---|
| **3008（已平倉）** | 5,350 | **1** | **5,350** | ~14,000 | **38%** |
| 3008（持倉） | 7,470 | 2 | 14,940 | ~20,700 | 72% |
| 3653 | 5,280 | 3 | 15,840 | ~20,700 | 77% |

> **「3008 一股問題」前次發現：驗證成立**，且不只 3008，所有高價股都受影響。

三個獨立的偏誤：

1. **高估可成交性**：台股整股單位是 1,000 股。1–3 股屬零股，走的是**盤中零股交易**——09:10 起每 1 分鐘一次集合競價的**獨立委託簿**，與整股簿分開撮合。模擬假設「以 09:00 整股開盤價買到 1 股」，這在台股市場結構上**不可能發生**。零股的價差與流動性也明顯較差。
2. **低估波動與回撤**：量化誤差使實際曝險遠低於名目（3008 只有目標的 38%），權益曲線被人為壓平 → **MDD 低估、年化波動低估、Sharpe 分母失真**。TOP7 的 −3.67% MDD 有一部分是「根本沒買到量」造成的假象。
3. **低估交易成本**：兩條線上引擎都只套費率（0.1425%），沒有 **NT$20 最低手續費**。5,350 元的交易模擬收 7.62 元，實際收 20 元——成本被低估 62%。`eval_trader.py:27` 有 `MIN_COMMISSION = 20.0`，線上引擎沒有。

**離線 eval：整張約束下的隱形價格上限，這是 −1.83% 失效的主因。**

`eval_trader.py:190` 把股數無條件捨去到整張：

```python
shares = (shares // LOT_SIZE) * LOT_SIZE   # LOT_SIZE = 1000
```

而部位大小是 `slot = equity × POSITION_SIZE = 1,000,000 × 0.15 = 150,000`。實測結果：

| 股價 | 可買股數 | 捨去到整張後 |
|---|---|---|
| 100 | 1,497 | 1,000 ✅ |
| 140 | 1,069 | 1,000 ✅ |
| **150** | 998 | **0 ❌** |
| 300 | 499 | **0 ❌** |
| 1,315 | 113 | **0 ❌** |
| 7,150 | 20 | **0 ❌** |

**`eval_trader` 在 MR20 設定下，股價超過約 150 元的標的一律買不起，全數落入 `CANCELLED_INSUFFICIENT_CASH`（實際紀錄 17 筆）。** 而 MR20 的實際候選是 6446（1,315 元）、3008（7,150 元）這種高價股。

**結論：`artifacts/eval_mr20_60d.md` 的 7 筆成交，全都是股價 ≤ 約 150 元的低價股。−1.83% / Sharpe −0.5795 / 勝率 57.14% 量到的是「MR20 訊號的低價股子集」，不是 MR20。** 這個數字既不是高估也不是低估，而是**量錯對象**。同一個 bug 也污染了 baseline 的 +5.57%（65 筆資金不足撤單），所以兩者的比較也不成立。

### 2.2 09:00–09:30 模擬 → 低估成交率，並引入逆選擇偏誤

規格（`ai_report.py:237`、`paper_tracker.py:514`）：訊號日收盤價掛買進限價單，`time_in_force: DAY_UNTIL_0930`，次一交易日 09:30 撤單。

實作（`strategy/order_execution.py:60-90`，三套引擎共用）：

```python
def evaluate_buy_limit_at_open(limit_price, open_price):
    """Evaluate the opening-auction portion of a one-day buy limit order."""
    ...
    if open_price <= limit_price: return FILLED (fill at open)
    return CANCELLED_OPEN_ABOVE_LIMIT
```

函式的 docstring 誠實地寫了「the **opening-auction portion**」——它只模擬 09:00 集合競價那一個時點。但三個引擎都把它當成整段 09:00–09:30 的**唯一**判定。

1. **低估成交率**：真實的 `DAY_UNTIL_0930` 限價單在 09:00–09:30 這 30 分鐘內，只要盤中價格觸及限價就會成交。模擬把「開高 3 元後 09:15 回落到限價以下」的情況全部判為撤單。這正是撤單統計的主體：MR20 39/63、TOP7 9/28。
2. **逆選擇偏誤（更嚴重）**：模擬只留下「開盤沒有漲上去」的標的。成交樣本因此**系統性偏向開盤走弱者**。這使得已成交部位的報酬**低於**訊號母體的真實期望值。
3. **因此，−1.83%（MR20）與 −0.04%（TOP7）兩個數字都是偏誤子樣本的結果**，不能直接當作策略的成效評價——但同樣地，也不能拿來為策略辯護。唯一正確的結論是：**目前的成交樣本沒有代表性**。
4. 專案有 `data/intraday/*_5m.csv`（20 檔 5 分鐘 K），但只供 Meal Money 策略使用（`meal_money_sweep.py`），MR20/TOP7 完全沒接。修正 09:00–09:30 判定所需的資料其實部分已在本地。

### 2.3 盤中價 vs 收盤價 → 不對稱地高估

出場邏輯（`independent_sim.py:1078-1091`、`paper_tracker.py:494-501`、`eval_trader.py:233-241`，三套一致）：

```python
if low <= sl:   reason = "SL"; exit_price = open if open < sl else sl
elif high >= tp: reason = "TP"; exit_price = open if open > tp else tp
elif day_count >= max_hold: reason = "TIME"; exit_price = close
```

- **停損被高估（損失被低估）**：只要當日最低價觸及 SL，就假設**恰好成交在 SL 價**。真實停損在跳水行情會滑價成交在 SL 之下。程式的 `SLIPPAGE = 0.003` 只是對賣出**金額**加的成本項，不是價格路徑滑價，補不了缺口。只有「開盤即跳空低於 SL」這一種情況才用開盤價——但盤中跳水不算。
- **停利同樣假設恰好成交在 TP 價**，方向相反。但兩者的分布不對稱：上漲觸價通常有流動性接手，下跌觸價往往沒有。**淨效果偏向高估。**
- **同日 TP 與 SL 都觸及時，三套引擎都優先判 SL** —— 這一項是保守的（不是高估），值得肯定。但因為沒有盤中路徑資料，無法驗證真實的先後順序，這個保守假設也可能反過來低估某些交易。
- 進場日不檢查出場（`if pos["entry_date"] == as_of: continue`），所以進場當天的跳水不會觸發停損，只反映在權益上——這一點與回測一致，不算偏誤。
- 每日 mark-to-market 用**收盤價**，但進場價是**開盤價**。進場日的權益因此包含了「開盤到收盤」這一整段未被風控覆蓋的曝險。

### 2.4 day_count → 高估（延後認賠），且統計失真

`independent_sim.py:1064-1070`：

```python
if not bar or bar.get("close") is None:
    continue            # ← 沒有當日報價：不加 day_count，也不檢查出場
if bar.get("date") != as_of:
    continue
pos["day_count"] = pos.get("day_count", 0) + 1
```

`paper_tracker.py:485-487` 同樣邏輯。

1. **行情缺漏日部位「凍結」**：不老化、不檢查 TP/SL。資料源（yfinance）掛掉時，虧損部位可以無限期不出場，`max_hold_days = 20` 的時間止損形同失效 → **高估**（把該認的賠延後）。
2. **`day_count` 是「有報價的更新次數」，不是交易日天數**。`paper_tracker.py` 每天最多跑一次，且開頭有 `if data['equity_curve'][-1]['date'] == today: return` 的整段跳出——漏跑一天，**當天全部部位都不老化**。
3. **目前已經發生**：TOP7 自 9/03 停更 5 個交易日，3406 與 3008 的 `day_count` 停在 1，實際已持有 5 個交易日。這兩檔的時間止損現在落後現實 4 天。
4. **連帶污染統計**：`trades.csv` 的 `days_held` 直接取 `day_count`（`independent_sim.py:1116`），所以「平均持有 13.29 日」這類數字也失真。

### 2.5 缺價計價 → 三套引擎三種語意，兩種偏誤方向

| 引擎 | 程式碼 | 缺價時的行為 | 偏誤方向 |
|---|---|---|---|
| `independent_sim.py:1144` | `closes.get(tkr, pos["entry"]) * pos["shares"]` | 以**進場成本**計價 | 未實現損益歸零 → **高估**權益、**低估**波動與 MDD |
| `paper_tracker.py:800` | `price = prices.get(ticker, pos['entry'])` | 同上 | 同上 |
| `eval_trader.py:252-256` | `sum(... if date_str in index and t in columns)` | 該檔**從市值中消失** | 部位瞬間歸零 → **低估**權益 |

前兩者最危險：資料缺漏時權益曲線被人為拉平，虧損被隱藏，而且**外觀上完全看不出來**——曲線看起來只是「那天沒動」。第三者則會製造假的斷崖。

正確作法應是採用**最後已知有效收盤價（last valid close）**，並在 `equity.csv` 增加 `stale_marks` 欄位標記當日有幾檔是用舊價計的。三套語意不一致也意味著**三條線的數字彼此不可比較**。

### 2.6 權益曲線排序 → 目前尚未爆炸，但引信已經點著

`independent_sim.py:1226-1228`（`export_ledgers`）直接依 append 順序輸出，**沒有 `sort_values("date")`**：

```python
equity_df = pd.DataFrame(state.get("equity_curve", []), columns=equity_cols)
equity_df.to_csv(d / "equity.csv", index=False)
```

實測 `independent_sim_data_top7/equity.csv` 第 9 列：

```
2026-09-07 ...
2026-09-08 ...
2026-09-03 ...   ← 亂序
2026-09-09 ...
```

> **「equity.csv 未排序」前次發現：驗證成立。** MR20 那條線目前恰好有序，但用的是同一份程式碼，只是還沒補跑到。

連鎖後果（全部在 `mark_equity`, `independent_sim.py:1135-1188`）：

1. **`prev_equity = state["equity_curve"][-1]["equity"]`（line 1150）** 取的是「最後**被寫入**的」而非「日期上前一天」。補跑歷史日時，該日的 `daily_return` 會拿未來某天當基準 → **Sharpe、年化波動、excess_return 全錯**。
2. **`first_bm_close = state["equity_curve"][0]["benchmark_close"]`（line 1151）** 取「最先被寫入的」。若最早的日期是後來補跑的，基準報酬的基期就錯了 → **0050 比較失真**。
3. **同日去重（line 1173）發生在 `prev_equity` 取值之後**：
   ```python
   prev_equity = state["equity_curve"][-1]["equity"]     # line 1150
   ...
   state["equity_curve"] = [e for e in ... if e["date"] != as_of]   # line 1173
   ```
   同一天重跑時，`prev_equity` 取到的是**今天自己的舊值**，`daily_return` 變成「今天 vs 今天」≈ 0。**重跑不冪等，且每重跑一次就吃掉一天的真實報酬。**
4. **`compute_performance`（line 1253）用 `equity_df.iloc[-1]` 當期末**，`drawdown` 欄位又是 append 當下的 running peak。亂序時 MDD **可高可低，方向不定**——這是最難察覺的一種錯。

目前 TOP7 與 MR20 兩條曲線都是全平的（一條無部位、一條停更），所以還沒吃到虧。**一旦有實際部位再發生補跑，數字就會錯，而且不會有任何告警。**

### 2.7 額外發現（不在原清單，但同等重要）

**(a) 訊號檔可被事後覆寫 → 模擬不可重現**

`git diff artifacts/mr20/orders_mr20_20260907.json`：

```diff
-      "limit_price": 1315.0,        +      "limit_price": 1325.0,
-      "reference_close": 1315.0,    +      "reference_close": 1325.0,
-      "score": 66.4101,             +      "score": 63.2878,
-      "atr": 41.9986,               +      "atr": 42.8237,
-      "pullback": 5,                +      "pullback": 4,
```

模擬 `state.json` 記錄的是舊值（1315），磁碟上的訂單檔已被改成新值（1325）。行情供應商（yfinance）修正歷史資料後，重跑 `ai_report.py` 就會改寫已交付的訊號檔。**同一天的模擬跑兩次會得到不同結果，且沒有任何 hash 或封存機制可以發現。**

**(b) TOP7 的「基準線」是假的**

`paper_tracker.py:856`：

```python
benchmark_json = json.dumps([initial] * len(equity_curve))
```

`paper_trading.html` 圖表上那條「基準」是**一條初始資金的水平線**，不是 0050。任何看這張圖的人都會以為策略跑贏了基準（因為權益曲線大部分時間在 200,000 之上）。本文的 +3.01% 是手動從 `independent_sim` 的 `benchmark_close` 欄位算出來的，TOP7 自己的報告裡沒有這個數字。

**(c) `eval_trader` 的部位設定與線上規格不符**

| | `eval_trader.py:36-38` | MR20 線上規格 |
|---|---|---|
| 最大持倉 | 5 | **2**（`independent_sim.py:73-85`、`docs/EVAL_TRADER_PLAN_V2.md:129`） |
| 部位大小 | 15% | **45%** |
| 現金保留 | 20% | **10%** |
| 初始資金 | 1,000,000 | 1,000,000 ✅ |

即使沒有 §2.1 的整張 bug，`eval_mr20_60d.md` 量的也是另一組參數的策略。而部位大小又直接決定了整張約束的門檻（0.45 × 1,000,000 = 450,000 → 價格上限拉高到約 450 元），**兩個 bug 互相放大**。

### 2.8 可信度總結

| 面向 | 偏誤方向 | 嚴重度 |
|---|---|---|
| 整股取整（線上，允許零股） | **高估**可成交性；**低估**波動/MDD/成本 | 🔴 P0 |
| 整股取整（離線，隱形 150 元上限） | **量錯對象**，−1.83% 失效 | 🔴 P0 |
| 09:00–09:30 只判開盤價 | **低估**成交率 + 逆選擇偏誤 | 🔴 P0 |
| 盤中觸價假設恰好成交 | 不對稱**高估** | 🟠 P1 |
| day_count 缺價不老化 | **高估**（延後認賠） | 🟠 P1 |
| 缺價以成本計價 | **高估**權益、**低估** MDD | 🔴 P0 |
| 權益曲線未排序 | 方向不定，且無告警 | 🔴 P0 |
| 訊號檔可被覆寫 | 不可重現 | 🟠 P1 |
| 假基準線 | 判讀誤導 | 🟠 P1 |
| eval 設定不對齊 | 策略不可比 | 🔴 P0 |

**一句話**：目前這兩條虛擬交易線產出的任何績效數字，都還不到可以拿來做策略決策的程度。問題不在策略，在量尺。

---

## 3. 兩策略比較

| | TOP7 (v8.5) | MR20 |
|---|---|---|
| 線上模擬成交數 | 8 筆（2 平倉） | **0 筆** |
| 線上模擬報酬 | −0.04%（vs 0050 +3.01%） | 0.00%（vs 0050 +4.53%） |
| 離線回測 | 1200 日 + 4 段 WF OOS ✅ | 僅 60 日，且設定錯誤 + 樣本污染 ❌ |
| 回測年化 | +29.52%（Sharpe 0.943），但輸 0050 24pp | 無有效數字 |
| 訊號產出 | 每日有訊號（10 天 10 筆 daily_signals） | 17 天只有 4 單，13 天零訊號 |
| 管線狀態 | **停更 5 個交易日** | **4 張單漏了 3 張** |
| 有無獨立回測報告 | 有（`BACKTEST_REPORT_20260815.md`） | **無** |

### 判斷

**繼續跑：TOP7。** 理由不是它現在的績效好（−0.04% 輸大盤 3pp），而是**它是唯一有樣本外證據的策略**：1200 日回測、4 段非重疊 WF OOS、OOS 衰減比 1.10。前提是要先修好停更問題與假基準線，否則「繼續跑」等於什麼都沒跑。

**先修：MR20。** 這是關鍵判斷——**MR20 目前並沒有被證明失敗，它是根本沒被公平量過。** −1.83% 來自一個只能買 150 元以下股票、部位設定又跟線上不同的引擎；線上那條線一筆都沒成交；四張訂單漏了三張。在這種狀態下把 MR20 判死刑，等於把量尺的錯誤算到策略頭上。它需要的是一次公平的評估，不是判決。

**停掉：`independent_sim_data_top7` 這條線（`top2_score_v1` / 20,000 元 / 2 檔）。** 它既不是 TOP7 也不是 MR20，8 筆委託全數撤單、0 成交、20,000 元的資金規模在整張約束下連一張都買不起。它唯一的作用是讓人誤以為 TOP7 有第二套獨立驗證。目錄名與 `strategy_id` 不符本身就是判讀陷阱。**停掉它，或改名為 `independent_sim_data_top2` 並明確標示為第三個策略。**

---

## 4. 評分與 Verdict

### TOP7 (v8.5) — **5 / 10** — **先修再跑**

| 面向 | 分數 | 說明 |
|---|---|---|
| 樣本外證據 | 7/10 | 1200 日 + 4 段 WF OOS，衰減比 1.10，全專案最扎實 |
| 實際績效 | 3/10 | 回測年化輸 0050 24pp；paper 10 天 −0.04% 輸 3.05pp；2/4 段 OOS 為負 |
| 執行模型可信度 | 4/10 | 零股不可成交、缺價以成本計價、假基準線 |
| 管線健康度 | 3/10 | 已停更 5 個交易日，day_count 與現實脫節 |
| 統計功效 | 2/10 | paper 樣本 10 天 / 2 筆平倉，什麼都證明不了 |

**Verdict：先修再跑。** 具體是修 P0 執行模型（整張、缺價計價、真 0050 基準）+ 恢復每日更新，然後至少累積 30 個交易日再談績效。**不建議停掉**——它是唯一有 OOS 背書的策略。但也**不建議在修好之前拿現在的數字做任何決策**。

### MR20 — **3 / 10** — **先修再跑**

| 面向 | 分數 | 說明 |
|---|---|---|
| 樣本外證據 | 1/10 | 無獨立回測報告；唯一的 60 日 eval 設定錯誤且樣本被污染 |
| 實際績效 | 2/10 | 線上 0 成交（不是虧損，是沒有資料）；−1.83% 不成立 |
| 執行模型可信度 | 2/10 | 隱形 150 元價格上限、部位設定不對齊線上 |
| 管線健康度 | 2/10 | 4 張訂單漏 3 張；17 天 13 天零訊號 |
| 統計功效 | 1/10 | 7 筆交易、t ≈ −0.28，與 0 無異 |

**Verdict：先修再跑。** 分數低反映的是**證據量為零**，不是「已證實表現差」。修完批次 A + B1 之後重跑一次 180 日 eval，才第一次會有 MR20 的真實數字。若那時仍為負 Sharpe 且訊號漏斗仍過窄，**再考慮停掉**。

### （附）`top2_score_v1` 那條線 — **1 / 10** — **停掉**

0 成交、20,000 元資金在任何整張約束下都買不到 1 張、目錄名與 strategy_id 不符造成判讀污染。無保留價值。

---

# 第二部分 — Implementation Plan

## 排序原則

```
批次 A：成交樣本可信   ← 先把量尺修對（整股、執行模型、權益計算）
   ↓  ★ 做完可重跑虛擬交易，第一次得到可信的成交樣本
批次 B：策略可比       ← 再讓兩個策略在同一把尺下被公平評估
   ↓  ★ 做完可重跑完整評估，第一次得到可比的績效數字
批次 C：管線 / 告警     ← 最後才是不讓它再壞掉
```

**原則**：在批次 A 完成之前，**不要根據任何現有績效數字做策略去留決策**。

---

## 批次 A — 成交樣本可信（5 項，全 P0）

> **★ 這一批做完即可重跑虛擬交易驗證。** 完成後執行 §「重跑驗證程序 A」。

### A1｜統一整股單位與最低手續費（P0）

**問題**
線上引擎允許買 1 股（零股）→ 高估可成交性、低估波動與成本（3008 買 1 股，實投僅目標的 38%）。離線引擎捨去到整張但部位只有 15% → 股價 > 約 150 元一律買不起，`eval_mr20_60d.md` 的 7 筆全是低價股。

**檔案位置**
- `independent_sim.py:967`（`shares = math.floor(...)`）
- `paper_tracker.py:680`（`shares = int(...)`）
- `eval_trader.py:27-28`（`MIN_COMMISSION`、`LOT_SIZE`）、`eval_trader.py:190`
- 建議新增共用模組 `strategy/sizing.py`

**修法方向**
1. 抽出單一 `size_position(equity, position_size, fill_price, cash, reserve, lot_size, min_commission) -> (shares, cost)`，三套引擎共用。
2. `lot_size` 設為可配置：預設 `1000`（整股）；若要保留零股情境，明確標記 `odd_lot=True` 並在成交價套用零股折價（建議 +0.5% 買價懲罰），**不得**以整股開盤價成交。
3. 手續費改為 `max(notional × 0.001425, 20.0)`，買賣兩側都套用。
4. **當 `shares == 0` 時，區分兩種撤單原因**：`CANCELLED_BELOW_LOT_SIZE`（買不起一張）與 `CANCELLED_INSUFFICIENT_CASH`（現金不足），否則永遠診斷不出 §2.1 這種 bug。

**驗證門檻**
- `pytest tests/test_independent_sim.py tests/test_eval_trader.py tests/test_paper_tracker_turnover.py -q` 全綠。
- 新增測試：`size_position(equity=1_000_000, position_size=0.15, fill_price=1315)` → `shares == 0` 且 reason 為 `CANCELLED_BELOW_LOT_SIZE`。
- 新增測試：`size_position(..., fill_price=100)` → `shares == 1000`（非 1497）。
- 新增測試：`notional = 5350` → 手續費 `== 20.0`（非 7.62）。
- **回測數字門檻**：重跑 `eval_trader.py --strategy mr20 --days 60`，`CANCELLED_BELOW_LOT_SIZE` 應出現於報告，且成交標的的**價格中位數必須高於 150**（證明高價股不再被系統性排除）。

**預估 turns**：4–6

---

### A2｜09:00–09:30 限價單補上盤中觸價判定（P0）

**問題**
只判開盤價一個時點 → 低估成交率（MR20 39/63 開高撤單），並引入逆選擇偏誤（只買到開盤走弱的標的），使成交樣本沒有代表性。

**檔案位置**
- `strategy/order_execution.py:60-90`（`evaluate_buy_limit_at_open`）
- 呼叫端：`independent_sim.py:945`、`paper_tracker.py:592`、`eval_trader.py:161`
- 資料：`data/intraday/*_5m.csv`（已有 20 檔，`fetch_intraday.py` 可擴充）

**修法方向**
1. **保留 `evaluate_buy_limit_at_open` 不動**（它的語意是正確的，只是不完整），新增 `evaluate_buy_limit_until_0930(limit, open_px, intraday_bars | day_low)`。
2. 優先用 5 分鐘 K：09:00–09:30 區間內若 `min(low) <= limit` 則 FILLED，成交價取 `min(open_px, limit)`。
3. 無盤中資料時退回**保守日 K 近似**：`open > limit` 但 `day_low <= limit` → 標記為 `FILLED_INTRADAY_ESTIMATED`，成交價取 `limit`，並在 `orders.csv` 標記估計旗標。**估計成交必須可從報表中剔除**，以便同時產出「僅開盤成交」與「含盤中成交」兩組數字。
4. 擴充 `fetch_intraday.py` 的 ticker 清單，涵蓋 MR20/TOP7 的候選池。

**驗證門檻**
- `pytest tests/test_order_execution.py -q` 全綠，且原有 `evaluate_buy_limit_at_open` 測試**一個都不改**（回歸保護）。
- 新增測試：`open=1345, limit=1315, low=1300` → FILLED @1315；`low=1320` → CANCELLED。
- **回測數字門檻**：重跑 60 日 MR20 eval，成交率須從 `7/63 = 11.1%` 顯著上升；報告中須同時列出 open-only 與 intraday 兩組成交率與報酬，兩者差距即為逆選擇偏誤的量化估計。

**預估 turns**：6–8

---

### A3｜缺價計價統一為 last-known + stale 標記（P0）

**問題**
三套引擎三種語意：兩套以進場成本計價（隱藏未實現虧損、壓平權益曲線）、一套讓部位從市值消失（假斷崖）。

**檔案位置**
- `independent_sim.py:1143-1147`（`mark_equity`）
- `paper_tracker.py:800`
- `eval_trader.py:252-256`（`_daily_mtm`）

**修法方向**
1. 部位新增 `last_valid_close` 與 `last_valid_date` 欄位，每次有效行情時更新。
2. 缺價時以 `last_valid_close` 計價（首日缺價才退回 `entry`）。
3. `equity.csv` 新增 `stale_marks`（當日以舊價計價的檔數）與 `stale_ratio`（其市值佔比）欄位。
4. `stale_ratio > 0.3` 時在 `status` 輸出與報告中警示，該日的 `daily_return` 標記為不可信。

**驗證門檻**
- 新增測試：部位連續 3 日缺價，權益必須反映 `last_valid_close` 而非 `entry`，且 `stale_marks == 1`。
- 新增測試：缺價部位**不得**從 `eval_trader` 的市值中消失。
- **回測數字門檻**：重跑後 `equity.csv` 的 `stale_marks` 欄位存在且全期可稽核；若任一日 `stale_ratio > 0.3`，該日必須在報告中被列出。

**預估 turns**：3–4

---

### A4｜權益曲線排序與冪等重跑（P0）

**問題**
`export_ledgers` 未排序（實測 top7 的 `equity.csv` 有 9/03 夾在 9/08 與 9/09 之間）；`prev_equity` 取「最後寫入」而非「日期前一日」；同日去重發生在 `prev_equity` 取值之後導致重跑不冪等；`compute_performance` 用 `iloc[-1]` 當期末。

**檔案位置**
- `independent_sim.py:1150-1151`（`prev_equity`、`first_bm_close`）
- `independent_sim.py:1173`（同日去重時序）
- `independent_sim.py:1226-1228`（`export_ledgers` 未排序）
- `independent_sim.py:1253`（`compute_performance` 的 `iloc[-1]`）

**修法方向**
1. `mark_equity` 開頭先**依日期去重**，再**依日期取前一交易日**的 equity 當 `prev_equity`（不是 `[-1]`）。
2. `first_bm_close` 改為「日期最小的那筆」的 `benchmark_close`。
3. `mark_equity` 結尾對 `state["equity_curve"]` 做 `sort(key=lambda e: e["date"])`。
4. `export_ledgers` 加 `equity_df.sort_values("date")`；`orders.csv` 依 `(execution_date, event_time)` 排序、`trades.csv` 依 `exit_date` 排序。
5. `compute_performance` 一律先排序再取 `iloc[-1]`；`drawdown` 改為**由排序後的序列重算**，不用 append 當下寫入的值。

**驗證門檻**
- 新增測試（冪等）：同一天連續 `mark_equity` 三次，`equity_curve` 長度不變且 `daily_return` 三次相同。
- 新增測試（亂序補跑）：依 `[D1, D3, D2]` 順序寫入，輸出的 `equity.csv` 必須是 `[D1, D2, D3]`，且 `D2.daily_return` 以 `D1` 為基準、`D3.daily_return` 以 `D2` 為基準。
- 新增測試：亂序寫入後 `compute_performance` 的 `total_return` 與有序寫入**完全相等**。
- **回測數字門檻**：`independent_sim_data_top7/equity.csv` 重新匯出後日期嚴格遞增。

**預估 turns**：4–5

---

### A5｜day_count 改用交易日曆（P1，但屬同批）

**問題**
缺價日不老化、不檢查出場 → `max_hold_days` 可被無限期延後（TOP7 目前 3406/3008 的 `day_count=1` 但實際已持有 5 個交易日）。`days_held` 統計連帶失真。

**檔案位置**
- `independent_sim.py:1063-1070`
- `paper_tracker.py:485-487`
- `paper_tracker.py:466-469`（`equity_curve[-1].date == today` 的整段跳出）

**修法方向**
1. `day_count` 改為由 `entry_date` 與 `as_of` 用 `exchange_calendars.XTAI` 計算的**交易日差**，不再累加。
2. 缺價日仍不檢查 TP/SL（無資料無從判斷），但 `day_count` 照常前進；`day_count >= max_hold` 且缺價時，標記 `PENDING_TIME_EXIT` 並在下一個有效行情日以開盤價出場。
3. `paper_tracker` 的「今日已更新」跳出改為只跳過**權益寫入**，部位老化與 TP/SL 檢查照跑（或改為冪等重算）。

**驗證門檻**
- 新增測試：進場後連續 25 個交易日缺價，第 26 日有價 → 必須觸發 `TIME` 出場，且 `days_held == 20`（不是 1）。
- 新增測試：`day_count` 對「漏跑一天」免疫（跑 D1、跳過 D2、跑 D3，`day_count` 應為 2）。
- **回測數字門檻**：重跑後 `trades.csv` 的 `days_held` 必須全部 `<= max_hold_days`，無例外。

**預估 turns**：3–4

**批次 A 合計預估：20–27 turns**

---

## 批次 B — 策略可比（5 項）

> **★ 這一批做完即可重跑完整策略評估。** 完成後執行 §「重跑驗證程序 B」。批次 A 未完成前不要開始 B1。

### B1｜eval_trader 設定改由策略註冊表驅動（P0）

**問題**
`eval_trader.py` 硬編碼 5 檔 / 15% / 保留 20%，與 MR20 線上規格（2 檔 / 45% / 保留 10%）不符。加上 A1 的整張問題，`eval_mr20_60d.md` 量的是「另一組參數 × 低價股子集」。

**檔案位置**
- `eval_trader.py:33-38`（`TP_ATR`, `SL_ATR`, `MAX_HOLD`, `INIT_CAPITAL`, `MAX_POSITIONS`, `POSITION_SIZE`, `RESERVE_RATIO`）
- 註冊表：`independent_sim.py:58-102`（`STRATEGY_CONFIGS`、`get_strategy_config`）
- 規格對照：`docs/EVAL_TRADER_PLAN_V2.md:129`

**修法方向**
1. `eval_trader` 改為 `from independent_sim import get_strategy_config`（或把 `STRATEGY_CONFIGS` 抽到 `strategy/registry.py` 讓三方共用）。
2. 全部模組級常數改為由 `strategy_id` 解析出的 config 驅動；模組級常數只留作 fallback 並加註 deprecation。
3. **新增啟動時的設定斷言**：`eval_trader` 的有效參數與 `independent_sim.get_strategy_config(sid)` 逐欄比對，不符即 fail-fast 並印出差異表。
4. 報告開頭列出本次實際使用的完整參數（含 `lot_size`、`min_commission`、成交模型版本）。

**驗證門檻**
- 新增測試（`tests/test_eval_trader.py`）：`--strategy mr20` 時，`MAX_POSITIONS == 2 and POSITION_SIZE == 0.45 and RESERVE_RATIO == 0.10`。
- 新增測試：刻意讓兩處設定不一致 → 必須 raise，不得靜默通過。
- **回測數字門檻**：重跑 `eval_trader.py --strategy mr20 --days 60 --out artifacts/eval_mr20_60d_v2`，報告首段必須列出 `max_positions=2, position_size=0.45, reserve=0.10, lot_size=1000, min_commission=20`；`CANCELLED_INSUFFICIENT_CASH` 應從 17 筆顯著下降（部位放大到 450,000，價格上限從 150 拉高到約 450）。

**預估 turns**：3–4

---

### B2｜樣本量護欄與信賴區間（P1）

**問題**
把 60 日 / 7 筆交易年化成 −7.45% / Sharpe −0.5795 並拿來與 baseline 比較。實際 t ≈ −0.28（baseline +0.69），兩者都與 0 無異。`independent_sim` 有「< 30 日不報 Sharpe」的護欄，`eval_trader` 沒有。

**檔案位置**
- `strategy/risk_metrics.py:17-57`（`compute_risk_metrics`）
- `eval_trader.py:379`（呼叫端）
- `independent_sim.py:1265`（既有護欄，作為對齊標準）

**修法方向**
1. `compute_risk_metrics` 回傳值新增 `n_days`、`n_trades`、`sharpe_tstat`、`sharpe_ci95`（用 `SR ± 1.96 × √((1 + SR²/2) / n)`）。
2. 統一護欄：`n_days < 30` 或 `n_trades < 20` 時，`sharpe` / `ann_return` / `calmar` 一律回傳 `None`，報告顯示「樣本不足（n_days=X, n_trades=Y）」。
3. 報告中所有 Sharpe 一律附 t 值與 95% CI；`|t| < 2` 時明確標註「與 0 無顯著差異」。
4. 年化報酬在 `n_days < 120` 時不得單獨顯示，必須併列原始總報酬。

**驗證門檻**
- 新增測試：`n_trades=7` 的輸入 → `sharpe is None`。
- 新增測試：`SR=1.4212, n_days=60` → `sharpe_tstat` 落在 `0.69 ± 0.02`。
- **回測數字門檻**：重跑後 `eval_mr20_60d_v2.md` 與 baseline 報告都必須顯示 t 值；任何 `|t| < 2` 的 Sharpe 都帶上「不顯著」標註。

**預估 turns**：3–4

---

### B3｜MR20 walk-forward + Monte Carlo（P1）

**問題**
MR20 完全沒有獨立回測報告，也沒有樣本外驗證。TOP7 有 1200 日 + 4 段 WF OOS，兩者證據強度完全不對等，任何比較都不公平。

**檔案位置**
- 既有工具：`walk_forward.py`、`walk_forward_nested.py`、`monte_carlo.py`
- 訊號函式：`strategy/mr20_strategy.py`（`filter_mr20_candidates`，8-gate 漏斗）
- 撮合：批次 A 修好的共用引擎
- 輸出：`docs/BACKTEST_REPORT_MR20_<date>.md`（新增）

**修法方向**
1. 用批次 A 的引擎跑 MR20 至少 1200 日回測，產出與 `BACKTEST_REPORT_20260815.md` **完全相同格式**的報告（年化、Sharpe、MDD、交易數、勝率、PF、平均持有 + 0050 對照）。
2. 4 段非重疊 WF OOS，段界與 v8.5 報告對齊（2022-07 / 2023-07 / 2024-07 / 2025-08 / 2026-08），以便逐段對照。
3. Monte Carlo（`monte_carlo.py`）：交易順序重排 1000 次，輸出 MDD 分布的 P50 / P95，以及「總報酬 > 0」的機率。
4. 同時輸出**訊號漏斗統計**：1200 日內平均每日訊號數、零訊號日佔比。若零訊號日 > 60%，訊號稀缺就是 MR20 的主要瓶頸，而非進出場邏輯。

**驗證門檻**
- `docs/BACKTEST_REPORT_MR20_<date>.md` 產出，欄位與 v8.5 報告一一對應。
- **回測數字門檻（去留判準）**：
  - 交易次數 ≥ 100（否則統計無意義，直接判定為「訊號稀缺」問題）
  - 4 段 OOS 中正 Sharpe ≥ 2 段
  - OOS 衰減比 ≤ 1.5
  - Monte Carlo P95 MDD ≤ 35%
  - **全數通過 → 繼續跑；交易次數 < 100 → 先解訊號漏斗；其餘不通過 → 停掉 MR20。**

**預估 turns**：6–9

---

### B4｜訊號檔不可變與 hash 封存（P1）

**問題**
`artifacts/mr20/orders_mr20_20260907.json` 被事後覆寫（limit 1315→1325、score 66.41→63.29、atr 41.9986→42.8237、funnel.pullback 5→4），模擬 state 記的是舊值。同一天跑兩次結果不同，且無從察覺。

**檔案位置**
- `ai_report.py:237-260`（訂單產生）
- `run_mr20.sh:155`（`close-and-plan` 呼叫）
- `independent_sim.py:1611-1710`（訂單自動搜尋與 `planning_gaps`）

**修法方向**
1. 訂單檔寫入時計算內容 sha256，寫入檔內 `content_hash` 欄位，並附加到 `artifacts/mr20/MANIFEST.jsonl`（append-only）。
2. **同一 `signal_date` 的訂單檔若已存在且 hash 不同，預設拒絕覆寫**，改寫入 `orders_mr20_<date>.v2.json` 並在 manifest 記錄 supersede 關係。
3. `independent_sim` 消費訂單時把 `content_hash` 記入 `order_events`，讓 state 與磁碟檔可對帳。
4. 新增 `check_state.py` 子命令或獨立腳本，比對 manifest 與磁碟現況，列出所有被覆寫的訊號日。

**驗證門檻**
- 新增測試：對同一 `signal_date` 寫入不同內容 → 原檔不變，產生 `.v2.json`，manifest 有兩筆。
- 新增測試：`order_events` 中的 `content_hash` 與磁碟檔 hash 相符。
- **驗證門檻**：對現有 17 個 `artifacts/mr20/orders_mr20_*.json` 補建 manifest，並產出已知被覆寫檔案的清單（至少須包含 `20260907`）。

**預估 turns**：3–4

---

### B5｜TOP7 基準改為真實 0050（P1）

**問題**
`paper_tracker.py:856` 的基準是 `[initial] * len(equity_curve)` —— 一條初始資金水平線。`paper_trading.html` 上看起來策略贏過基準，實際上輸給 0050 三個百分點。

**檔案位置**
- `paper_tracker.py:856`（`benchmark_json`）
- `paper_tracker.py:1089`（圖表 data 綁定）
- 參考實作：`independent_sim.py:1156-1163`（`mark_equity` 的 `bm_equity` 計算）

**修法方向**
1. `paper_equity.json` 的 `equity_curve` 每筆新增 `benchmark_close` / `benchmark_equity` / `benchmark_return` / `excess_return`，與 `independent_sim` 的 schema 對齊。
2. 0050 收盤價取得方式與 `independent_sim` 共用（`DEFAULT_BENCHMARK_TICKER = "0050.TW"`），避免第二套抓價邏輯。
3. 歷史資料（2026-08-20 起）以 0050 日線回填。
4. HTML 圖表改繪真實 0050 曲線，並在標題列出超額報酬。

**驗證門檻**
- 新增測試：`equity_curve` 每筆都有非 None 的 `benchmark_close`。
- **回測數字門檻**：回填後 2026-09-03 那筆的 `benchmark_return` 必須等於 **+3.01%**（103.10 → 106.20），`excess_return` 為 **−3.05%**，與本文 §1.1 手算值一致。

**預估 turns**：3–4

**批次 B 合計預估：18–25 turns**

---

## 批次 C — 管線與告警（5 項，全 P1）

> 這一批不影響數字正確性，只影響「壞掉時會不會被發現」。可與 B 並行。

### C1｜artifacts → sim 漏單偵測（P1）

**問題**
`artifacts/mr20/` 有 4 張非空訂單（9/02、9/03、9/07、9/08），`state.json` 的 `order_events` 只有 9/07 一張，**3 張從未進入模擬**且無任何告警。

**檔案位置**
- `independent_sim.py:1611-1710`（訂單搜尋與 `planning_gaps`）
- `run_mr20.sh:79-85, 155-162`
- `check_processed_runs.py`

**修法方向**
每次 `close-and-plan` 之後，掃描 `artifacts/<strategy>/` 全部訂單檔，逐一比對 `signal_date` 是否出現在 `state["order_events"]`。缺漏者寫入 `state["unconsumed_orders"]`，並在 `status` 輸出與 Telegram 通知中列出。**非零訂單未被消費須以非零退出碼結束**，讓排程失敗可見。

**驗證門檻**
- 新增測試：artifacts 有 3 個 signal_date、state 只有 1 個 → `unconsumed_orders` 長度為 2 且退出碼非 0。
- **驗證門檻**：對現況執行，必須正確列出 9/02、9/03、9/08 三筆。

**預估 turns**：3–4

---

### C2｜paper_tracker 停更告警（P1）

**問題**
TOP7 自 2026-09-03 起停更 5 個交易日，無任何告警。`paper_trading.html` 仍然顯示著 9/03 的舊數字，看起來一切正常。

**檔案位置**
- `.github/workflows/update_ai_report.yml:110`（`run: python paper_tracker.py`）
- `paper_tracker.py:466-469`（今日已更新的整段跳出）
- `check_status.py`

**修法方向**
1. workflow 該步驟移除任何 `continue-on-error` 語意，失敗即 job 失敗並通知。
2. `paper_tracker` 啟動時檢查 `equity_curve[-1].date` 與今日的**交易日**差距，> 1 個交易日即在 stdout 以 `::warning::` 輸出並發 Telegram。
3. 新增 `--backfill <YYYY-MM-DD>` 讓漏跑的交易日可以補算（依賴 A4 的冪等與排序修復）。

**驗證門檻**
- 新增測試：`equity_curve` 最後一筆為 5 個交易日前 → 觸發告警路徑。
- **驗證門檻**：`python paper_tracker.py --backfill 2026-09-04` 起補到 09-09，補完後 `equity.csv` 日期嚴格遞增、`day_count` 與實際交易日一致（依賴 A4、A5）。

**預估 turns**：3–4

---

### C3｜停用 / 重建 `independent_sim_data_top7`（P1）

**問題**
目錄名為 top7，`strategy_id` 卻是 `top2_score_v1`（2 檔 / 45% / 初始 20,000）。0 成交、8 筆委託全撤。造成「TOP7 有第二套獨立驗證」的誤判。

**檔案位置**
- `independent_sim_data_top7/`（整個目錄）
- `independent_sim.py:59-71`（`top2_score_v1` 的 `default_data_dir`）

**修法方向**
擇一（建議 1）：
1. **改名為 `independent_sim_data_top2`**，在 `STRATEGY_CONFIGS` 明確標示為第三個獨立策略，並在報告標題加上策略全名。20,000 的初始資金在整張約束下（A1）無法成交任何標的，**必須同步調高至可交易水位或明確標記為「demo 用途、不納入評估」**。
2. 直接停用該條線並移除目錄。

無論哪一種，都要在 `independent_sim.py` 加上**目錄名與 `strategy_id` 一致性檢查**，不符即 fail-fast，杜絕同類問題再發生。

**驗證門檻**
- 新增測試：`data_dir` 名稱與 `strategy_id` 不符 → raise。
- **驗證門檻**：`grep -r "independent_sim_data_top7" --include=*.py --include=*.sh --include=*.yml .` 無殘留引用。

**預估 turns**：2–3

---

### C4｜三套撮合引擎收斂為一（P1）

**問題**
`independent_sim.py` / `paper_tracker.py` / `eval_trader.py` 各有一套撮合、sizing、計價、出場邏輯。批次 A 的每一項修復都必須改三個地方，且三者數字彼此不可比較。

**檔案位置**
- 新增 `strategy/matching_engine.py`
- 既有共用起點：`strategy/order_execution.py`
- 呼叫端三處：`independent_sim.py:900-1130`、`paper_tracker.py:513-760`、`eval_trader.py:149-258`

**修法方向**
把 sizing（A1）、限價成交判定（A2）、缺價計價（A3）、出場優先序（SL > TP > TIME）、成本模型抽成單一模組，三個呼叫端只保留各自的 I/O 與排程差異。**這一項排在 A 之後刻意為之**：先在三處各自修對並用測試釘住行為，再合併，比先合併再修安全。

**驗證門檻**
- **黃金測試**：同一組訂單與行情輸入，三套引擎的 `trades.csv` 必須 byte-identical。
- 合併前後，`pytest tests/ -q` 全數通過且**無任何既有測試被修改**。

**預估 turns**：8–12

---

### C5｜每日對帳報表（P1）

**問題**
目前沒有任何一個地方能一眼看出「今天訊號幾筆、下單幾筆、成交幾筆、撤單原因分布、權益多少、是否有 stale 計價」。所有本文的發現都是靠翻 JSON 才找到的。

**檔案位置**
- 新增 `daily_reconcile.py`
- 資料源：`artifacts/*/orders_*.json`、`*/state.json`、`paper_equity.json`

**修法方向**
每日產出 `artifacts/reconcile_<date>.md`，涵蓋：訊號漏斗各關卡計數 → 下單數 → 成交/撤單（分原因）→ 持倉與 `day_count` → 權益與 0050 對照 → stale 計價檔數 → `unconsumed_orders`（C1）→ 停更檢查（C2）。任一異常項以 🔴 標記並發 Telegram。

**驗證門檻**
- 對 2026-09-08 的歷史資料執行，報表須正確標出：MR20 訂單 3037 未被消費、TOP7 已停更 3 個交易日。
- **驗證門檻**：連續 5 個交易日的報表全數產出，且異常項與人工核對結果一致。

**預估 turns**：4–5

**批次 C 合計預估：20–28 turns**

---

## 重跑虛擬交易驗證的時機

### ★ 重跑驗證程序 A（批次 A 完成後）

**目的**：第一次得到可信的成交樣本。

```bash
pytest tests/ -q                                    # 全綠為前提

# 1. 重建 MR20 線上模擬（保留舊 state 備份）
cp -r independent_sim_data_mr20 independent_sim_data_mr20.bak
python independent_sim.py init -s mr20 --force
# 依 artifacts/mr20/ 的 17 個訂單檔逐日重放 8/18 → 9/09

# 2. 重建 TOP7 paper（補跑 9/04 → 9/09）
python paper_tracker.py --backfill 2026-09-04

# 3. 重跑 60 日 eval（設定尚未對齊，僅看執行模型差異）
python eval_trader.py --strategy mr20 --days 60 --out artifacts/eval_mr20_60d_A
```

**驗收標準**
- 三條線的 `equity.csv` 日期**嚴格遞增**。
- `trades.csv` 的 `days_held` **全部 ≤ 20**。
- 所有成交的股數**皆為 1,000 的倍數**（或明確標記 `odd_lot`）。
- 每筆成交的手續費 **≥ 20**。
- 撤單原因中出現 `CANCELLED_BELOW_LOT_SIZE`（證明 A1 生效）。
- MR20 成交率相對 `11.1%` 有可解釋的變化，且報告同時列出 open-only / intraday 兩組。
- `stale_marks` 欄位存在且可稽核。

**此時可以說的話**：「這是可信的成交樣本。」
**此時還不能說的話**：「MR20 比 TOP7 好 / 差。」——設定尚未對齊（B1），統計功效也還不足。

---

### ★ 重跑驗證程序 B（批次 B 完成後）

**目的**：第一次得到可比的績效數字，並據以做去留決策。

```bash
python eval_trader.py --strategy mr20     --days 180 --out artifacts/eval_mr20_180d
python eval_trader.py --strategy baseline --days 180 --out artifacts/eval_baseline_180d
python walk_forward.py --strategy mr20 --segments 4
python monte_carlo.py  --strategy mr20 --n 1000
```

**驗收標準**
- 兩份報告首段列出的參數與 `get_strategy_config` 完全一致。
- 所有 Sharpe 附 t 值與 95% CI；`|t| < 2` 者標註「不顯著」。
- MR20 交易次數 ≥ 100；否則結論只能是「訊號稀缺」，不能是「策略無效」。
- `docs/BACKTEST_REPORT_MR20_<date>.md` 與 v8.5 報告欄位一一對應。

**去留決策（依 B3 門檻）**
- MR20 **4 段 OOS 正 Sharpe ≥ 2、衰減比 ≤ 1.5、MC P95 MDD ≤ 35%、交易數 ≥ 100** → 繼續跑。
- 交易數 < 100 → 問題在訊號漏斗，先調 `filter_mr20_candidates` 的 gate，不動進出場。
- 其餘 → **停掉 MR20**，資源集中到 TOP7。

TOP7 則在 A + B5 完成後恢復每日更新，**累積至少 30 個交易日**再做第二次評估（届時 Sharpe 才第一次可報）。

---

## 總覽

| 批次 | 項目數 | P0 / P1 | 預估 turns | 完成後可做的事 |
|---|---|---|---|---|
| **A — 成交樣本可信** | 5 | 4 P0 / 1 P1 | 20–27 | **★ 重跑虛擬交易，得到可信成交樣本** |
| **B — 策略可比** | 5 | 2 P0 / 3 P1 | 18–25 | **★ 重跑完整評估，做 MR20 去留決策** |
| **C — 管線 / 告警** | 5 | 5 P1 | 20–28 | 壞掉時會被發現 |
| 合計 | 15 | | **58–80** | |

**單一最重要的原則**：批次 A 完成之前，`−1.83%`、`−0.04%`、`Sharpe −0.5795` 這些數字**一個都不要拿來做策略決策**。它們量到的不是策略，是量尺的誤差。

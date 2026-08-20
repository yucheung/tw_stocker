# tw_stocker v8.5 Claude Opus 程式碼審查報告
> 日期: 2026-08-14 | 模型: Claude Opus | 審查者: Hermes 派工

我已完成對整個 repo 的審查（核心引擎逐行讀過，並比對了 `artifacts/` 中的實際產出）。以下是報告。

---

# tw_stocker v8.5 程式碼審查 + 策略分析報告

## 1. 執行摘要

**核心引擎（`event_backtest.py`）的時序邏輯寫得相當嚴謹**——訊號一律取 `i-1`、成交取 `open[i]`、可交易性檢查、跳空穿價成交、停損優先，這些細節多數業餘量化專案都做錯，這裡都做對了；驗證基礎建設（nested walk-forward、DSR、PBO、block-bootstrap MC、experiment registry）也遠超一般水準。**但這套嚴謹的引擎跑在一個有致命結構缺陷的資料基礎上**：`EXTENDED_TICKERS` 是一份「今天手選的 140 檔名單」，所有回測、walk-forward、PBO、DSR 全部繼承了這份前視選股偏差，因此 Sharpe 2.365 / 年化 +76.8% 這組數字不具統計意義。更急迫的是，**production pipeline 目前處於靜默故障狀態**：2026-08-07 的實際產出只有 1 檔股票（2497）、34 筆交易、Sharpe 0.03，而這份報表已被 commit 並發佈，README 仍掛著 Sharpe 2.365。此外 **paper trading 的部位控管與回測完全不同**（回測每檔 10% 權益 × regime 縮放；paper 是把 90% 現金平分給當日候選、且完全沒有 regime 縮放），因此 paper trading 目前並不是在驗證 v8.5。**結論：不建議用於實盤**，需先修復資料閘門、股池偏差、paper/backtest 對齊三件事。

---

## 2. 程式正確性評估

### 2.1 動態 Universe 選股邏輯 — ⚠️ 演算法正確，但輸入有致命偏差

`strategy/ai_strategy.py:137-172` 的實作本身沒問題：`turnover.rolling(20).mean()` → 每日 rank → Top-60，並用 `close_df.notna() & (close_df > 0)` 排除無效格，無 look-ahead。

問題在上游。`ai_report.py:71-97` 的 `EXTENDED_TICKERS` 是硬編碼的 ~140 檔清單，選的是**今天**已知的權值股、AI 股、航運股。所謂「動態 Universe Top-60」是**在一份前視名單裡再取 Top-60**：

- **存活者偏差**：2023 年以來下市、重整、長期跌破流動性門檻的股票從未進入池中。
- **選擇偏差**（更嚴重）：`3711`(日月光投控)、`6770`(力積電)、`2603`(長榮)、`1519`(華城) 這類名單，本身就是「2023-2026 表現亮眼」的事後產物。動量策略在一個事後篩選出的贏家池裡跑，報酬被系統性灌水。
- **傳染性**：`walk_forward_nested.py` 呼叫 `ai_report.py --start-date/--end-date`，股池不變，所以 **OOS fold 並非真正的 out-of-sample**；PBO、DSR 也都建立在同一份污染資料上。

### 2.2 `threshold = 2.0` — ❌ 這不是品質門檻，是恆定的中位數切點

`--threshold` 的 help 寫「AI 評分安全下限」（`ai_report.py:1542`），`STRATEGY_GUIDE.md:19` 也把 `score ≥ 2.0` 列為進場條件 1。但這在數學上恆為真：

```
score = pct_rank_mom × 3 + pct_rank_trend × 1  ∈ (0, 4]
```

pct rank 在 N 檔的 universe 內取值 `{1/N, …, 1}`。對任一標的做反射 `k → N+1-k`，可得 `score → 4(1+1/N) − score`，即 score 分布**對稱於 `2 + 2/N`**。因此 `score ≥ 2.0` 的通過比例恆為 ~50%（N=60 時略高於 50%），**與動量因子本身好不好完全無關**。在 Top-60 池中，每天永遠有約 30 檔「通過安全下限」——包括 2022 年那種全面下跌的月份。

實際生效的過濾只有三個：`prev_close > 60MA`、regime scale、Top-K 排序。文件把 threshold 描述成風控條件，是對策略行為的誤解，也是為什麼 2.1 節的退化情境（universe 只剩 1 檔時 score = 4.0）能一路無阻通過。

### 2.3 事件驅動回測引擎 — ✅ 時序正確，⚠️ 少數邊界問題

**做得對的部分（逐項確認）：**

| 項目 | 位置 | 評估 |
|---|---|---|
| 訊號 `i-1` / 成交 `open[i]` | `event_backtest.py:698-701` | ✅ 無 look-ahead |
| 大盤 regime 用 `dates[i-1]` | `event_backtest.py:206` | ✅ 已修正同日偷看 |
| 成交量確認用 `i-1` | `event_backtest.py:724` | ✅ 開盤時確實不知當日總量 |
| ATR 用 `i-1` | `event_backtest.py:1043` | ✅ |
| `_compute_atr` 真 True Range | `event_backtest.py:180-193` | ✅ `max(H-L, |H-C₋₁|, |L-C₋₁|)` 標準寫法 |
| 停損優先於停利 | `event_backtest.py:525-544` | ✅ 保守且一致 |
| 跳空穿價成交 | `event_backtest.py:527-530` | ✅ 開盤已跌破停損則以開盤價成交，非常務實 |
| `is_tradable_bar` | `event_backtest.py:432-449` | ✅ 停牌日不製造假成交 |
| Close 只 ffill 1 天，OHLV 不 ffill | `ai_strategy.py:126-129` | ✅ 避免假流動性，難得的細心 |
| 權益 = 現金 + 持倉市值 | `event_backtest.py:1071-1078` | ✅ 含未實現損益 |

**20 天到期出場 — ✅ 正確。** `days_held` 在 Step 1 遞增，而進場在 Step 3，所以進場日 `days_held = 0`，第 20 個交易日觸發 `>= 20`，等於進場後第 20 個交易日以收盤價平倉。語意正確。

**⚠️ 進場日盤中不檢查 TP/SL。** 部位在 day *i* 的 Step 3 建立，而 Step 1 早已跑完，所以 day *i* 的 high/low 從未用於出場判定，第一次檢查是 day *i+1*。實務上進場當天掛的停損單是會觸發的。因 TP/SL 是 4/3 倍 ATR，首日觸發機率低，屬次要問題，但屬於回測與實盤的行為差異。

**⚠️ 板塊分類不完整。** `event_backtest.py:798-799` 的電子股前綴少了 `5xxx`（如 5274 已在股池內）、`8xxx`。且 `max_elec_total = int(10 × 0.75) = 7`，與 `top_k = 7` 相等，**sector cap 實質上從不 binding**——`STRATEGY_GUIDE.md:24` 列為進場條件 6 的「板塊上限 75%」實際上是無效條款。

**⚠️ `dd_pause` off-by-one。** `event_backtest.py:615-620`：設為 5 之後同一輪立刻遞減成 4，實際暫停 4 天。預設 `dd_pause_pct=1.0`（停用）所以不影響 production，但 README 建議實盤設 0.15 時會生效。

**⚠️ `event_backtest.py:961`** 相關性過濾補位時用 `top_k` 而非 `effective_top_k`，會繞過 dynamic top-k 的縮減。預設 `dynamic_topk=False`，暫不影響。

**ℹ️ 死碼。** `ai_report.py:1874` 的 `market_close = bench_raw * bench_raw.iloc[0]`——`fetch_benchmark` 回傳的已是歸一化到 1.0 的序列（`benchmark.py:71`），所以這是乘以 1.0 的 no-op。因 MA 比較具尺度不變性而無害，但會誤導後續維護者。

**ℹ️ ATR 雙軌但無害。** `ai_strategy.py:241` 的 `close.pct_change().abs().rolling(20).mean() * close` 不是真 ATR（無 High/Low）。所幸 `ai_report.py:1947` **沒有把 `atr_df` 傳給 `run()`**，引擎因此走內部的 `_compute_atr`（真 TR）。production 路徑正確，但這是個「靠沒傳參數才對」的脆弱設計。

### 2.4 TP/SL（ATR 自適應） — ✅ 回測內合理，❌ 出到訂單時錨點錯誤

回測內：`tp = actual_entry + 4×ATR`、`sl = actual_entry − 3×ATR`，`actual_entry` 是**含滑價的實際成交價**（`event_backtest.py:1049-1050`），錨點正確。4:3 的 R:R 配上 57.7% 勝率在數學上自洽。

但 `ai_report.py:410-411` 產生訂單時：

```python
tp_price = price + atr_val * tp_atr_mult   # price = 訊號日「收盤價」
sl_price = price - atr_val * sl_atr_mult
```

而 `paper_tracker.py:279` 是以**隔日開盤價**成交，卻沿用這組以收盤價為錨的 TP/SL。只要有隔夜跳空，實際的 R:R 就偏離設計值——跳空上漲時停利距離被壓縮、停損距離被放大（風險放大），跳空下跌時反之。這是回測與實盤之間一個可量化、可修復的偏差。

另外 `ai_report.py:410-411` 的 fallback 預設是 `3.0/2.0`，與 production 的 `4.0/3.0` 不同。目前 config 一定會帶值所以不觸發，但屬潛在地雷。

### 2.5 Paper Trading 與回測一致性 — ❌ 資金管理完全不同

這是**最需要立即處理的邏輯落差**。

| 面向 | 回測 (`event_backtest.py`) | Paper (`paper_tracker.py`) |
|---|---|---|
| 單筆部位 | `current_equity × 0.10`（固定 10%） | `(現金 − 10% 保留) ÷ 當日候選數`（`:301`）|
| Regime 縮放 | `× regime_scale`（1.0/0.7/0.4/0.10）| **完全沒有** |
| 最大集中度 | 10%（弱勢時降到 1%） | 若當日只有 1 檔訊號 → **~90% 押單一標的** |
| 板塊上限 / 相關性過濾 | 有（雖不 binding） | 無 |
| 股數 | 小數股 | `int()` 整數股（零股）|

`paper_tracker.py:301` 的 `gross_budget = available_cash / remaining_candidates` 意味著：假設某天大盤在 60MA 之下（回測會把曝險壓到 10%），而當天只有 1 檔訊號通過，paper tracker 會把約 90% 的資金投入那一檔。**回測的最重要風控（漸進式 regime 曝險）在 live path 完全不存在**——因為 `ai_report.py:356-376` 產生交易計畫時只檢查 `score >= threshold`、`price > 60MA`、`Top-K`，沒有任何 regime 判斷。

其他 paper tracker 缺陷：

- **`paper_tracker.py:236` `pnl_pct = (exit/entry − 1) × 100` 不含成本**，而回測的 `Return_Pct` 含全部成本（`event_backtest.py:570`）。兩邊報表的「平均報酬」不可直接比較，paper 端偏樂觀約 0.58%/筆。
- **`paper_tracker.py:397` PF 計算 bug**：`total_loss = abs(sum(...)) if losses else 1`，若尚無虧損交易，`pf = total_profit / 1`，會把**損益金額當成 Profit Factor 顯示**（例如「Profit Factor 5000.00」）。
- **`paper_tracker.py:412` `ann_return = total_return × (252 / n_days)`** 是線性外推且不複利，樣本數小時會產生極端數字，卻以「年化報酬」的名義顯著呈現。
- **除息日誤觸停損（實質 bug）**：程式全程未指定 `auto_adjust`，yfinance 現行預設為 `True`，即歷史價格會被回溯還原。但 `paper_equity.json` 裡存的 `entry`/`tp`/`sl` 是當時的名目價格。台股配息集中在 7-9 月且殖利率常達 3-7%，**持倉跨越除息日時，市價自然除息下跌，而 stale 的 SL 不會跟著調整 → 產生虛假停損**，且 `pnl_pct` 也少計了股利。回測因全程使用一致的還原價序列而不受影響——這正是 backtest/live 分歧的典型來源。
- `prices.get(ticker, pos['entry'])`（`:361`）在無報價時以進場價 mark-to-market，會掩蓋停牌股的損失。

### 2.6 交易成本模型 — ✅ 準確且偏保守

- 買進 0.1425%：✅ 台股法定券商手續費上限，未套用實務常見的 2-6 折折扣 → **保守**。
- 賣出 0.4425% = 0.1425% + 0.3% 證交稅：✅ 現股賣出正確。
- 滑價 10bps 進出各一次：⚠️ 對權值股合理，對零股與中小型股偏樂觀（`STRATEGY_GUIDE.md:123` 自己也承認實盤可能 30-50bps）。

**❌ 但整張成本模型建立在一個不可執行的成交假設上：零股問題。** 初始資金 200,000、單筆 10% = 20,000。以 2454 (聯發科，~3400 元) 為例只能買 5.8 股，2330 也不到 20 股——**幾乎每一筆都是零股**。回測用小數股、paper 用 `int()` 整數股，兩者都假設「以當日開盤價成交」，但台股盤中零股是 09:10 起每分鐘集合競價、**不參與開盤競價**，流動性與價格都與整股開盤價不同。也就是說 `t+1 open` 這個成交模型對本策略的實際資金規模是**不成立的**。要嘛把資金放大到每筆足夠買整張（單筆 ≥ 20 萬，總資金 ≥ 200 萬），要嘛把成交模型改成零股競價模型。

---

## 3. 策略分析

### 3.1 優點

1. **因子選擇有學術支撐。** 20 日 cross-sectional momentum + 60MA trend 是文獻中最穩健的兩個橫截面因子，3:1 的權重讓動量主導、趨勢做確認，設計合理。消融實驗顯示加入殘差動量、籌碼、量能、平滑度全部變差——**這個「什麼都沒用」的結論本身是可信的**，因為它與「簡單動量難以被改進」的文獻一致，而不是往「我又找到一個新 alpha」的方向自我強化。
2. **Regime 漸進式曝險是對的方向。** 相較於 binary on/off，四段式（100/70/40/10%）避免了開關震盪，floor 10% 保留了 whipsaw 後的再進場能力。v8.5 的 Breadth Regime（用 Top-60 中站上 20MA 的比例修正 0050 被台積電綁架的盲點）是這份 repo 裡最有洞見的改動。
3. **驗證基礎建設超出業餘水準。** DSR 公式與 Bailey/López de Prado 一致（`deflated_sharpe.py:131` 的 `1 − γ₃·SR + (γ₄−1)/4·SR²` 正確、`expected_max_sharpe` 的 `(1−γ)Z⁻¹(1−1/N) + γZ⁻¹(1−1/(Ne))` 正確）；Monte Carlo v3 改用 daily-return block bootstrap 而非單筆交易 bootstrap 是正確的方法論升級（保留了組合效應）；`experiment_registry` 記錄所有候選而非只記贏家，這正是防止 p-hacking 的正確做法。
4. **對負面結果誠實。** `STRATEGY_GUIDE.md:66-75` 記錄了 nested finalist gate 未通過、PBO = 0.94，因此**拒絕升級參數**；README 第 6 行主動聲明舊版高 Sharpe 不應再沿用。這種紀律在個人量化專案中極為罕見，值得肯定。

### 3.2 缺點與風險

**風險 1（最高）：報酬數字建立在污染資料上，PBO 0.94 是紅色警報。**

`PBO = 0.94` 的意思是：在 94% 的 CSCV 切分中，用 in-sample 選出的最佳參數在 out-of-sample 落到中位數以下——**這比隨機選參數還糟**。作者正確地拒絕了本輪升級，但需要意識到更深的含意：**目前的 production baseline（TP/SL 4.0/3.0、Top-7、Gap 1.5）本身也是用同一套程序在前幾輪選出來的**。PBO 0.94 不只否定了這次的候選，也對 baseline 的來歷投下陰影。

再疊上 §2.1 的股池偏差與 §2.2 的 threshold 無效性，Sharpe 2.365 應被視為**上界的上界**，而非期望值。相比之下，nested walk-forward 的 avg OOS Sharpe 1.025 / min −0.888（`STRATEGY_GUIDE.md:70`）是遠更誠實的數字——但即使這個數字，也仍然繼承了股池偏差。

**風險 2：Top-K = 7 的統計基礎薄弱。**

從 60 檔中選 7 檔（top 11.7%），持有 20 天，樣本期約 3 年 → 575 筆交易。看似樣本充足，但這些交易高度重疊且高度相關：同一天進場的 7 檔在 20 天內共享同一個市場 beta 與（因板塊 cap 無效）常常同一個板塊。**有效獨立樣本數遠低於 575**，可能只有數十個「獨立的 20 天期間 × 少數幾個板塊主題」。這正是 Sharpe 的標準誤被嚴重低估的原因，也是 DSR 的 `n_trials` 參數必須誠實填寫的原因（見改善建議 5）。

**風險 3：動量崩盤下的尾部風險被低估。**

`STRATEGY_GUIDE.md:109-112` 自己指出 Monte Carlo 最差 5% MDD = −44.6%。但這個估計本身偏樂觀，因為：block bootstrap 保留了短期自相關，卻**打散了長期的動量崩盤結構**（2022 全年、2024/8 這類連續數月的反轉）。真實的動量崩盤是一個持續 3-6 個月的體制，不是可以靠 20 天 block 重組出來的。策略在 20 天持有期 + floor 10% 曝險下，遭遇一次台版 2022 可能面對 −50% 以上。

**風險 4：0050 基準對比的意義有限。**

`benchmark.py` 的 0050 buy-and-hold 是正確的實作（歸一化、含配息還原），但作為評判基準不足：
- 策略平均曝險遠低於 100%（regime scale + 現金部位），拿一個滿倉 beta=1 的基準比較，在多頭期間不利策略、在空頭期間有利策略。
- **缺少最關鍵的對照組：等權買進「同一份 EXTENDED_TICKERS」的 buy-and-hold**。`equal_weight_benchmark(close_df)` 有算，但它與策略共享同一份前視名單——這反而讓它成為最重要的診斷工具：**如果策略打不贏這個 EW 基準，那麼所有 alpha 都來自選股名單而非策略邏輯**。目前報表有畫這條線，但沒有把它當成 gate。
- 真正缺的是風險調整後的 alpha（相對 0050 的 beta、資訊比率），目前只有累積超額報酬（`benchmark.py:126` 的 `strat_norm − bench_norm`），這在複利下會誤導。

**風險 5：指標計算的次要偏誤（皆使績效看起來更好）。**

- **Sortino 高估**（`risk_metrics.py:60`）：用 `downside_returns.std()`（負報酬圍繞其自身均值的標準差），標準定義應為 `sqrt(mean(min(r,0)²))`（對全樣本取平方根均值）。前者系統性偏小 → Sortino 偏大。報告的 4.14 應打折。
- **Profit Factor 失真**（`risk_metrics.py:94-96`）：用 `Return_Pct` 的加總而非損益金額。因部位大小隨權益、regime、gap 變動，等權加總百分比與真實 PF 不等價。
- **MDD 只用日收盤**：盤中最大回撤必然更深。

---

## 4. 改善建議

依優先順序（P0 = 上實盤前必須完成）：

### P0-1 加入資料完整性閘門（阻止靜默故障）

這是目前最急迫的問題——系統正在每天產出並發佈 Sharpe 0.03 的退化報表。在 `ai_report.py` 的 Phase 2 之後加入 hard fail：

```python
n_valid = int((close_df.notna().sum() > 100).sum())
if n_valid < 0.7 * len(tickers):
    raise RuntimeError(f"資料完整性不足: {n_valid}/{len(tickers)} 檔有效，中止產報")
avg_univ = universe_mask.sum(axis=1).mean()
if avg_univ < 0.8 * args.universe_size:
    raise RuntimeError(f"Universe 退化: 平均每日僅 {avg_univ:.0f} 檔")
```

同時在 GitHub Actions 中，**產報失敗時不 commit**（目前失敗仍會覆蓋 `stock_report.html` 與 `paper_equity.json`，污染 paper trading 的歷史記錄）。也建議加一條「今日訊號數 = 0 且回測交易數 < 100」的 sanity check。

### P0-2 修復股池的前視偏差

這決定了所有績效數字是否可信。做法（由易到難）：
1. **最低限度**：改用「回測起始日當時」的市值/成交額 Top-N 建構 point-in-time 股池，並納入期間內的下市股票（TWSE 有歷史清單）。
2. **較佳**：把 `EXTENDED_TICKERS` 換成全上市櫃約 1000 檔的全集，讓 `build_liquid_universe` 真正發揮作用——這才是「動態 Universe」原本應該的意思。目前的實作等於在贏家池裡再選贏家。
3. 修完後**重跑全部驗證**（ablation / walk-forward / PBO / DSR）。我的預期是 Sharpe 會顯著下修至 1.0-1.5 區間。這不是壞事——那才是可以拿來做決策的數字。

### P0-3 讓 live path 等於 backtest path

把 `EventDrivenBacktester` 的選股與 sizing 抽成一個共用函式，讓回測迴圈與 `ai_report.generate_report` 的交易計畫呼叫**同一份程式碼**。至少必須補上：

- `ai_report.py:356-376` 的候選篩選加入 `regime_scale`，並把 `regime_scale` 寫進 `orders_*.json`。
- `paper_tracker.py:301` 改為 `trade_amount = current_equity × position_size × regime_scale × gap_scale`，取消「平分所有現金」。
- TP/SL 改為在 `paper_tracker.py` 成交當下、以**實際開盤成交價**重算（`entry ± mult × ATR`，ATR 沿用訂單中的值），與 `event_backtest.py:1049-1050` 對齊。
- `pnl_pct` 改為含成本，與回測的 `Return_Pct` 定義一致。

`next_session_gap_limit()`（`event_backtest.py:281`）已經示範了正確的對齊思路，值得把同樣的做法推廣到 regime 與 sizing。

### P0-4 解決零股問題

二選一：
- **(a)** 把回測與 paper 的資金提高到單筆能買整張的水準（單筆 ≥ 20 萬 → 總資金 ≥ 200 萬），`shares = round(trade_amount / price / 1000) * 1000`，不足一張則跳過。這會顯著減少可交易標的（高價股被排除），需重跑驗證。
- **(b)** 保留零股，但把成交模型改成「09:10 之後零股競價價格」並把滑價提高到 30-50bps。

現況（小數股 + 開盤價成交）是兩者皆非，回測結果不可執行。

### P1-1 修正 threshold 的語意

若要一個真正的品質閘門，改用絕對量而非百分位，例如 `mom_20 > 1.0`（20 日絕對正報酬）或 `mom_20` 的 z-score > 0。或者最起碼，把 CLI help 與 `STRATEGY_GUIDE.md` 的描述改為「等同取 universe 前 50%，實際不 binding」，避免誤以為有一層風控。

### P1-2 修正指標計算

```python
# risk_metrics.py:60 — Sortino 下行波動
downside = np.minimum(daily_returns, 0)
downside_vol = np.sqrt((downside ** 2).mean()) * np.sqrt(252)

# risk_metrics.py:94 — Profit Factor 應以損益金額計
# 需在 event_backtest.py 的 trade_record 中加入 'PnL_Amount' 欄位
```

順便修 `paper_tracker.py:397` 的 PF 除以 1、以及 `:412` 的線性年化（改為 `(1+r)**(252/n) - 1`，並在 n < 60 時顯示「樣本不足」而非數字）。

### P1-3 誠實化 DSR 的 `n_trials`

`compute_deflated_sharpe(n_trials=...)` 目前由使用者手填。根據 `ablation_results.csv`、`factor_search_results.csv`、`sweep_log_*.csv` 與 `STRATEGY_GUIDE.md` 記載的「七波 60+ 組」，實際嘗試次數應在**數百至上千**量級。建議讓 `experiment_registry` 自動累計歷史 trial 數並餵給 DSR——這正是 registry 存在的意義，目前沒有接起來。以 n_trials = 500 重算，Sharpe 2.365 的 DSR 機率會大幅下降。

### P1-4 修正 PBO 的 CSCV 實作

`pbo_cscv.py:82-93` 刻意移除了互補配對（`C(8,4)=70 → 35`），註解說是避免「double-counting」。但 Bailey et al. 的 CSCV 之所以叫 **Symmetric**，正是因為要同時評估 `(train=S, test=Sᶜ)` 與 `(train=Sᶜ, test=S)`——兩個方向會選出不同的最佳配置、產生不同的 logit，不是重複計算。移除一半會在時間序列有體制不對稱時引入偏誤。建議移除去重邏輯，恢復完整 70 組。

（另 `:119` 的 percentile 含自身，標準做法用 `r/(N+1)`，會輕微低估 PBO，屬次要。）

### P2 其他

- 補上進場日的盤中 TP/SL 檢查。
- 修正板塊前綴表（加入 `5x`/`8x`），並把 `sector_max_pct` 降到 0.5 讓它真正 binding，重測。
- `dd_pause` off-by-one。
- 移除 `ai_report.py:1874` 的 no-op 與 `ai_strategy.py:241` 的假 ATR（或改為真 ATR 以免誤用）。
- 把「策略 vs 等權持有同一股池」設為明確的 gate：若打不贏，說明 alpha 來自名單而非邏輯。

---

## 5. 結論：是否可信賴用於實盤操作

**目前不可以。** 分三層說明：

**第一層 — 工程層面（可在數天內修復）。** Production pipeline 正在靜默故障並發佈退化報表；paper trading 的資金管理與回測是兩套不同的策略；零股成交模型不可執行。這三件事讓「用 paper trading 驗證後再上實盤」這條原本正確的路徑失效了——**目前的 paper trading 並沒有在驗證 v8.5**。修完 P0-1、P0-3、P0-4 之後，paper trading 才開始具有驗證價值，此時應**重置 `paper_equity.json` 重新累積**，因為既有記錄是用錯誤的 sizing 產生的。

**第二層 — 統計層面（需數週，且結論可能不利）。** Sharpe 2.365 不是策略的期望績效，而是「在事後選出的贏家股池上、經過數百次參數搜尋後、由一個實質不 binding 的門檻篩選出的」樣本內數字。PBO 0.94 已經是系統自己發出的警告。修復股池偏差並重跑全套驗證之前，任何基於這個數字的部位規模決策都是危險的。誠實的預期應該錨定在 nested walk-forward 的 **avg OOS Sharpe ≈ 1.0、min ≈ −0.9**——而且這還是偏樂觀的上界。

**第三層 — 策略層面（結構性）。** 撇開前兩層，20 日動量 + 60MA + regime 曝險本身是一個**方向正確、有文獻支撐、且經過誠實消融測試的策略**。作者已經做對了很多其他人做錯的事：拒絕升級未通過 gate 的參數、記錄所有負面結果、主動聲明舊數字失效、在引擎裡逐條修掉 look-ahead。這些是好的量化紀律。問題不在策略構想，在於**驗證管線的地基（資料）與屋頂（live path）沒有跟上引擎本身的嚴謹度**。

**建議路徑：**

1. 立即停止把失敗的 run commit 上去（P0-1），並在 README 頂端標註目前報表數據不可用。
2. 修 P0-2 股池偏差 → 重跑 ablation / walk-forward / PBO / DSR → 接受新的（較低的）baseline 數字。
3. 修 P0-3、P0-4 → 重置 paper trading → 累積**至少 6 個月**、涵蓋一次 regime 切換（大盤跌破 60MA）的記錄。
4. 比對 paper 與同期回測的逐筆成交價差、勝率、MDD。**差異在 15% 以內**才考慮小額實盤。
5. 實盤起始資金依 P0-4 的選擇而定；若走整張路線則需 ≥ 200 萬，並依 `STRATEGY_GUIDE.md:132` 的外部 hardstop（−8~10% 全平倉）執行——那條建議是對的，而且**必須是外部的**，因為消融測試已證明內建 MDD breaker 有害。

最後一點值得強調：`STRATEGY_GUIDE.md:143` 寫著「未經 paper trading 驗證就投入實盤」是絕對不要做的事。這條規則是對的——只是目前的 paper trading 因為 §2.5 的分歧而尚未真正開始驗證。把那件事修好，是通往實盤最短的一條路。
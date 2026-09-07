# EVAL_TRADER_PLAN 評估報告（Codex Review）

**評估日期：** 2026-08-29
**評估對象：** `docs/EVAL_TRADER_PLAN.md`
**總評：** **4.0 / 10 — 需重大修訂後再實作**

## 一、執行摘要

這份計畫抓到一個正確方向：先建立簡單、可解釋、能持續累積交易樣本的 deterministic baseline，再考慮 TradingAgents。但目前的 `eval_trader.py` 並不能真正「修復或評估 MR20 與 Top-7」，而是繞過兩者，另建一個 20MA + 爆量的新策略。它可以作為第三個寬鬆對照組，不能取代原策略的診斷與評估。

更關鍵的是，計畫所稱的「零交易」並非單一問題：

- MR20 在 2026-08-27 的實際 funnel 是 `1085 → 1084 → 50 → 26 → 3 → 0`；最後是 RSI gate 將 3 個 pullback 候選全部排除，屬於**零訊號**。
- Top-7 歷史上能產出有效每日訊號，但曾因滿倉、資金鎖住與 tracker 中斷而沒有新成交，屬於**有訊號、無容量／無成交**。現有分析記錄了 28 筆已平倉交易，不能籠統稱為「長期零交易」。
- 目前獨立 Top-7 state 的 2 筆委託因排程晚於 execution date 而成為 `CANCELLED_EXPIRED`，屬於**排程／freshness 問題**。
- 系統過去也曾把 `closed_trades=0` 誤讀成零成交；現有監控已把 fill、cancel、pending、open、closed 分開。

因此，正確目標應是「找出訊號、委託、成交、持倉與平倉各層的流失原因，並用同一個無前視、含成本的引擎比較 MR20、Top-7 與寬鬆 baseline」，而不是以「降低門檻確保交易」作為唯一解法。

## 二、逐項評分

| 評估項目 | 分數 | 結論 |
|---|---:|---|
| 1. 是否解決零交易問題 | **4/10** | 可增加一個較寬鬆策略的候選數，但未修復原 MR20／Top-7，也未處理容量、排程與成交率。 |
| 2. 信號邏輯（20MA + 量能） | **4/10** | 適合當簡單 baseline，但不是完整動量定義；排名、時點、流動性與 regime 均未定義。 |
| 3. Paper trade 模擬 | **2/10** | 同日收盤訊號、同日收盤成交有 look-ahead／不可成交問題；成本、滑價、成交順序等規格缺失。 |
| 4. 評估指標 | **5/10** | 六項核心指標有基本覆蓋，但缺樣本量、成交品質、風險調整與 OOS 穩健性指標；Alpha 定義不清。 |
| 5. 技術約束 | **5/10** | 單檔 MVP 可做，但全市場 yfinance + 模擬 + 報告限制在 200–300 行，會犧牲可靠性且重複現有程式。 |
| 6. 遺漏與風險控制 | **3/10** | 缺資料品質、偏差、台股交易規則、資金配置、可重現性與測試／驗收條件。 |
| 7. TradingAgents 整合準備 | **5/10** | 先做 baseline 是對的，但沒有 adapter contract、實驗設計、資料截止時點、成本與非決定性治理。 |

**加權總評：4.0 / 10。** 若把它重新定位成「第三個 baseline 的概念草案」，可給 6/10；若視為可直接交付的 MR20／Top-7 評估系統規格，目前只有 4/10。

## 三、詳細評估

### 1. 計畫是否解決零交易問題？

**只部分解決，而且解決的是「取得更多 baseline 樣本」，不是原問題。**

`20MA 之上 + 當日量能放大` 通常比 MR20 的五重 gate 寬鬆，歷史回放時很可能產生較多信號。不過「門檻低」不能推出「確保有交易」：市場可能沒有符合 1.5 倍量能的股票、資料可能缺失、Top-5 可能已持有、資金或持倉槽可能已滿，委託也可能不成交。

原系統已有直接證據支持應先分層診斷：

- MR20 的條件依序是流動性、趨勢、回檔、RSI、止跌確認；實作位於 `strategy/mr20_strategy.py:349-438`。
- 最新含診斷的 MR20 artifact 顯示，50 檔流動性股票中 26 檔通過趨勢、3 檔通過回檔、0 檔通過 RSI。這支持「RSI 與 pullback 組合過窄」的假說，不支持把所有零交易都歸因於策略太複雜。
- Top-7 的結構性卡點記錄於 `docs/TURNOVER_MECHANISM_ANALYSIS.md:9-18` 與 `:38-51`：訊號存在，但滿倉與資金不足阻擋交易。
- Phase 0 已將 fill／cancel／pending／open／closed 分開，見 `docs/PHASE0_REVIEW.md:41-48`。

**建議：** 保留 MA+量能作為 `baseline_ma20_volume_v1`，但評估系統同時輸入原生 MR20 與 Top-7 orders。報告至少列出：

`universe → valid_data → signal → selected → pending → filled → open → closed`，並按取消原因分組。零訊號、零成交、零平倉不可再混用。

### 2. 20MA + 量能放大信號是否合理？

**作為低複雜度 baseline 合理；作為獨立可投資策略定義不足。**

優點：

- 規則簡單、可解釋，適合當 sanity-check baseline。
- 20MA 可排除部分弱勢股，量能可確認市場參與度。
- 相較 MR20 的多重交集 gate，較容易累積交易樣本。

主要缺陷：

1. **「站上 20MA」不是動量本身。** `Close > MA20` 只是趨勢狀態，可能已站上數月；若要稱動量，至少加入 20 日報酬、MA 斜率或今日突破／cross-over 的明確定義。
2. **Top-5 排名沒有 score。** 布林條件只能決定合格與否，無法決定誰是前五名。應固定 score，例如 `0.6 × percentile(20d return) + 0.4 × percentile(volume ratio)`，並固定同分排序。
3. **量均值必須避免自我包含。** 建議 `volume_ratio_t = volume_t / mean(volume[t-20:t-1])`，即基準使用 `.shift(1).rolling(20)`；若把當日爆量放進均量分母，定義會被稀釋且與直覺不同。
4. **1.5 倍沒有實證依據。** 它可能偏好財報、處置、事件與短線追價股。應先報告 1.2／1.5／2.0 的 signal coverage，不可為了交易數量挑出績效最好的門檻。
5. **缺少可交易 universe。** 至少需全體 TWSE 普通股、20 日平均成交額門檻、有效價格／成交量與足夠歷史；現有 `strategy.universe` 已有官方清單、快取與存活者偏差說明，可直接重用。
6. **缺少市場 regime。** repository 的既有實證認為 regime 與流動性控制比新增因子重要，見 `docs/INVESTMENT_STRATEGY.md:158-165`。若 baseline 刻意不加 regime，應明確標示為「無 regime 對照組」，而不是默認它足夠安全。
7. **需分開研究目的與頻率目標。** 最小交易數是統計驗收條件，不應成為調參目標，否則容易把噪音當訊號。

建議先定義：

```text
eligible = common_stock
           and history >= 60 sessions
           and avg_turnover_20d in daily Top-100 (or fixed minimum)

trend = close_t > MA20_t and MA20_t > MA20_{t-5}
volume_ratio = volume_t / mean(volume_{t-20:t-1})
signal = trend and volume_ratio >= 1.5
score = percentile(return_20d) + percentile(log(volume_ratio))
rank = score desc, turnover desc, ticker asc
```

這仍是待驗證的 baseline，不代表有 alpha。

### 3. Paper trade 模擬是否正確？

**目前規格不正確，且不足以讓兩個實作者得到相同結果。**

最大問題是：計畫用當日收盤價與當日完整成交量產生信號，卻又以「信號日收盤價」進場。等日 K 完成後才知道信號，不可能回到同一收盤價成交；歷史回測這樣做會形成 look-ahead。除非明確使用收盤前可取得的固定時間快照並模擬 MOC，否則應改為：

```text
D 日收盤後產生信號
D+1 以 D 日收盤價掛買進限價單
若 D+1 Open <= Limit，以 Open 成交；否則取消（或明定 09:30 前逐筆規則）
```

這正是現有共用成交函式 `strategy/order_execution.py:60-90` 與 `independent_sim.py:772-932` 已實作的模型，沒有理由在新單檔中重寫。

其他必須補齊的規格：

- ATR 的公式、回溯期、平滑法及時點；只能使用 D 日以前可得資料。
- TP／SL 應以**實際 fill price** 為錨，而非 signal close；現有實作在 `independent_sim.py:873-917` 已如此處理。
- 日 K 同日同時觸及 TP 與 SL 時，應採保守 `SL > TP > TIME`，或改用分鐘資料；現有邏輯見 `independent_sim.py:939-976`。
- 缺口穿越停損／停利時應以 open 或可成交價處理，不能假設成交於門檻價。
- 是否允許進場當日觸發 TP／SL。現有 simulator 會跳過 entry date（`independent_sim.py:948-950`），這本身需重新確認，因為開盤成交後當日仍可能觸發風控。
- 買賣手續費、證交稅、滑價、最低手續費、整股／零股規則與現金保留。
- 每檔部位比例、最大總曝險、同日多單的資金分配順序、重複訊號及已持有股票處理。
- 停牌、無量、缺 bar、漲跌停、下市、除權息與 corporate actions。
- 20 個「交易日」而非日曆日；採 XTAI calendar。
- 每日 mark-to-market、現金與未實現損益必須進入 equity curve，否則 Sharpe／MDD 錯誤。

現有 `independent_sim.py` 已包含 next-open 成交、現金與部位、成本／滑價、TP／SL／TIME、0050 與報告；應抽取／重用，避免另造一套語意不同的 250 行 simulator。

### 4. 評估指標是否完整？

目前六項足以做首頁摘要，但不足以判定策略品質：

- `Alpha vs 0050` 定義不清。若只是 `策略累積報酬 - 0050 累積報酬`，應稱 **excess return**，不是 beta-adjusted alpha。
- Sharpe 必須由**每日完整投組報酬**計算，註明無風險利率、`ddof`、年化 252 與最小樣本數。現有 simulator 至少 30 個交易日才顯示 Sharpe（`independent_sim.py:1153-1157`），新計畫也應採取最低門檻。
- Win rate 與平均交易報酬在小樣本下極不穩定，必須同時報告交易數與信賴區間／bootstrap 區間。

MVP 至少應包含：

| 類別 | 必備指標 |
|---|---|
| 報酬 | Total return、CAGR／annualized return、monthly returns、excess return vs 0050 |
| 風險 | Annualized volatility、MDD、drawdown duration、Sharpe、Sortino、Calmar |
| 交易 | Signal count、order count、fill count／rate、closed trades、win rate、avg win／loss、expectancy、profit factor、avg／median hold days |
| 資金 | Average／max exposure、cash ratio、turnover、交易成本總額與成本前後報酬 |
| 穩健性 | In-sample／OOS 分離、walk-forward folds、按多／空／震盪 regime 分組、最小樣本警告 |
| 基準 | 0050 同期 Total return、Sharpe、MDD；若宣稱 alpha，再估 beta 與 regression alpha |

repository 已有較完整的 `strategy/risk_metrics.py:17-150`，涵蓋 CAGR、波動、Sortino、Calmar、月度與交易統計，應優先重用，而非只重做六項。

### 5. 技術約束是否可行？

**可做 demo，不適合作為可靠評估系統的硬限制。**

- `yfinance + pandas + 單一 CLI` 可快速完成 prototype。
- 每天下載約 1,085 檔 TWSE 股票，則需要 batching、retry、cache、缺檔 coverage gate、symbol mapping、速率限制與資料快照。這些都塞進 200–300 行會迫使實作者省略必要檢查。
- yfinance 非交易所權威資料源；批次缺值、調整價設定、除權息與 historical revisions 都需記錄。應保存 raw snapshot／下載時間／套件版本，使 run 可重現。
- 用「目前上市清單」回測歷史會有存活者偏差。現有 `strategy/universe.py:18-27` 已明確承認此限制。
- 新單檔會重複 `fetch_panel_data`、XTAI calendar、order execution、risk metrics 與 universe cache，增加定義漂移。

建議將「ponytail」解讀為**最小新程式量**，而非**所有邏輯塞入單檔**：

```text
eval_trader.py                 # 只做 orchestration / CLI
strategy/eval_baseline.py      # MA+volume 純函式
既有 strategy/order_execution.py
既有 strategy/risk_metrics.py
既有 strategy/universe.py
既有 independent_sim ledger/schema（或抽出共用 engine）
```

新碼仍可控制在約 200–300 行，但核心語意由已有測試的模組提供。

### 6. 遺漏與主要風險

按優先級排序：

#### P0 — 未修正不可開始績效解讀

1. 同日 close signal／close fill 的 look-ahead。
2. 沒有清楚區分零訊號、零委託、零成交、零平倉。
3. Top-5 無排名公式，ATR 與 Alpha 無定義。
4. 沒有成本、滑價、現金、position sizing 與 portfolio equity 規格。
5. 沒有 train／validation／OOS 或 walk-forward，容易以完整歷史調門檻造成 data snooping。
6. 沒有「同一 execution engine」要求，三個策略無法公平比較。

#### P1 — 會使結果偏樂觀或不可重現

1. 存活者偏差、Yahoo 缺值與 corporate-action 處理。
2. current volume 是否包含於 20 日均量；資料截止時間未固定。
3. 同日 TP／SL、gap、entry-day exit、停牌與漲跌停的歧義。
4. 沒有 run manifest：資料區間、universe snapshot hash、參數、程式 commit、套件版本、時區。
5. 只有單一 `eval_report.json`，缺 schema version、atomic write、按 run_id 保存，容易覆蓋歷史證據。
6. 以「確保有交易」導向調參，形成 trade-frequency overfitting。

#### P2 — 上線前補強

1. 異常中斷後 resume／idempotency／duplicate order。
2. 網路 retry、cache freshness、資料 coverage 告警。
3. 參數敏感度、bootstrap／Monte Carlo、regime 分層與 benchmark 對齊。
4. JSON schema tests、golden fixture、look-ahead regression test、成本與邊界測試。

### 7. 與 TradingAgents 的整合可能性

**技術上可行，但應是 Phase 2 的候選覆核器／排序器，不應取代 deterministic simulator。**

截至本次評估，tw_stocker 內沒有 TradingAgents adapter，只有 `docs/PHASE0_REVIEW.md:64-66` 提到 Phase 1。TradingAgents 官方專案提供 Python package，核心呼叫形式是 `TradingAgentsGraph(...).propagate(ticker, date)`；框架包含技術、基本面、新聞、情緒、研究辯論、交易與風控角色，也支援 Yahoo Finance 能覆蓋的 exchange-suffixed ticker。官方同時明確指出 LLM 與 live text data 不可完全重現，歷史日期的新聞／社群資料仍可能反映現在，回測不能假設 deterministic。

建議整合架構：

```text
TWSE universe
  → deterministic MA+volume / MR20 / Top-7 candidate generation
  → 只將每日 Top-N 候選送入 TradingAgents（成本與延遲可控）
  → adapter 將輸出正規化為 APPROVE / REJECT / REDUCE + confidence
  → portfolio rules / deterministic order engine
  → paper ledger + baseline-vs-overlay evaluation
```

Adapter 至少保存：

- `run_id`, `as_of_date`, `evidence_cutoff`, ticker／`.TW` mapping。
- baseline strategy、rank、score、feature snapshot hash。
- TradingAgents version、model provider／model、temperature、prompt／config hash、debate rounds。
- 原始 decision、結構化 action、confidence、rationale、latency、token／API cost、error／fallback。
- 資料來源與每段 evidence 的時間戳，拒絕 `evidence_cutoff` 之後的資料。

實驗應做 chronological A/B：

1. A = deterministic baseline 原樣交易。
2. B = 同一批候選經 TradingAgents approve／reject 或 rerank。
3. A、B 使用完全相同的 execution、position sizing、cost、benchmark 與期間。
4. 報告 B 相對 A 的增量：coverage、reject rate、return、MDD、Sharpe、turnover、API cost、latency。
5. 對 LLM decision 做 immutable cache；歷史回放不可每次重新詢問後挑最好結果。

台股特有風險是英文新聞、基本面與社群 coverage 可能弱於美股；`.TW` 價格可用不代表公司辨識、中文新聞與 TWSE 公告已正確接入。第一階段宜只用 TradingAgents 的 technical／risk subset，或提供自訂台灣資料 adapter，再擴張到新聞與基本面。

參考：TradingAgents [官方 repository 與 package usage](https://github.com/TauricResearch/TradingAgents)、[研究論文](https://arxiv.org/abs/2412.20138)、[Apache-2.0 授權](https://github.com/TauricResearch/TradingAgents/blob/main/LICENSE)。

## 四、建議的修訂版 MVP

### 目標

不是「保證交易」，而是：

> 在無前視、含交易成本、相同成交模型下，量化 MR20、Top-7 與 MA20+Volume baseline 從訊號到平倉的完整 funnel，並產生可重現、可供 TradingAgents A/B 使用的評估資料。

### 策略組

1. `mr20`：原生 orders，不偷偷放寬；用 funnel 說明零訊號原因。
2. `top7`：原生 orders；記錄 capacity／cash／price／expiry 取消原因。
3. `ma20_volume_v1`：新增的寬鬆 baseline，明確定義 universe、score 與 tie-break。

### 統一成交規則

- D close 後產訊號，D+1 next-open limit fill／cancel。
- TP +4 ATR、SL -3 ATR、TIME 20 sessions；全部以 fill price 為錨。
- SL > TP > TIME；gap 以 open；明確決定 entry-day 是否檢查 exit。
- 共用 commission、tax、slippage、calendar、sizing 與 benchmark。

### 輸出

```text
artifacts/eval/<run_id>/
  manifest.json       # commit、資料與參數指紋
  signals.jsonl       # 全部信號與 funnel
  orders.jsonl        # pending／fill／cancel reason
  trades.jsonl        # 成本前後交易明細
  equity.csv          # 每日投組與 0050
  report.json         # schema_version + metrics + warnings
  report.md           # 人讀摘要
```

### 最低驗收條件

- 測試可證明 D 日信號不讀 D+1 資料，且不以 D close 假成交。
- synthetic bars 覆蓋：fill／cancel、gap SL／TP、同日雙觸發、TIME、缺 bar、滿倉、現金不足、重複執行。
- 每個策略輸出 signal／order／fill／open／closed counts 與 reason breakdown。
- 報告沒有把 excess return 標成 alpha；少於 30 日或少量交易時輸出樣本不足警告。
- 同一資料快照與 config 重跑得到相同 deterministic 結果。
- 至少一個固定 fixture 的完整 offline smoke test，不依賴即時 yfinance。

## 五、最終建議

**不建議依原計畫直接開始寫新的 250 行 `eval_trader.py`。** 先完成以下三項修訂：

1. 將 MA20+量能改名並定位為第三個 baseline，而非 MR20／Top-7 的修復。
2. 將同日 close fill 改為次日可成交模型，並重用既有 order execution、risk metrics、universe 與 simulator ledger。
3. 把 acceptance criteria 改成「funnel 可觀測、無前視、同引擎公平比較、可重現」，而不是「確保有交易」。

完成以上 P0 後，這個方案可提升到約 **7/10**；加入 OOS／walk-forward、資料 manifest 與 TradingAgents A/B contract 後，可達 **8/10** 的可執行計畫。

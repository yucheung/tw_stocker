# tw_stocker 評估審查 — 2026-09-07

審查範圍：MR20、TOP7、獨立模擬、paper_tracker、repo 內排程與 eval 產物。程式基準 HEAD：`16e83200dd3537eef399420b88a13c862b0bbe1d`；資料採本次工作樹快照（原已有未提交異動）。僅新增本文件，未修改程式、帳本或排程。

## 判定

**系統 FAIL，4/10；MR20 FAIL，4/10；TOP7 FAIL，4/10。** PASS 的門檻是策略身份／資金／執行模型一致，訊號可追到終態，且績效證據足以支持驗收。這是工程與驗證成熟度評分，不是預測報酬，也不代表已證明策略永遠無效。

**獨立模擬零交易的主要原因是上游產單到規劃入帳之間斷流，加上過期訊號與缺單仍標完成的處理缺陷；不是已送出的單全部因資金不足撤銷。** MR20 起初確實有多日無訊號，但後續已有訊號仍未進帳。TOP7 目錄實際跑的是 Top2，且僅有兩張過期訂單。paper_tracker 是另外一套有成交的帳本，不能把它描述成連續數週零交易。

不能從 repo 檔案斷言是哪一條主機 cron 被停用、失敗或呼叫錯誤。遵守「只讀本工作目錄」，未查系統 crontab、外部排程服務、主機日誌、遠端 Actions runs；以下將排程程式缺陷與實際排程歷史分開判定。

## 1. 先校正現場線索

| 項目 | 實際讀到的證據 | 含義 |
|---|---|---|
| MR20 orders 全是 18B | 13 份：8/18–8/26 七份 18B；8/27、8/28、8/31、9/1 四份 629B 含診斷且空單；9/2 1311B、9/3 1314B 各有一單 | 「全部空包」已過時 |
| MR20 非空單 | 9/2 訊號 `3037`、限價 973、9/3 執行；9/3 訊號 `6446`、限價 1350、9/4 執行 | 已有可供規劃的策略輸出 |
| MR20 state | cash 1,000,000；positions/pending/events/closed 全 0；close-and-plan 無 8/28–9/3 紀錄 | 這兩單未留下 PENDING 或任何拒絕事件，斷點在執行前 |
| TOP7 state | strategy_id=`top2_score_v1`；本金 20,000；max_positions=2；position_size=.45；reserve=.10 | 目錄名稱不等於七檔策略 |
| TOP7 events | 8/24 才規劃 8/14 訊號、應於 8/17 執行的 `2059`／`6213`；8/25 兩單 CANCELLED_EXPIRED | 真正看到的撤單原因是過期，不是缺現金 |
| 9/7 09:36 更新 | MR20 09:36:07、TOP7 09:36:10；兩者最後 processed run 均 `open:2026-09-07`，pending=0 | 空跑 open 也會存檔，mtime 不是交易管線健康證明 |
| paper_equity | 檔案 mtime 9/4；內容最新 equity/daily_signals 其實是 **9/3**，pending `2455` execution_date=9/4 | 檔案同步時間與帳務涵蓋日不同 |
| paper 交易 | 8 次 FILLED、2 次平倉、6 個持倉；最近 FILLED 是 9/2 的 `3406`、`3008` | 「全系統數週零交易」不成立 |

paper 的 28 個事件分布：FILLED=8、CANCELLED_EXPIRED=9、CANCELLED_OPEN_ABOVE_LIMIT=9、CANCELLED_NO_OPEN_PRICE=2；沒有 INSUFFICIENT_CASH 或 NO_CAPACITY。最後現金 101,784.37，保留現金 40,000，尚有約 61,784.37 可用。

## 2. 排程與 pending_orders 三跳追蹤

### MR20：產單 → 規劃 pending → open

1. **產單可證實成功。** `strategy/mr20_strategy.py:739` 生成 orders，`:752` 依 signal_date 寫入 `artifacts/mr20/orders_mr20_YYYYMMDD.json`。9/2、9/3 非空檔的 diagnostic.saved_at 分別為 9/3 13:32:41、9/4 13:32:12（+08:00），比其宣告的次日開盤執行時點晚。若這是首次產出，該開盤已錯過；saved_at 不能排除曾重跑覆寫，故不把首次遲到當成已證實歷史。
2. **規劃跳有明確缺口。** `independent_sim.py:1516` 預設只找 as_of 當日檔，不自動取最近一份。9/4 的 MR20 orders 檔不存在；當日 close-and-plan 卻已在 processed_runs。`:1546` 缺檔只印提示，`:1550` 仍標完成。`:1489` 同日重跑直接跳過，晚到訂單無法補入。實際 state 在 9/2、9/3 沒有 close-and-plan 紀錄，MR20 events 完全為零；這足以把近期零成交定位在規劃前／規劃跳，不能再向後歸因到撮合與 sizing。
3. **open 不會自己產單。** `independent_sim.py:1449` 只讀 execution_date=今天的 pending；`:1453` 即使零單也標記／存檔。故今早 state 更新與空倉長期不動可以同時成立。執行引擎若真的因現金、限價或價格資料拒單，`:818`、`:826`、`:835`、`:859` 會留下終態事件；現有 MR20 帳本沒有這些事件。

**可重現缺陷：** 在記憶體執行實際 `run_close_and_plan`，以缺少 9/4 檔案的現場路徑先跑，再提供訂單路徑重跑同一天，第二次在讀檔前即 `Idempotent skip`。這證明缺單鎖死重試的機制；不宣稱每個歷史缺日都由同一機制導致。

### 排程腳本本身 FAIL

- `run_mr20.sh:20` 用日曆昨天，9/7 得到星期日 9/6，而不是上一個交易日 9/4；`strategy/mr20_strategy.py:310` 拒絕非交易日。腳本啟用 `set -e`，`:30` 非零便退出，`:31` 的失敗處理接不到。週一有效 open 也可能被前置產單失敗一併阻斷。
- 檔頭說 D 日 18:05 產單、D+1 09:35 執行，但 `run_mr20.sh:30`、`:76`、`:84` 無時段／子命令分支，每次都是「昨日產单、昨日 close、今日 open」。在晚間排它也不會產當日訊號；在早上排它則現做昨晚應完成的工作。
- `run_mr20.sh:53` freshness gate 只比較 diagnostic.signal_date，沒有真的比較 execution_date 與本次目標；`:46` command substitution 失敗也會受 `set -e` 提前退出，`:64` 的友善處理到不了。七份舊 18B 檔缺 diagnostic，會被拒絕，但不能據此推論它們當時就是走此版本腳本。
- `.github/workflows/update_ai_report.yml:7` 的 repo 內 cron 是 UTC 09:17（台灣 17:17）；`:95` 跑 ai_report，`:110` 跑 paper_tracker，沒有獨立 MR20／Top2 close/open。它能解釋 paper 與獨立帳本更新不同步，不能证明主機另有哪條 cron。

### TOP7：產單 → 獨立 Top2 pending → open

`ai_report.py:1581` 寫 `artifacts/orders_YYYYMMDD.json`；`independent_sim.py:63`／`:1519` 從該處讀取，`:501` 選最多兩檔，`:744` 建 pending，`:784` 只執行指定日期的單。現場最新 TOP7 檔為 9/3、七筆，**沒有 9/4 當日檔**；Top2 close-and-plan 最後卻標了 9/4。兩筆唯一 PENDING 早在 8/24 就帶著過期 execution_date。

`independent_sim.py:430` 只驗證訊號日與次交易日彼此相容，不驗證它們是否符合本次 as_of；`:700` 保留舊 signal_date、`:701` 保留舊 execution_date。因此「8/14 → 8/17」內部日期合法，卻可以在 8/24 被接受。舊單接入、隔日過期的現場 events 直接支持此根因。

### paper_tracker：JSON → signal → pending → 下一次更新開倉

- `paper_tracker.py:185` 只 glob 頂層 `artifacts/orders_*.json`，不會混讀 MR20 子目錄；`:188` 以 mtime 選最新，沒有 signal_date 新鮮度檢查。`:246` JSON 無訊號時會回退 HTML，缺檔、正常空單、過期資料没有清楚區分。
- `ai_report.py:256`／`:257` 傳 position_size／regime_scale；`paper_tracker.py:234`／`:235` 讀入；`:717`／`:718` 傳入 pending；`:626`／`:627` 開倉使用。**sizing 三跳沒有普遍漏傳。** 現有 pending `2455` 就有 .10／1.0，不能把此案歸因為欄位遺失。
- `paper_tracker.py:704` 建 pending 沒保留 limit_price，之後由 reference_close／entry fallback；現行 builder 令兩者同價，這次不致零成交，但將來限價獨立於 reference_close 就會改變撮合。`:714` 又把 signal_date 改成執行更新的 today，使舊檔來源不易辨識。
- `paper_tracker.py:471` 執行 due，`:479` 將過期單撤掉；`:638` 才是現金拒單。當前六個持倉有一個空位，帳本並非滿倉阻擋全部開倉。

## 3. 策略與 eval 審查

### MR20 — FAIL，4/10

訊號定義有一致的策略意義：流動性 Top50、MA20>MA60、MA60<收盤<MA20、RSI(5)≤40、今日收盤高於昨日（`strategy/mr20_strategy.py:349`、`:375`、`:380`、`:385`、`:390`）。超賣加止跌並非邏輯矛盾，9/2、9/3 的實際入選也反證「永遠不可能有訊號」。

但訊號很稀疏。診斷顯示 8/27 在 RSI 後為零；8/28、8/31、9/1 分別在 RSI 後剩 1／4／2，全部被 bounce 篩光。不能只因少交易就任意放寬條件；需先修傳遞，再比較同模型下的分層／樣本外結果。

舊 `PLAN_new_strategy.md` 使用 RSI≤35，但 `docs/PHASE0_REVIEW.md:21` 已記錄預設統一為40的變更；本次以實際40版本審查，不把舊計畫與新設定差異單獨判成未授權 bug。不同門檻的歷史績效仍須分版。

`artifacts/eval_mr20_60d.md`：63 訊號、7 成交、56 撤單（88.89%）；開高 39、資金不足 17；報酬 -1.83%、Sharpe -0.5795、最大回撤 -6.52%。七筆成交不足以支持穩健有效，也不足以推翻策略概念。此檔沒有完整期間、設定雜湊、資料版本與逐單證據，不能視為目前百萬元、兩檔各45%配置的驗收。

### TOP7 — FAIL，4/10

實際 TOP7 訊號是動能／趨勢排名（`strategy/ai_strategy.py:377`）再經分數門檻、MA60、Top-K 選擇（`ai_report.py:492`、`:508`、`:516`）。其訊號、paper 七檔帳本、獨立 Top2 集中帳戶必須分別識別。

**baseline 並不是 TOP7。** `strategy/eval_baseline.py:1` 明確是 MA20+Volume 控制組，`:19` Top5、`:75` MA20 向上且站上均線、`:77` 量比≥1.5、`:100` 以報酬與量比分位數評分。`eval_trader.py:108` 也只有 baseline／mr20，沒有 TOP7 adapter。

`artifacts/eval_baseline_60d.md` 的 +5.57%、Sharpe 1.4212、15 筆成交不能冒充 TOP7 驗證；其 152/167=91.02% 撤單中，開高87、資金不足65，沒有「已開倉取消」分類。當前 `eval_trader.py:130` 已排除 held/pending 重複標的。若另一次實測有已開倉撤單，須提供那次逐單檔與版本才能歸因，現場兩份 markdown 不支持該說法。

### sizing 與時序的實質缺陷

| 執行器 | 配置／單位 | 問題與影響 |
|---|---|---|
| MR20 independent | 100萬、兩檔45%、保留10%、一股單位 | 目前兩張訂單都買得起，且沒有現金拒單事件 |
| TOP7 目錄 independent | 2萬、兩檔45%、保留10%、一股單位 | 9,000 左右的單筆預算買不起參考價12,235的2059；選股前未按一股含費可負擔性補選，會浪費候選名額 |
| paper | 20萬、max7、當前單10%×regime、保留20%、一股單位 | 資金欄位有傳遞；`paper_tracker.py:630` 卻把合法 regime_scale=0 改為1，原應不進場會變全額，屬風控缺陷，方向不是造成零交易 |
| eval | 100萬、max5、15%、保留20%、1000股單位、最低手續費20 | `eval_trader.py:187` 不按可用現金縮單，`:190` 向下取整張；單價高於約149.79時，15萬預算連一張都買不到，會記 INSUFFICIENT_CASH，即使現金充足 |

此外 `paper_tracker.py:435` 先用當日 high/low/close 平舊倉，再於 `:466` 回頭做當日 open 買進，可能用盤中／收盤才釋放的資金或名額回填早上交易；`:623` sizing 又用當日 close。`independent_sim.py:849` 同樣讀 bars.close 估值，而 `:1449` 僅下載待買股票、沒有全面抓已持倉 open，估值常退回成本。eval `:177` 用持倉 open；三套模型因此不能直接比較。這些是績效可信度問題，不能取代本案已證實的產單傳遞根因。

**TOP7 的正式回測也有前視 sizing：** `strategy/event_backtest.py:632` 用第 i 日 close 計算仍持有股票市值，`:1092` 再用這個 current_equity 配置第 i 日 open 的新倉（開盤成交邏輯在 `:1021`）。有持倉時，早上不可能知道該日收盤價；因此即使另有 TOP7 回測績效，也應修正此時序後重驗，不能只用 baseline 成績替代。

eval 尚有缺價處理風險：`eval_trader.py:223` 取 close 後未先檢查有限值，`:228` 增加持有天數，`:253` 直接納入市值；NaN 可污染權益，TIME 出場亦可能用 NaN。這是由程式確認的條件式風險，現有 markdown 無逐日資料，不能斷言該次60日結果已受污染。

## 4. 修復建議（僅建議，未實作）

| 優先級 | file:line | 建議與驗收條件 |
|---|---|---|
| P0 | `run_mr20.sh:20`, `:30`, `:76`, `:84` | 分開收盤產單／規劃與次日 open；使用交易日曆和台北時區，open 不依赖即時重產昨日資料。驗收週一、假日、上游失敗時的生命週期 |
| P0 | `independent_sim.py:1489`, `:1546`, `:1550` | 分開結算完成與訂單規劃完成；缺檔是可重試狀態，不能封死同日補送；已有完整 diagnostic 的正常空單才能標「規劃成功零單」 |
| P0 | `independent_sim.py:430`, `:700`; `paper_tracker.py:188`, `:714` | 將 signal_date、execution_date 與本次交易日對齊驗證；保留來源日期、檔案雜湊與拒絕原因；過期單不得先入 PENDING |
| P0 | `.github/workflows/update_ai_report.yml:110`, `:135`; `.gitignore:175` | 明確指定產單、planner、executor 的資料交付者；目前新頂層 JSON 被 ignore，`git add -u artifacts` 只帶已追蹤檔（現場 orders 僅 20260526 被追蹤），若靠 git 傳給 VPS，新每日 orders 不會送到。改為明確 artifact 交付並驗證消費回執；不要盲目提交所有生成檔 |
| P0 | `paper_tracker.py:630`, `:435`, `:623` | 保留合法零曝險；以 open 時可得資金／名額先撮合，再處理當日盤中與收盤出場；避免當日 close 前視 |
| P0 | `strategy/event_backtest.py:632`, `:1092` | 正式 TOP7 回測的 open sizing 改用當時可得估值；用改變當日 close 而維持 open 不變的對照，驗證早上成交股數不跟著未來 close 改變 |
| P1 | `independent_sim.py:59`, `:501`, `:744`, `:781` | 清楚命名 TOP7 訊號與 Top2 帳戶；固定可追溯配置，選股時排除買不起一股的候選並補下一名；若需要跟隨上游 sizing/regime，就完整傳遞且明定覆蓋規則 |
| P1 | `eval_trader.py:28`, `:36`, `:187`, `:190`; `strategy/eval_baseline.py:15` | 加入真正 TOP7 評估入口；將本金、持倉數、reserve、股數單位、費用與執行順序對齊目標帳戶；拆分預算不足買一股／一張與總現金不足，避免混為同一取消原因 |
| P1 | `eval_trader.py:223`, `:253` | 對缺價／非有限值設明確估值與出場規則，逐日驗證 cash、equity、成交價格有限；把資料缺口列入評估報告 |
| P1 | `paper_tracker.py:704`; `independent_sim.py:1449`, `:849` | pending 保留真實 limit_price；持倉估值使用同一時點可得價格，缺價明確降級並留痕 |
| P1 | `independent_sim.py:1453`, `:1555`; `strategy/mr20_strategy.py:289` | 監控每跳的 signal_date、input count、accepted、pending、terminal count、最後成功資料日；缺檔、零訊號、全部拒單分開告警，不能只看 mtime／exit 0 |

先修資料交付與排程，再評估參數；不能用降低 RSI 門檻、放大資金或解除限價來掩蓋零單傳遞。

## 5. 本次驗證與限制

唯讀靜態查核：上述原始碼行號、13 份 MR20 訂單、頂層 TOP7 訂單、三套 JSON 帳本、事件分布與 processed_runs，命令 exit 0。shell 語法檢查 `bash -n run_mr20.sh run_mr20_full.sh run_mr20_status.sh` exit 0；**語法通過不代表日期與排程正確。**

用 `python3 -B` 從 AST 擷取原函式，在記憶體複本執行，未 import 生產入口、未抓行情、未存 state；日期／日曆／外部 IO 以 stub 隔離，以下開盤价均為**合成 open=limit，不是歷史重播成交**。Top2 的 ATR 取既有 PENDING 事件。四情境最終 exit 0，真實輸出：

```text
MR20 synthetic open=limit 20260902 planned 1 status FILLED shares 461
MR20 synthetic open=limit 20260903 planned 1 status FILLED shares 332
TOP2 synthetic open=limit [('2059', 'CANCELLED_INSUFFICIENT_CASH', 0), ('6213', 'FILLED', 19)]
paper pending 2455 synthetic open=limit FILLED shares 38 cash 81805.94
PASS: 4 in-memory scenario assertions; no strategy/state files written
```

缺單後同日重試測試最終 exit 0，真實輸出：

```text
ℹ️ [mr20] 未找到今日訂單檔，略過訂單規劃
✅ Close-and-plan completed for 2026-09-04: 0 positions closed, 0 new orders planned, 0 expired orders cancelled. Equity: 1,000,000 TWD
Notice: Run close-and-plan:2026-09-04 was already processed. Idempotent skip.
PASS: missing artifact marks day processed; retry never loads supplied artifact (mocked IO, no writes)
```

驗證 harness 初次嘗試因漏注入原函式依賴而 NameError（exit 1），補齊隔離依賴後得到上述 exit 0；未改產品程式。未跑完整 pytest、build、lint 或 eval CLI：本次是唯讀審查，未啟動可能建立暫存、下載行情、覆寫產物的流程；不宣稱全套測試通過。既存 eval 報酬僅轉述本地報告，未重新測算。未新增提交，無新 commit SHA。

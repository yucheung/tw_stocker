# Codex R2 審查 — 2026-09-07

**總判定：FAIL，6.0 / 10；未達 ≥8.5 放行門檻。**

審查範圍為 `16e8320..a63681c` 的五個 commits，對照 `docs/REVIEW-opus-20260907.md` §3.2 前五步及 §3.1 三項遺漏。行號皆指本次 HEAD `a63681cd1936c8539b7f167cb4815b7e156c4df9`。本次只新增本文件；既有未提交帳本、報表、訂單及其他檔案不屬於這五個 commits，也未改動。測試與反向驗證使用臨時資料／臨時 git archive，不執行正式產單或正式 runner。

## 1. 前五步逐項驗收

| Opus §3.2 | Commit | 判定 | 分數 /10 | 結論 |
|---|---|---|---:|---|
| 1 時序契約、日曆、時區 | `d431637` | PARTIAL | 6.5 | close/open 拆分、當日 session 檢查、次交易日驗證正確；仍用主機 `date`，錯誤被轉成 exit 0，未提供排程接線證據。 |
| 2 交付、告警、三跳 smoke | `3103b6d` | PARTIAL | 4.5 | 缺 run_id 的檢查與兩跳整合測試有效；不涵蓋真實產單，未修訂單交付、未接每日監控，亦看不到 planning_gaps。 |
| 3 規劃狀態機及原 :1516/:1519 | `28ee4ac` | FAIL | 4.0 | 內容搜尋與顯式缺檔拋錯有效；自動缺檔仍標完成、不可重試，正面違反驗收要求；合法無日期空單會誤判缺檔。 |
| 4 as_of 對齊，含顯式 --orders | `a63681c` | PARTIAL | 6.0 | independent_sim 的顯式舊單拒絕有效且不落地；paper 的檢查可被 HTML fallback 繞過，混合日期亦未逐單拒絕。 |
| 5 regime_scale=0 零曝險 | `a336c1c` | PASS | 9.0 | 實際 sizing 保留零，舊版反向測試確實開倉、新版不開倉；負值歸零也是保守方向。 |

總分採五項等權平均：`(6.5 + 4.5 + 4.0 + 6.0 + 9.0) / 5 = 6.0`。步驟 3 的核心驗收失敗亦是獨立阻擋理由，不能以其他單元測試綠燈抵銷。

## 2. 具體發現，依影響排序

### F1 — P1：自動缺單仍封死補跑，監控同時假綠

`independent_sim.py:1617–1629` 自動搜尋失敗時新增 `planning_gaps`，但仍寫入 `close-and-plan:D`；`:1562–1565` 在任何讀訂單／修復 gap 之前就跳過同日重跑。`check_processed_runs.py:38–40` 只查兩個 run_id，完全不讀 gap，與新增註解所稱「daily check 會呈現」不符。

本次用臨時 MR20 帳戶重現：先執行 D 的 open、自動缺單 close，再寫入合法 D 訂單並用顯式 `orders_path` 重跑 D。實際輸出：

```text
Notice: Run close-and-plan:2026-09-02 was already processed. Idempotent skip.
REPRO {"pending": [], "gaps": ["2026-09-02"], "monitor_missing": []}
```

這正是原本「缺單仍標完成」根因，現在只是多一欄紀錄。缺檔不等於正常零訊號。建議拆分結算與規劃完成狀態，讓規劃缺檔可重試而不重複結算；監控須把未補齊的 planning gap 判為失敗。`:1620` 的「結算已發生不能重跑」不能當作驗收豁免：目前顯式缺檔分支本身就是在結算後拋錯且不落地，或可明確分階段記錄。

### F2 — P1：新增 smoke 未跨產單，交付與每日告警仍未完成

`tests/test_e2e_mr20_smoke.py:1–9` 明確排除產單；`:32` 起手寫訂單，`:63` 起直接呼叫 sim 函式。它確實驗證規劃→隔日 open→FILLED 落地，但沒有呼叫 `generate_mr20_orders`、產單 CLI 或 `run_mr20.sh`，也沒有跨交付路徑。可用固定行情驅動真實產單，不需要為了離線而省略整跳。

`.github/workflows/update_ai_report.yml:135` 仍是 `git add -u artifacts`，不會加入新訂單；`.gitignore:175` 仍忽略頂層 `artifacts/*.json`。五個 commits 沒有提供替代交付方式。repo 的 `.github` 及 runner 搜尋未找到 `check_processed_runs.py` 的每日呼叫；腳本「可掛 cron」尚不是「每天缺跳即告警」。未讀 host crontab，因此不宣稱 host 必然沒排程，但本次不能據此驗收上線。

建議補齊可查驗的排程／交付設定與離線三跳驗收，並驗證漏產單、漏規劃、漏 open 都會被觀測到。

### F3 — P1：paper 拒絕過期 JSON 後仍可重新吃入舊 HTML

`paper_tracker.py:200–202` 回傳 `[]` 表示拒絕舊檔，但 `:254–286` 把空結果視為可 fallback，直接解析未驗日期的 `stock_report.html`。新增 HTML 訊號沒有來源日期，後續 `:724` 又退回 today。

本次臨時目錄放入 2020 年 JSON 及一列可解析買進 HTML，呼叫真正上層 `extract_signals_from_report()`：

```text
⚠️ artifacts/orders_20200101.json signal_date=['2020-01-01'] 與今日 2026-09-07 不符（mtime 最新 ≠ 內容最新），略過
STALE_JSON_TO_HTML_FALLBACK [{'ticker': '2059', 'entry': 100.0, 'tp': 120.0, 'sl': 80.0, 'max_hold_days': 20}]
```

新增 freshness 測試只測下層函式，未測實際入口。另 `:199–200` 僅要求日期集合包含 today：同檔一筆 today、一筆舊日期會整批放行，全部缺日期也不拒絕。建議區分「無來源」與「拒絕來源」，拒絕時停止 fallback，並逐單驗證日期；若保留 HTML 入口，亦須驗證來源日期。

### F4 — P2：runner 失敗回報成功，且時區契約未完成

`run_mr20.sh:35/:67` 使用未指定 TZ 的主機日期；沒有固定 Asia/Taipei。這在日期跨台北午夜時會不同於 Python 的台北預設日期，日曆修正不能代替時區修正。

`:45–48` 將所有 session helper 非零退出都當非交易日略過，包含 Python／import／日曆錯誤；`:81–86` 將產單失敗或缺檔轉 exit 0；`:115–120` 將 freshness 失敗轉 exit 0。原先 set -e 的不可達分支已修，但失敗現在真的會走到成功退出；外部依退出碼監控將看不見，而且產單失败會連持倉收盤結算都跳過。

建議固定台北日期、區分非交易日與執行錯誤，必要跳失敗回傳非零。新增 mode 也要求既有無參數呼叫遷移，不能只靠檔首註解證明已部署。

### F5 — P2：自動搜尋對合法舊格式空單產生新誤判

`independent_sim.py:462–474/:501–509` 需要訂單或 diagnostic 內有 signal_date。合法既有格式 `{"orders": []}` 即使放在正確當日檔名，`resolve_orders_file(...)` 仍回傳 None。本次臨時目錄實測：

```text
EMPTY_AUTO_RESOLUTION None
```

原版用檔名可找到這種檔案，而 `load_orders` 和新增 smoke 的顯式空檔路徑仍接受它；同一空單現在會因自動／顯式入口不同而被標 gap／成功。建議建立清楚的空單日期契約並保留必要相容性，增加自動搜尋空單測試。

## 3. TDD 紅→綠可信度

**PARTIAL。** Git 中測試與實作同 commit，不能證明作者實際先寫測試並跑紅；本次只能獨立驗證「回歸測試是否能區分修復前後」。

| Commit | 證據 | 判定 |
|---|---|---|
| `a336c1c` | 將目前零曝險測試放到 `16e8320` 臨時快照：`3231` 實際開出 134 股，assertNotIn 失敗；HEAD 通過。不是 import/signature 假紅。 | PASS：行為回歸有效 |
| `d431637` | commit 沒新增 runner 測試；本次 bash 語法檢查不能證明日期、失敗碼或三跳部署正確。 | PARTIAL：無紅→綠證據 |
| `3103b6d` | 新增 smoke 在該 commit 快照已 `2 passed`；當時尚未修規劃狀態機與 as_of。這證明測到原本可走通的兩跳 happy path，無法拿它證明零交易根因已修。 | PARTIAL |
| `28ee4ac` | 顯式缺檔測試放到 parent `3103b6d`：`DID NOT RAISE FileNotFoundError`，HEAD 綠。內容 resolver 單測有用，但 `tests/test_orders_resolution.py:91–107` 明確把錯誤的「缺檔仍完成」寫成預期；`:109–126` mock 掉 resolver，不能證明任意名稱實際會被找到（真實 glob 仍限 pattern）。 | PARTIAL |
| `a63681c` | 顯式舊單測試放到 parent `28ee4ac`：`DID NOT RAISE ValueError` 且規劃 1 筆，HEAD 綠且驗證未落地。paper 僅測下層 freshness，漏上層 fallback。`plan_orders` 測試要求重蓋舊日期，並不能證明舊 candidate 被拒絕。 | PARTIAL：sim 核心有效、paper 不完整 |

`plan_orders` 改寫 candidate 日期不是獨立拒絕機制；目前正常 close 路徑由 `load_orders(as_of=...)` 提供拒絕保障，因此不把直接呼叫 helper 的測試當成正式管線已出現另一個事故。

## 4. 約束及 §3.1 三項遺漏

| 項目 | 判定 | 說明 |
|---|---|---|
| 五 commits 未碰 strategy/* 參數、eval_trader.py、event_backtest.py | PASS | `git diff --exit-code 16e8320 a63681c -- strategy/ eval_trader.py event_backtest.py` 無輸出、exit 0；整個 strategy/ 都未變。 |
| 修改範圍 | PASS | 僅 runner／alias、sim、paper、監控及相關測試，共 10 檔；沒有藉修管線調 RSI、資金、持倉配置。測試 fixture 的配置不等於策略參數變動。 |
| §3.1-1 原 :1516/:1519 | PARTIAL | 已加內容 signal_date 搜尋；晚到／改名檔只有在指定正確規劃日、未被 processed 封住且符合 glob 時能接入。缺檔補跑與正常空單驗收仍失敗。 |
| §3.1-2 event_backtest 前視 | FAIL（既有、留待第六步） | `strategy/event_backtest.py:632` 當日 close 估值仍流向 `:637–640/:700` 回撤 gating、`:1011–1016` heat 撤單、`:784` hedge P&L、`:1085/:1090/:1092` sizing；heat 分子 `:1008` 也直接使用當日 close。未新增固定 open、擾動 close、比對成交 ticker 集合及股數的驗收。 |
| §3.1-3 三跳端到端與可觀測性 | PARTIAL | 有兩跳落地 smoke 與缺 run_id 檢查；產單、交付、排程、planning gap 告警尚未完成，見 F1/F2。 |

第六步前視尚未修，符合本次禁止修改 event_backtest 的範圍限制，**不因此扣前五步越界分，也不要求本輪直接修改它**；但它仍是明確待辦，不能宣稱全部 Opus 遺漏已補齊，亦不能據受污染回測數字證明策略有效。

## 5. 本次實際驗證輸出及限制

以下皆設定 `PYTHONDONTWRITEBYTECODE=1`，pytest 使用 `-p no:cacheprovider`：

```text
python -m pytest -q -p no:cacheprovider test_paper_tracker_edge_cases.py tests/test_orders_resolution.py tests/test_as_of_alignment.py tests/test_check_processed_runs.py tests/test_e2e_mr20_smoke.py
43 passed in 1.29s
exit 0

python -m pytest -q -p no:cacheprovider tests/test_independent_sim.py tests/test_independent_sim_strategy_switch.py tests/test_paper_tracker_turnover.py tests/test_mr20_strategy.py tests/test_ai_report_orders.py
102 passed in 8.55s
exit 0

bash -n run_mr20.sh run_mr20_full.sh
無輸出，exit 0

git diff --exit-code 16e8320 a63681c -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0
```

反向測試在臨時 `git archive` 快照執行，沒有 checkout／改寫工作樹：

```text
16e8320 + 新零曝險單測：1 failed in 0.88s，exit 1（預期紅）
28ee4ac + 新顯式過期單測：1 failed in 1.07s，exit 1（預期紅）
3103b6d + 新顯式缺檔單測：1 failed in 1.30s，exit 1（預期紅）
3103b6d 原始 smoke：2 passed in 1.06s，exit 0
```

另以臨時帳戶／訂單完成 F1、F3、F5 的行為重現，腳本 exit 0（成功重現缺陷，不代表驗收通過）。共 145 個 HEAD 相關測試通過。未跑整個 repository 全測、build 或全域 lint，未查 host cron、未連網產單、未改正式帳本、未重算 event_backtest/eval 績效；不把這些未執行項目報為成功。未建立 commit。

放行前至少需補齊：缺單可重試及 gap 告警、包含真實產單的三跳與交付證據、paper 上層拒絕過期來源、runner 台北時區及失敗碼。修復後再依同一門檻複驗。

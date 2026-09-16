# Codex R4 補修複審 — 2026-09-07

**總判定：PARTIAL，8.25 / 10；未達 ≥8.5 放行門檻，不放行。**

對照 `docs/REVIEW-codex-r3-20260907.md` **第 2 節 F1–F4**，審查 `eaa379d`、`9189c90`、`661cf70`、`2b5feee`；基準 `c9db230`，HEAD `2b5feee0542a51a5745c779ca6d41fcabe545404`。行號均指 HEAD。本輪只新增本文件，不修改程式碼或測試，不建立 commit。

## 1. F1–F4 驗收

| 項目／commit | 判定 | 評分 /10 | 證據與剩餘界線 |
|---|---|---:|---|
| F1 逐單日期驗證／`eaa379d` | PASS | 9.0 | `paper_tracker.py:224–227` 對每筆 buy 的 `signal_date` 比對 today，拒絕過期及缺日期，保留今日訂單。新增兩測試有效；本輪額外呼叫真正上層 `extract_signals_from_report()`，今日／過期／缺日期三筆只輸出今日一筆。 |
| F2 JSON 損壞不回退／`9189c90` | PASS | 9.0 | `paper_tracker.py:200–205` 讀取或 JSON 解析失敗回傳 `unreadable`；`:288–290` 不進 HTML。`:298–303` 驗證 HTML 報表日期，缺日期／過期拒絕，今日接受；格式與 `ai_report.py:1391` 相符。通過上層拒絕與接受測試。此 PASS 指 r3 的損壞 JSON fallback 漏洞，不代表完整 JSON schema 驗證。 |
| F3 gap 訊息／`661cf70` | PASS | 9.0 | `independent_sim.py:1669–1681` 依 gap 是否仍存在選擇訊息。合法零單清 gap 並印 resolved；來源仍缺維持 unresolved。新增空單補跑測試在 parent 真紅、HEAD 綠。未更動結算與規劃邏輯。 |
| F4 runner 與上線條件／`2b5feee` | PARTIAL | 6.0 | 台北日期、正常曆表例外 exit 2 分流、產單／freshness 失敗非零退出、成功 close 後監控接線均有實作與測試。其他非零 session exit 仍假綠；失敗仍略過結算及監控；交付與自動空單搜尋條件未修。見下節。 |

四項等權平均：`(9 + 9 + 9 + 6) / 4 = 8.25`。F4 按 r3 原文完整條件評估，不縮成 commit 標題列出的子項。

## 2. 殘留與新問題

### R4-1 — P2：session helper 非預期錯誤仍被當休市（F4 殘留）

`run_mr20.sh:52–60` 只把 exit 2 當錯誤，其餘非零一律休市、exit 0。helper 被終止（例如 exit 137）仍隱藏失敗；Python 啟動／匯入階段在 CLI 的 try 之前失敗並 exit 1，也無法與休市區分。原本吞錯的缺口只部分收斂，並非所有錯誤均已修掉。

本輪以臨時 `$PYTHON` stub 驅動真實 runner 的 open 分支；stub 只輸出台北日期及指定 session exit，不執行開盤：

```text
SESSION_PROBE 1 runner_exit 0 holiday True
SESSION_PROBE 2 runner_exit 1 holiday False
SESSION_PROBE 3 runner_exit 0 holiday True
SESSION_PROBE 137 runner_exit 0 holiday True
```

至少應僅對明確的休市結果略過，其餘失敗非零退出；若需涵蓋 Python 啟動／匯入失敗，休市還需可辨認的結果協定，不能只依通用 exit 1。

### R4-2 — P1：產單失敗仍阻斷既有部位結算（F4 殘留）

`run_mr20.sh:93–99`、`:127–133` 在產單或 freshness 失敗時直接退出；`:137` 的 close-and-plan 與 `:150` 的每日監控都不執行。改 exit 1 確實改善失敗可見性，但當日 SL／TP／TIME 結算、equity 更新仍被訊號產生綁住，亦不建立此次規劃 gap。新增測試只要求非零退出，未驗證結算仍完成。

應保留拒絕不可信新單的邊界，同時讓既有部位結算與缺口記錄完成，再回報失敗。這是 r3 明列的未修條件，不是本輪新增交易回歸。

### R4-3 — F4 交付與自動空單搜尋仍缺證據

- `.github/workflows/update_ai_report.yml:135/212` 仍為 `git add -u artifacts`；`.gitignore:175` 仍忽略頂層 `artifacts/*.json`。四 commits 未提供新增訂單的替代交付設定。
- r3 F4 引用的 r2 F5：無日期 `{"orders": []}` 自動搜尋相容性未修改；F3 測試使用顯式路徑，不能替代自動搜尋驗收。
- repo 內成功 close 的監控呼叫已接上，可確認程式接線；未讀 host crontab、未驗跨主機交付，因此不宣稱已部署或 host 必定未排程。

### R4-4 — P2：新增 scheduler 測試依賴正式 checkout 路徑（新測試缺陷）

`tests/test_run_mr20_scheduler.py:174–194` 把 stub 訂單寫至測試自身 `REPO_ROOT`，但 `run_mr20.sh:22` 固定 `cd /root/work/tw_stocker`。其他 checkout 會在不同目錄寫入／讀取訂單，正常流程測試失敗；沒有該絕對目錄的環境更會提前失敗。硬編碼 runner 是既有問題，本輪新增測試將它引入測試套件的可攜性要求。

本輪把未修改的 HEAD 用 `git archive` 展開到暫存目錄，執行：

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_run_mr20_scheduler.py -k healthy_close_run_still_exits_zero
AssertionError: assert 1 == 0
stderr='❌ (No orders generated or strategy failed — skipping close-and-plan)'
1 failed, 14 deselected in 1.09s
exit 1
```

測試應在隔離目錄統一 runner 的工作目錄與 stub 的輸出位置。目前本機通過不代表一般 CI／worktree 可通過。測試也共用固定 `20990105` 檔名；本輪串行執行已清除測試暫存訂單，未留下產物。

### r3 F5 邊界

本輪使用者指定 F1–F4，**未將 r3 F5 加入四項計分，也未把它算成本輪新 bug**。但 `independent_sim.py:1657–1658` 的歷史 gap 補跑仍未檢查目標 open 是否已完成，四 commits 沒有加入該防護。F1–F3 PASS 不代表 r3 全部風險已關閉。

## 3. 範圍與 TDD 可信度

**修改範圍：PASS。** 四 commits 僅改六檔：`paper_tracker.py`、`independent_sim.py`、`run_mr20.sh` 及三個相關測試檔。新增 strict helper／CLI 屬 runner 分流所需；HTML 日期驗證屬 F2。未改策略、回測或參數。未發現本輪新引入的交易行為 bug；已確認的新問題為 scheduler 測試可攜性。

**TDD：PARTIAL。** 三項回歸測試辨識力可信；四 commits 均同時包含實作與測試，沒有足以證明作者先寫測試、先跑紅的歷史紀錄。本輪反向驗證只證明測試能辨識差異，不冒充作者的 TDD 時序。

方法：在各 commit 的 parent `git archive` 暫存快照，覆入該 commit 的測試檔，使用 `python -m pytest -q -p no:cacheprovider <file> -k <selection>`；不 checkout 或修改工作樹。以下為實際輸出：

| commit／selection | parent + 新測試 | 證據解讀 |
|---|---|---|
| `eaa379d`／`TestExtractSignalsFromOrdersMixedDates` | `2 failed, 28 deselected in 1.16s`，exit 1 | 原版輸出兩筆，期望一筆；兩個真實行為紅燈。 |
| `9189c90`／`TestExtractSignalsFromReportStaleFallbackBoundary` | `3 failed, 4 passed, 27 deselected in 1.08s`，exit 1 | 損壞 JSON、無日期 HTML、舊 HTML 均錯誤輸出買單；今日 HTML 接受仍通過。 |
| `661cf70`／`legitimate_zero_signal_backfill` | `1 failed, 11 deselected in 1.53s`，exit 1 | gap 已清但輸出含 `still unresolved`；精準辨識原問題。 |
| `2b5feee`／`TestIsSessionCliExitCodeContract` | `2 failed, 1 passed, 12 deselected in 3.37s`，exit 1 | parent 無 `is-session`，正常日／休市皆 argparse exit 2 而失敗；malformed-date 案例同樣因未知命令 exit 2 而通過，不能把此通過視為例外分流證據。 |

HEAD 上述測試皆綠，包含真正 CLI 的交易日／休市／錯誤測試。未宣稱 scheduler shell 的所有新增測試均已在 parent 完成可信反向驗證：parent 尚不支援 `$PYTHON` 注入，且存在固定 checkout 路徑依賴。

scheduler 測試仍有以下證據限制：日期測試 `:198–210` 只排除 host `date` 呼叫，不驗證午夜跨時區；正常監控測試 `:249–252` 只看提示文字，但另有 monitor exit 1 使 runner 失敗的測試補強實際接線。產單、close、open 與監控多用 stub，不能宣稱完整排程端到端驗收。

## 4. 本輪驗證紀錄

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider test_paper_tracker_edge_cases.py tests/test_orders_resolution.py tests/test_as_of_alignment.py tests/test_check_processed_runs.py tests/test_e2e_mr20_smoke.py tests/test_independent_sim.py tests/test_independent_sim_strategy_switch.py tests/test_paper_tracker_turnover.py tests/test_mr20_strategy.py tests/test_ai_report_orders.py tests/test_run_mr20_scheduler.py
179 passed in 19.47s
exit 0

bash -n run_mr20.sh run_mr20_full.sh
無輸出，exit 0

git diff --check c9db230 HEAD
無輸出，exit 0

git diff --exit-code c9db230 HEAD -- strategy/ eval_trader.py event_backtest.py .github/workflows/update_ai_report.yml .gitignore
無輸出，exit 0

上層混合日期臨時資料探針：
UPPER_MIXED [('2330', '2026-09-07')]
assert 僅保留 2330，exit 0
```

另依工作指示從專案根目錄執行 `codex exec --skip-git-repo-check`，指定唯讀 `git diff --check c9db230 2b5feee`，回報無輸出、exit 0。此項只算 diff 格式檢查，不冒稱額外完整模型審查。

未執行全 repo 測試、build 或全域 lint；未使用正式行情產單、操作正式帳本或發送通知。測試所需暫存資料已清理，保留開始前既有未提交變更。179 項通過不抵銷已重現的 F4 假綠與測試可攜性失敗；本輪不放行。

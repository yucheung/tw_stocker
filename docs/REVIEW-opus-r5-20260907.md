# Opus R5 補修複審 — 2026-09-07

**總判定：PARTIAL，8.13 / 10；未達 ≥8.5 放行門檻，不放行。**

對照 `docs/REVIEW-codex-r4-20260907.md` **第 2 節 R4-1～R4-4**，審查 `cf9c8db`、`80c4c9f`、`f3310a7`、`af9402f`。基準 `2b5feee`，HEAD `af9402f3c2f822add0f6034c69692b68e26f6402`。行號均指 HEAD。本輪只新增本文件，不修改程式碼或測試，不建立 commit。

## 1. R4-1～R4-4 驗收

| 項目／commit | 判定 | 評分 /10 | 證據與剩餘界線 |
|---|---|---:|---|
| R4-1 session 休市協定／`80c4c9f` | PASS | 9.0 | `independent_sim.py:1886–1895` 在 0／1 兩種正常結果印 `SESSION_RESULT:{TRADING_DAY,HOLIDAY}:<date>`；`run_mr20.sh:63` 只在 `rc==1` 且輸出符合 `^SESSION_RESULT:HOLIDAY:` 時 exit 0，`:68–73` 其餘非零一律 exit 1。真實 CLI 與 runner 探針均重現三態，r4 記錄的 137 假綠已消失。 |
| R4-2 結算先行／`f3310a7` | PASS | 9.0 | `run_mr20.sh:110–151` 以 `FAILED` 旗標取代提前 exit，失敗時清空 `CLOSE_PLAN_ORDERS_ARGS` 而非跳過；`:155–158` close-and-plan 照常執行，`:161` status、`:168` 監控照常，`:173–175` 最後才非零退出。庫側 auto-discovery 落空仍完成 settlement／記 gap 由既有 `tests/test_orders_resolution.py:112–128` 覆蓋。 |
| R4-3 交付＋無日期空單／`af9402f` | **PARTIAL** | **5.0** | 兩個半邊都有問題：交付端加在一個從不產生 mr20 訂單的 workflow；空單相容端的判定函式實際不檢查「無日期」，把**過期日期的空單**一併放行，並實測造成 planning gap 漏記。見 §2.1、§2.2。 |
| R4-4 去硬編碼／`cf9c8db` | PASS | 9.5 | `run_mr20.sh:26–28` 改用 `BASH_SOURCE` 推導 `SCRIPT_DIR`；`tests/test_run_mr20_scheduler.py:152–189` 改在 tmp workdir 複製腳本、預建 `artifacts/mr20`。r4 用來重現失敗的同一手法（`git archive` 到暫存目錄再跑）本輪 22 passed，且真實 checkout 未殘留 2099 測試產物。 |

四項等權平均：`(9.0 + 9.0 + 5.0 + 9.5) / 4 = 8.125`，記為 8.13。

## 2. 新問題

### 2.1 — P2（新引入的行為回歸）：`_is_dateless_empty_orders` 名不符實，過期空單被當成當日合法來源

`independent_sim.py:485–498` 的函式名與 docstring 都寫「no signal_date anywhere in it」，但 `:497` 的實作只判斷 `data.get("orders") == []`，完全沒有檢查日期欄位。因此只要慣例檔名存在，內容帶著**明確不同日期**的空單也會走 `:530–531` 的放行分支。

臨時目錄實測（`resolve_orders_file("mr20", "2026-09-02", orders_dir=tmp)`，檔名固定 `orders_mr20_20260902.json`）：

```text
STALE_DATED_EMPTY    -> /tmp/tmpbf1_7q6a/orders_mr20_20260902.json
DATELESS_EMPTY       -> /tmp/tmpbf1_7q6a/orders_mr20_20260902.json
ORDERS_NULL          -> None
WRONG_EXEC_TODAY_SIG -> /tmp/tmpbf1_7q6a/orders_mr20_20260902.json
CORRUPT              -> None
```

第一列是規格外的放行：`{"orders": [], "diagnostic": {"signal_date": "2026-08-14", ...}}`。commit message 只承諾「無日期空單」相容，`tests/test_orders_resolution.py:72–82` 也只測無日期那一種，過期日期空單無測試覆蓋。

更進一步用真實 `run_close_and_plan()`（`fetch_market_bars`／`fetch_benchmark_close` mock，其餘為真）在暫存資料夾比對 HEAD 與其 parent `f3310a7`：

```text
HEAD (af9402f):
  planning_gaps  = None
  processed_runs = ['close-and-plan:2026-09-02']
  equity_points  = 1

parent (f3310a7):
  ⚠️ [mr20] 找不到 signal_date=2026-09-02 的訂單檔（自動搜尋未命中），已記錄為 planning_gaps
  planning_gaps = ['2026-09-02']
```

同一個過期空單檔，修補前記為 gap、修補後被當成「合法零訊號日」直接結案。`check_processed_runs.py` 依賴 `planning_gaps` 告警，因此這一天的上游斷鏈不會再被每日監控攔到。

**與 R4-2 的交互**：`run_mr20.sh:105–109` 的註解宣稱 freshness 失敗時「找不到當日訊號檔會記錄 planning_gaps」。若被拒絕的那個檔恰好是空單且位於慣例檔名，close-and-plan 的 auto-discovery 會把 freshness gate 剛剛拒絕的同一個檔重新收下，gap 不會被記。runner 仍因 `FAILED=1` 非零退出（失敗仍可見），但狀態機留下的是「已解決」而非「有缺口」。

**交易面影響有限**：`load_orders`（`independent_sim.py:445–446`）對空 orders 直接回 `[]`，不會規劃任何部位，因此不會下錯單。損失的是監控訊號，不是資金安全。定為 P2。

### 2.2 — P1（既有 R4-3 交付缺口未實質關閉）：修補加在不會產生訂單的 workflow 上

`.github/workflows/update_ai_report.yml:140/219` 新增 `git add artifacts/mr20 || true`。`-u` 只更新已追蹤檔案這個診斷正確，`.gitignore:174–176` 的 `artifacts/*.json` 也確實不涵蓋 `artifacts/mr20/`（`git check-ignore` 無輸出）。但這個 workflow 不是產生者：

```text
$ ls .github/workflows/
update_ai_report.yml            （全repo唯一 workflow）

$ grep -rn "run_mr20\|mr20_strategy\|independent_sim" .github/workflows/
（無輸出）

$ grep -n "mr20" .github/workflows/update_ai_report.yml
137,138,140,219                 （全部都是本次新增的那兩行與其註解）

.github/workflows/update_ai_report.yml:47  runs-on: ubuntu-latest
.github/workflows/update_ai_report.yml:163 runs-on: ubuntu-latest
```

兩個 job 都跑在 GitHub 託管的 ephemeral runner 上、每次全新 checkout，工作流程中沒有任何一步呼叫 `run_mr20.sh` 或 `strategy.mr20_strategy`。因此 CI 端的 `artifacts/mr20` 只會有已 commit 的舊檔，新增的 `git add` 在 CI 永遠沒有新檔可加。

真正的產生者是 cron 主機上的 `run_mr20.sh`（`:92` 寫入 `artifacts/mr20/orders_mr20_${D_COMPACT}.json`），而該腳本完全沒有 git 動作：

```text
$ grep -n "git " run_mr20.sh run_mr20_full.sh
NONE
```

現況佐證缺口仍開著：

```text
$ git ls-files artifacts/mr20          → 20260818 … 20260826，共 7 檔（皆為早期人工 commit）
$ git status --porcelain artifacts/mr20 → ?? 20260827 / 0828 / 0831 / 0901 / 0902 / 0903 / 0907
```

8/27 之後每一個排程產出的訂單檔都仍是 untracked，本次四 commits 之後也不會改變。

`tests/test_workflow_artifact_delivery.py:31–39` 只斷言 YAML 文字含有 `git add artifacts/mr20`，不驗證任何交付行為，因此這個測試在上述情況下永遠是綠的。

此項為 r4 R4-3 既列缺口，非本輪新增的交易回歸，但「已修」不成立。仍未讀 host crontab、未驗跨主機交付，不宣稱主機端排程狀態。

### 2.3 — P3（可攜性註記）：空陣列展開需要 bash ≥ 4.4

`run_mr20.sh:157` 在 `set -u` 下展開 `"${CLOSE_PLAN_ORDERS_ARGS[@]}"`，FAILED 分支會是空陣列。bash 4.4 起才把空陣列展開視為已定義；4.3 以前會以 unbound variable 中止。本機 `GNU bash 5.3.9`、GitHub runner 亦為 5.x，實務風險低，僅記錄。

### 2.4 — P3（記錄降級）：交易日路徑的 session 輸出被吞掉

`run_mr20.sh:59` 改為 `SESSION_OUT=$(... 2>&1)`，正常交易日不再把 `SESSION_RESULT:TRADING_DAY:` 印到排程 log（只有失敗分支 `:71` 回放）。不影響判斷，只影響事後追查。

### 2.5 — 未涵蓋範圍

r3 F5（`independent_sim.py` 歷史 gap 補跑未檢查目標 open 是否已完成）本輪四 commits 同樣未觸及，依使用者指定的 R4-1～R4-4 範圍不計分，亦不算本輪新 bug。R4-1～R4-2、R4-4 PASS 不代表 r2／r3 全部風險已關閉。

**本輪未發現新的 P0。** 最嚴重的新問題（2.1）不會導致錯誤下單，只會使監控漏警。

## 3. 修改範圍

**PASS。** `git diff --name-only 2b5feee HEAD` 僅六檔：

```text
.github/workflows/update_ai_report.yml
independent_sim.py
run_mr20.sh
tests/test_orders_resolution.py
tests/test_run_mr20_scheduler.py
tests/test_workflow_artifact_delivery.py
```

```text
git diff --exit-code 2b5feee HEAD -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0
```

`strategy/*`、`eval_trader.py`、`event_backtest.py` 確認零變動。`independent_sim.py` 的兩處改動（`is-session` 標記、`resolve_orders_file` 快取路徑）都在對應項目範圍內；未見與四項無關的順手改動。

## 4. TDD 可信度

**PARTIAL（較 r4 有明顯改善）。** 四 commits 仍都是實作與測試同一 commit，git 歷史無法證明作者先寫測試並跑紅。本輪對四項全部完成反向驗證：把每個 commit 的測試檔覆蓋到它自己的 parent 快照（`git archive` 到暫存目錄，不 checkout、不動工作樹）執行。r4 當時無法對 scheduler shell 測試做這件事（parent 不支援 `$PYTHON` 注入、又有固定 checkout 路徑），本輪四項都有辨識力證據：

| commit／parent／selection | parent + 新測試 | 證據解讀 |
|---|---|---|
| `cf9c8db` / `2b5feee` / `healthy_close_run_still_exits_zero or freshness` | `1 failed, 1 passed, 13 deselected`，exit 1 | 隔離 workdir fixture 打在硬編碼 `cd` 的 parent runner 上：stub 訂單寫 tmp、runner 讀真實 checkout，`stderr='❌ (No orders generated...)'`、returncode 1。精準重現 r4 §R4-4 的失敗。 |
| `80c4c9f` / `cf9c8db` / session 四例 | `4 failed, 1 passed, 12 deselected`，exit 1 | parent CLI 無 `SESSION_RESULT` 標記，交易日／休市斷言雙紅；`killed_by_signal`(137) 與 `exit_one_without_holiday_marker` 均因 parent runner 誤判休市而紅。 |
| `f3310a7` / `80c4c9f` / `TestSettlementStillRunsOnOrderGenerationFailure` | `4 failed, 1 passed, 17 deselected`，exit 1 | parent 在產單／freshness 失敗時提前 exit，`Step 2: Close-and-Plan` 從未出現、`CLOSE_AND_PLAN_ORDERS_ARG_ABSENT` 亦不存在。第 5 例 healthy 路徑在 parent 本就綠，符合預期。 |
| `af9402f` / `f3310a7` / `dateless_empty` | `1 failed, 1 passed, 12 deselected`，exit 1 | `assert None == PosixPath('.../orders_mr20_20260902.json')`；glob fallback 的負向測試在 parent 本就綠，不能算辨識力。 |
| `af9402f` / `f3310a7` / workflow delivery | `1 failed, 1 passed`，exit 1 | 字串斷言紅→綠成立，但如 §2.2，它只驗 YAML 文字，不驗交付行為。 |

**測試品質的兩點保留**：
1. `tests/test_workflow_artifact_delivery.py` 是純文字存在性斷言，無法察覺「加在錯誤的 workflow」（§2.2）。紅→綠成立但驗收價值低。
2. R4-2 的五個新 shell 測試全部用 stub python，只斷言 `Step 2` 出現與 `--orders` 缺席，不驗證真正的 SL／TP／TIME 結算與 equity mark。庫側行為另有 `tests/test_orders_resolution.py:112–128` 覆蓋，兩者合起來足夠；但單看新增測試不能宣稱端到端結算已驗收。
3. §2.1 揭露的過期空單放行完全無測試覆蓋——新增測試只挑了實作正確的那一半輸入。

## 5. 本輪驗證紀錄

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  test_paper_tracker_edge_cases.py tests/test_orders_resolution.py tests/test_as_of_alignment.py \
  tests/test_check_processed_runs.py tests/test_e2e_mr20_smoke.py tests/test_independent_sim.py \
  tests/test_independent_sim_strategy_switch.py tests/test_paper_tracker_turnover.py \
  tests/test_mr20_strategy.py tests/test_ai_report_orders.py tests/test_run_mr20_scheduler.py \
  tests/test_workflow_artifact_delivery.py
190 passed in 20.92s
exit 0

R4-4 可攜性（git archive HEAD → 暫存目錄 → 在該目錄執行）：
cd /tmp/tmp.RrRQqHJeMj && python -m pytest -q -p no:cacheprovider tests/test_run_mr20_scheduler.py
22 passed in 11.13s
exit 0
（真實 checkout 未殘留 2099 測試產物：ls artifacts/mr20 | grep -c 2099 → 0）

真實 is-session CLI 三態：
2026-09-04 → SESSION_RESULT:TRADING_DAY:2026-09-04   rc=0
2026-09-06 → SESSION_RESULT:HOLIDAY:2026-09-06       rc=1
bogus-date → ERROR: trading-day lookup failed ...    rc=2

R4-1 runner 探針（暫存 workdir + $PYTHON stub，只驅動 session 分支）：
session_rc=1   marker=1 -> runner_exit=0 holiday_skip=yes
session_rc=1   marker=0 -> runner_exit=1 holiday_skip=no
session_rc=2   marker=0 -> runner_exit=1 holiday_skip=no
session_rc=137 marker=0 -> runner_exit=1 holiday_skip=no
（對照 r4 §R4-1：137 當時為 runner_exit 0 / holiday True）

bash -n run_mr20.sh
無輸出，exit 0

git diff --check 2b5feee HEAD
無輸出，exit 0

git diff --exit-code 2b5feee HEAD -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0

git check-ignore -v artifacts/mr20/orders_mr20_20260907.json artifacts/mr20/
無輸出（未被忽略）
```

未執行全 repo 測試、build 或全域 lint；未讀 host crontab、未連網產單、未操作正式帳本、未發送通知。所有探針都在 `mktemp -d` 暫存目錄進行並已刪除，真實 `artifacts/`、`independent_sim_data_mr20/` 未被寫入；開始前既有的未提交變更原樣保留。

## 6. 結論

- **R4-1 PASS、R4-2 PASS、R4-4 PASS** — 三項行號存在、語意相符，且都有獨立於作者測試的重現證據。
- **R4-3 PARTIAL** — 交付端修在無關的 CI job（§2.2），空單相容端的實作寬於宣稱範圍並造成 planning gap 漏記回歸（§2.1）。
- **總分 8.13 / 10，低於 8.5，不放行。**

放行前建議至少補齊：
1. 把 `_is_dateless_empty_orders` 收斂成真正的「無日期」判定（明確拒絕 `diagnostic.signal_date` 或 `orders[*].signal_date` 與 `today_str` 不符的空單），並補上過期空單的負向測試。
2. 為 cron 主機產出的 `artifacts/mr20/*.json` 建立真正的交付路徑（在 `run_mr20.sh` 內 commit/push，或改由 workflow 實際產單），並讓測試驗證交付行為而非 YAML 字串；同時處理現存 8/27–9/07 七個未追蹤檔。

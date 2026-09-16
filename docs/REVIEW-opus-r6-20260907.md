# Opus R6 補修複審 — 2026-09-07

**總判定：PARTIAL，7.00 / 10；未達 ≥8.5 放行門檻，不放行。**

對照 `docs/REVIEW-opus-r5-20260907.md` **§2.1／§2.2**，審查 `222a16d`（空單改判 `signal_date`）與 `36371cd`（交付改接 `run_mr20.sh`）。基準 `af9402f`，HEAD `36371cdc93d70f579c61fe29ee6977ddda9e9f3b`。行號均指 HEAD。本輪只新增本文件，不修改程式碼或測試，不建立 commit，所有探針都在 `tempfile` 暫存目錄進行並已刪除。

## 1. 三項驗收

| 項目 | 判定 | 評分 /10 |
|---|---|---:|
| 1. 過期空單真被拒且記 gap，並有測試 | **PASS** | 9.0 |
| 2. 交付接在真正產單路徑、下游撿得到 | **PARTIAL** | 6.5 |
| 3. 無新 bug／無範圍外改動 | **PARTIAL**（範圍 PASS，新 bug FAIL） | 5.5 |

三項等權平均：`(9.0 + 6.5 + 5.5) / 3 = 7.00`。

---

## 2. 項目 1 — 過期空單（`222a16d`）：PASS，9.0

### 2.1 修補正確且語意對得上真實產出

`independent_sim.py:506–509` 現在先確認 `orders == []`，再要求 `diagnostic.signal_date` 為空才回 `True`。`resolve_orders_file:535–541` 的順序是「先 `_orders_signal_date(cf) == today_str`，才試 dateless 寬鬆分支」，所以**當日**日期的空單仍走第一個分支放行，只有帶著**其他日期**的空單落到第二個分支並被拒。

這與真實產出形狀一致 — 真正的零訊號日檔案是帶 `diagnostic.signal_date` 的：

```text
artifacts/mr20/orders_mr20_20260904.json  n_orders=0
  diag_keys=['config','execution_date','funnel','saved_at','signal_date','strategy',...]
```

亦即 `20260904` 這個真實零訊號日走的是 `:538` 的日期相符分支，不依賴寬鬆分支，修補沒有誤傷合法零訊號日。

### 2.2 行為重測（重跑 r5 §2.1 的矩陣，並擴充邊界）

`resolve_orders_file("mr20", "2026-09-02", orders_dir=<tmp>)`，檔名固定 `orders_mr20_20260902.json`：

```text
STALE_DATED_EMPTY    -> REJECT     ← r5 §2.1 的回歸，已修
DATELESS_EMPTY       -> ACCEPT     ← 宣稱的相容範圍，符合
TODAY_DATED_EMPTY    -> ACCEPT     ← 合法零訊號日，未誤傷
EMPTY_STR_SIGDATE    -> ACCEPT
NULL_SIGDATE         -> ACCEPT
TOPLEVEL_DATE_ONLY   -> ACCEPT     ← 殘留窄縫，見 §2.4
DIAG_EXEC_ONLY       -> ACCEPT     ← 殘留窄縫，見 §2.4
DIAG_NOT_DICT        -> RAISE AttributeError（既有問題，見 §2.5）
ORDERS_NULL          -> REJECT
CORRUPT              -> REJECT
```

### 2.3 測試有辨識力

兩個新測試（`tests/test_orders_resolution.py:94–105` unit、`:145–169` `run_close_and_plan` 端到端）覆蓋到 parent `af9402f` 快照（`git archive` → 暫存目錄，不動工作樹）：

```text
=== parent af9402f | -k stale_dated_empty -> exit 1
FAILED ...TestResolveOrdersFile::test_fast_path_rejects_stale_dated_empty_orders_at_conventional_filename
FAILED ...TestCloseAndPlanMissingOrdersStateMachine::test_stale_dated_empty_orders_at_conventional_filename_records_gap
2 failed, 14 deselected
```

紅→綠成立，且端到端那一個直接斷言 `state["planning_gaps"] == ["2026-09-02"]`，不是字串代理。r5 §2.1 指出的「新增測試只挑了實作正確的那一半輸入」已補上。

### 2.4 殘留窄縫（P3，不扣為 FAIL）

判定只看 `diagnostic.signal_date` 一個欄位。空單若把日期放在別處 — `diagnostic.execution_date` / `diagnostic.saved_at` / 頂層 `as_of`、`generated_at` — 仍被當成 dateless 放行（上表 `DIAG_EXEC_ONLY`、`TOPLEVEL_DATE_ONLY`）。實務上 `strategy/mr20_strategy.py:533–551` 一律把 `signal_date` 與 `execution_date` 一起寫入 diagnostic，不會單獨產生這種形狀，因此不是可觸發的回歸。

另有一點值得記錄：`generate_mr20_orders` 唯一產生裸 `{"orders": []}` 的路徑是 `strategy/mr20_strategy.py:505–507`（`signal_date is None`），而 CLI 在 `:739` 永遠帶著已解析的 `signal_date` 呼叫，所以**這個寬鬆分支目前沒有任何正式產出者**。若要收得更乾淨，直接刪掉 dateless 例外會比維持它更安全；不影響本項判定。

### 2.5 既有（非本輪引入）：`diagnostic` 非 dict 會拋未捕捉例外

```text
File "independent_sim.py", line 538, in resolve_orders_file
    if _orders_signal_date(cf) == today_str:
File "independent_sim.py", line 484, in _orders_signal_date
    return diag.get("signal_date")
AttributeError: 'str' object has no attribute 'get'
```

崩點在 `_orders_signal_date`（`:484`），該函式在 `222a16d` 之前就存在且未被本次改動觸碰，`:538` 也早於新分支執行。屬既有健壯性缺口，不計入本輪新 bug；記錄供後續處理。

---

## 3. 項目 2 — 交付路徑（`36371cd`）：PARTIAL，6.5

### 3.1 「接在真正產單路徑」— 成立

`run_mr20.sh:174–210` 的 Step 4 就寫在 `$ORDERS`（`:92`）實際落地的同一支腳本裡，r5 §2.2 指出的「修在一個從不呼叫 `run_mr20.sh`／`mr20_strategy` 的 workflow」已不再是唯一路徑。實測在暫存 git checkout + bare origin 上跑 `run_mr20.sh close`，訂單檔確實 commit 並 push 到 `origin/main`，remote 內容與本地檔案逐字元相同。四個新測試對 parent `222a16d` 全紅：

```text
=== parent 222a16d | -k TestOrderArtifactDelivery -> exit 1
FAILED ...test_delivers_new_orders_file_to_git_remote_on_success
FAILED ...test_no_delivery_when_strategy_generation_failed
FAILED ...test_gracefully_skips_when_not_a_git_checkout
FAILED ...test_push_failure_still_fails_run_but_settlement_already_ran
4 failed, 22 deselected
```

且測試改用真的 git repo + bare remote 斷言 remote 樹內容，不是 r5 §2.2 批評的 YAML 字串存在性斷言。這一半是實質改善。

### 3.2 「下游撿得到」— 只成立到「進了 origin/main」為止

全 repo 只有 `independent_sim.py:77`（`orders_dir: artifacts/mr20`）讀這個目錄，而它就跑在同一台 cron 主機上；沒有任何 CI job 或其他程序消費 `artifacts/mr20`。所以本項的實際意義是「訂單檔進入版本控制、其他 clone 可取得」，這點成立；但「下游」目前是假想的，repo 內沒有可驗證的消費端。

### 3.3 未關閉：8/27–9/07 的積壓檔

Step 4 只處理當日的 `$ORDERS`。r5 放行建議第 2 點要求「同時處理現存 8/27–9/07 七個未追蹤檔」，本輪未做，而且結構上永遠不會被補上：

```text
$ git status --porcelain artifacts/mr20
?? artifacts/mr20/orders_mr20_20260827.json
?? ... 20260828 / 20260831 / 20260901 / 20260902 / 20260903 / 20260904 / 20260907
（現為 8 檔）
```

---

## 4. 項目 3 — 新 bug 與範圍：範圍 PASS，新 bug FAIL，5.5

### 4.1 範圍：PASS

```text
$ git diff --name-only af9402f HEAD
independent_sim.py
run_mr20.sh
tests/test_orders_resolution.py
tests/test_run_mr20_scheduler.py

$ git diff --exit-code af9402f HEAD -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0
```

四檔全部落在 §2.1／§2.2 範圍內，`strategy/*`、`eval_trader.py`、`event_backtest.py` 零變動，未見順手改動。

一個殘留物（非 bug）：`36371cd` 的 commit message 說 workflow 那條路「等於沒接」，但 `af9402f` 加的 `.github/workflows/update_ai_report.yml:140/219` 兩行 `git add artifacts/mr20 || true` 並未撤掉，`tests/test_workflow_artifact_delivery.py:35` 仍在斷言那段字串存在。兩行有 `|| true`、CI 也永遠沒有新檔可加，行為無害；但現在是一段作者自己已判定無效、卻被測試鎖住的死碼。

### 4.2 新 bug（全部在 Step 4 新程式碼內，全部實測重現）

#### F1 — P1：重試路徑的 `--depth=50` 會把 cron 主機的完整 checkout 變成 shallow repo

`run_mr20.sh:190`：

```bash
elif git fetch --no-tags --depth=50 origin main:refs/remotes/origin/main \
    && git rebase --autostash origin/main; then
```

這個 idiom 是從 `update_ai_report.yml:145/226` 抄來的。在 CI 上無害（每次全新 ephemeral checkout、本來就淺）；在**常駐的 cron 主機**上，`git fetch --depth=N` 會把原本完整的 repo 就地轉成 shallow。60 commit 的暫存 repo 實測：

```text
local history depth before: 60
shallow before: False
fetch rc: 0
shallow AFTER fetch --depth=50: True
rev-list count after: 50
```

真實 checkout 是 `git rev-list --count HEAD → 226`。只要發生過一次 push 競爭走進重試分支，本地歷史就被永久截斷到 50 個 commit 並產生 `.git/shallow`，需要 `git fetch --unshallow` 才能救回；blame／歷史查詢在那之後全部失真，部分 server 設定也會拒絕 shallow repo 的 push（`shallow update not allowed`）。

**這條重試路徑零測試覆蓋** — `test_push_failure_still_fails_run_but_settlement_already_ran` 直接 `shutil.rmtree(remote)`，`git fetch` 在第一步就失敗，`rebase`／二次 `push` 從未執行。修法是把 `--depth=50` 拿掉（或換成 `git fetch --no-tags origin main`）。

#### F2 — P2：freshness gate 拒絕的訂單，Step 4 照樣 push 上 origin/main

Step 4 的守門條件（`:181`）只有「檔案存在 + 在 git checkout 內」，完全不看 `FAILED`。訂單檔存在但 freshness 未過時（檔名是今天、內容 `signal_date` 是別天），腳本一邊在 stderr 說「拒絕信任」，一邊把同一個檔案發佈到共用 main：

```text
### SCENARIO A: freshness gate FAILS, orders file exists
  returncode: 1
  freshness : ['❌ Freshness gate FAILED: STALE: signal_date=2099-01-02 expected=2099-01-05',
               '(Refusing to trust these orders — settlement will still run, planning gap recorded)']
  delivery  : ['✅ Delivered artifacts/mr20/orders_mr20_20990105.json to origin/main']
  remote log: chore(mr20): deliver orders_mr20_20990105.json (2099-01-05) | seed
  remote has stale artifact: True
```

這直接違反 `:105–109` 自己寫的設計意圖，也正是整個 freshness gate 要防的「8/24 規劃 8/14 訊號」形狀。交易面影響有限：任何下游走 `resolve_orders_file` 都會因內容 `signal_date` 不符而拒收，走 `--orders` 明確路徑則由 `load_orders:454–457` 拋錯。損失的是「共用 main 上不該出現不可信 artifact」這條界線。定 P2。既有測試 `test_no_delivery_when_strategy_generation_failed` 只覆蓋「檔案根本不存在」，沒有覆蓋這一種。

#### F3 — P2：push 失敗後同日重跑會假綠 —「already delivered」+ exit 0，但檔案根本不在 origin

`:182` 用 `git status --porcelain -- "$ORDERS"`（工作區是否乾淨）當作「已交付」的判準，而 commit 在 push 之前就已經建立。push 失敗留下本地 commit → 工作區乾淨 → 下一次同日重跑走 `:206` 的 else 分支報「已交付」並 exit 0：

```text
### B1 first run, remote unreachable
  rc: 1 | ['❌ Failed to push ... — order artifact undelivered']
### B2 same-day re-run, remote back online
  rc: 0
  delivery: ['artifacts/mr20/orders_mr20_20990105.json already delivered (no working-tree changes)']
  artifact actually on origin/main: False
  local unpushed commits: chore(mr20): deliver orders_mr20_20990105.json (2099-01-05)
```

remote 已經恢復可用，重跑卻不重試，還回報成功。這正是 r5 §2.2 批評的假綠形狀換了個位置重現。判準應改成比對 `origin/main` 是否已含該 commit（例如 `git merge-base --is-ancestor HEAD origin/main`），而不是工作區乾淨與否。無測試覆蓋。

#### F4 — P2：交付會把該分支上所有未推送的無關 commit 一併發佈

`git push origin HEAD:main` 推的是整條分支，不是那一個 commit。暫存實測（本地先放一個無關 WIP commit）：

```text
remote log: chore(mr20): deliver orders_mr20_20990105.json | unrelated local WIP commit | upstream commit | seed
unrelated LOCAL_WIP pushed to origin: True
```

在純 cron 專用 checkout 上這無所謂；但本 checkout 現在就是 `git rev-list --left-right --count origin/main...HEAD → 0  23`。也就是說，這份合併之後的**第一次 close 排程執行，會順帶把這 23 個尚未推送的 commit 推上 origin/main**，包含本輪四個補修 commit 與更早的本地 commit。這是排程腳本的非預期外溢，使用者應該是明示決定要推、而不是由 cron 代為決定。

`git rebase --autostash origin/main`（`:191`）也是整條分支 rebase；`independent_sim_data_mr20/equity.csv`、`state.json` 這些**已追蹤且每次 close 都被改寫**的帳本檔會被 autostash 進出，遠端若同時動到同一批檔案，stash pop 衝突會把衝突標記留在活的帳本 CSV 裡。

### 4.3 其他觀察（P3，不計入 F 編號）

- **HTTPS 認證與 cron 掛死**：`origin` 是 `https://github.com/yucheung/tw_stocker.git`。Step 4 沒有設 `GIT_TERMINAL_PROMPT=0`、也沒有 `timeout`。cron 環境若沒有可用的 credential helper，`git push` 依環境不同可能直接失敗（尚可）或停在憑證提示（排程掛死）。建議加 `GIT_TERMINAL_PROMPT=0` 並包 `timeout`。
- **`git rev-parse --is-inside-work-tree` 會沿父目錄往上找**：若 checkout 本身不是 repo 但被放在某個 repo 底下，Step 4 會 commit 進錯的 repo。訊息 `:209` 寫「`$SCRIPT_DIR` is not a git checkout」與這個語意不完全相符。實務機率低。
- **`set -e` 下 Step 4 的中途失敗沒有訊息**：`git add`／`git commit`（`:183–185`）失敗會讓腳本立刻以 git 的退出碼中止，跳過 `:199–201` 的 ❌ 訊息與 `:212` 的統一收尾。退出碼仍非零，可見性略差。

---

## 5. 本輪驗證紀錄

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/
271 passed in 27.50s
exit 0

r5 同一組回歸集（12 檔）：
196 passed in 24.43s      （r5 當時 190 passed，+6 = 本輪兩 commit 的新測試數）
exit 0

直接受影響三檔：
tests/test_orders_resolution.py tests/test_run_mr20_scheduler.py tests/test_workflow_artifact_delivery.py
44 passed in 13.32s
exit 0

辨識力（新測試 → parent 快照，git archive 到暫存目錄）：
af9402f + stale_dated_empty        → 2 failed, 14 deselected, exit 1
222a16d + TestOrderArtifactDelivery → 4 failed, 22 deselected, exit 1

bash -n run_mr20.sh
無輸出，exit 0

git diff --name-only af9402f HEAD                → 4 檔（見 §4.1）
git diff --exit-code af9402f HEAD -- strategy/ eval_trader.py event_backtest.py
無輸出，exit 0

git rev-list --count HEAD                        → 226
git rev-list --left-right --count origin/main...HEAD → 0  23
git status --porcelain artifacts/mr20            → 8 個 ?? 未追蹤檔（8/27–9/07）

執行後檢查：HEAD 仍為 36371cd，工作樹與開始前逐項相同，
ls artifacts/mr20 | grep -c 2099 → 0（無測試殘留）
```

未執行 build 或全域 lint；未讀 host crontab、未連網產單、未操作正式帳本、未推送任何 ref、未發送通知。所有 git 探針都在 `tempfile` 建立的暫存 repo（含 bare origin）內進行並已刪除，真實 remote 從未被寫入。

---

## 6. 結論

- **項目 1（過期空單）PASS 9.0** — r5 §2.1 的回歸確實關閉，unit 與端到端測試都有辨識力，且未誤傷合法零訊號日。殘留只是不可觸發的窄縫。
- **項目 2（交付路徑）PARTIAL 6.5** — 交付確實改接到真正的產出者並實測 push 成功，這一半是實質修好；但 8/27–9/07 積壓檔未處理，且下游消費端在 repo 內不存在。
- **項目 3（新 bug／範圍）PARTIAL 5.5** — 改動範圍乾淨；但新程式碼帶進 1 個 P1（F1 shallow 化）與 3 個 P2（F2 發佈不可信訂單、F3 push 失敗後假綠、F4 外溢推送 23 個無關 commit），其中 F1／F3 完全無測試覆蓋。
- **總分 7.00 / 10，低於 8.5，不放行。**

放行前至少要處理（依嚴重度）：

1. **F1**：`run_mr20.sh:190` 移除 `--depth=50`，並補一個「origin 已前進 → 第一次 push 被拒 → 重試成功」的測試（現行四個測試都沒走到重試路徑）。
2. **F4**：把交付縮到只推該 artifact（例如在暫存分支上 commit 後 push，或先確認 `origin/main..HEAD` 只有這一個 commit 才 push），避免排程順手發佈 23 個本地 commit。
3. **F3**：「已交付」改用 `git merge-base --is-ancestor HEAD origin/main` 之類的遠端事實判定，並補「push 失敗 → 同日重跑會重試並成功」的測試。
4. **F2**：Step 4 加上 `[ "$FAILED" -eq 0 ]` 或等值的 freshness 條件，讓被 gate 拒絕的訂單不進 origin/main，並補對應測試。
5. **§3.3**：補交 8/27–9/07 這 8 個未追蹤訂單檔（一次性人工 commit 即可，Step 4 結構上不會回頭處理）。
6. 清掉 `.github/workflows/update_ai_report.yml:140/219` 與 `tests/test_workflow_artifact_delivery.py` 這段已失效的死碼／死斷言。

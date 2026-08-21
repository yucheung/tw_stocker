# VPS FastAPI 二審報告（P1 fix 驗證，REVIEW_vps_api_v2.md）

> 審查日期：2026-08-16
> 審查對象：`/root/work/tw_stocker/vps_api/`（commit `8e33ace`「P1 fix: VPS FastAPI 審查 5 個 P1 問題修復」）
> 對照基準：一審 `vps_api/REVIEW_vps_api.md`（7.5/10，5×P1）+ `references/vps-api-review-20260816.md`（修法處方）
> 驗證方式：靜態閱讀 × 全部修改處 + `py_compile` + 功能測試 harness（29 項斷言）+ subprocess 整合測試（SIGTERM/SIGKILL 雙路徑 A/B 對照）

---

## 0. 驗證執行記錄

| 驗證項 | 指令 / 方式 | 結果 |
|---|---|---|
| 語法 | `python3 -m py_compile vps_api/*.py` | ✅ 通過 |
| P1-1 功能 | `build_cli_args` True/False 兩向 + `_CLI_MAP` 17 flags 對 ai_report.py argparse 三方一致 | ✅ 5/5 PASS |
| P1-2 端點 | TestClient（AUTH_DISABLED=1）：sector_rotation_v2 / meal_money_v2 / 未知策略 / momentum_v85 | ✅ 4/4 PASS |
| P1-3 端點 | monkeypatch `_fetch_latest_prices`：capital 50k + MV 55k → 105,000 | ✅ 2/2 PASS |
| P1-4 單元 | floor 快照 → 舊檔排除 / 新檔選取 / 多檔取最新 / result.json 快照 | ✅ 6/6 PASS |
| P1-5 SIGTERM 路徑 | 假腳本 sleep 300 + 完整 stop()：終態 failed、無孤兒、tasks 清空、<15s 快速路徑 | ✅ 7/7 PASS |
| P1-5 SIGKILL 路徑 | 假腳本忽略 SIGTERM → 15s 逾時 → 強制 SIGKILL | ❌ **孤兒存活（見 §P1-5）** |
| P1-5 修復驗證 | 隔離副本套補強 patch 後重跑 SIGKILL 路徑 | ✅ 無孤兒 |
| 環境殘留 | `ps` 掃描 fake 程序 + `vps_api/jobs/` 內容 | ✅ 無殘留（僅 .gitkeep） |

功能測試總計 **29 項斷言：28 PASS / 1 FAIL**（FAIL 即 P1-5 SIGKILL 保險路徑的孤兒問題，詳下）。

---

## 1. 逐項判定

### P1-1：`rank_weight` 加入 `_CLI_MAP` — ✅ 正確

- `strategies.py:84` `_CLI_MAP` 已含 `"rank_weight": "--rank-weight"`；`build_cli_args` 既有 boolean 邏輯自動生效。
- 功能測試：`{'rank_weight': True}` → args 含 `--rank-weight`；`False` → 省略。✅
- 三方一致：`_CLI_MAP` 17 個 flags 全數存在於 `ai_report.py` argparse，`--rank-weight` 為 `action='store_true'` 且接線到 `rank_weighted=args.rank_weight`（ai_report.py:1800、2064）。✅
- `schema` 預設 `False` 與 ai_report 預設停用一致，無預設值漂移。

### P1-2：`create_job` 對空 schema 策略回 400 — ✅ 正確

- `backtest.py:531-535`：`meta.get("schema")` 為空 → `HTTPException(400, "策略 … 尚未開放（Phase 3 未開放…）")`。
- 端點實測：`sector_rotation_v2` / `meal_money_v2` → **400 含「Phase 3」**；未知策略 → 400；`momentum_v85`（唯一有 schema）→ **202** 且 worker 正常撿起進入 running。✅
- 採「空 schema 400」而非「依 script 組 cmd」，符合一審處方（「或 schema 空策略直接 400」）。registry `script` 欄位留待 Phase 3 接線，已無 mislabel 風險。
- 補充：400 在 `queue.submit` 之前，不會寫入任何 job 檔。✅

### P1-3：`/paper/live` 改為 `capital + total_mv` — ✅ 正確

- `paper.py:161-165`：`total_equity_est = round(capital + total_mv, 2)`，dead `invested_cost` 已刪除，註解說明正確（`capital` 是純現金，見 paper_tracker.py:387/:295/:446-449）。
- 功能測試（monkeypatch 即時報價）：capital 50,000 + 持倉 50 股 × 1,100 = 105,000，舊式會錯報 55,000。✅
- 單檔 `market_value` / `pnl` 計算亦正確；無持倉時直接回 `capital`（無 MV 可加，行為正確）。

### P1-4：`_extract_result` 加 mtime floor — ✅ 正確（含殘餘風險 ⚠️）

- `Job._metadata_floor_mtime`（repr=False、to_dict 白名單外 → 不洩漏）；`_run_job` 在組 cmd 前快照 `_current_metadata_mtime()`（backtest.py:314）；`_extract_result` 只接受 `st_mtime > floor` 的檔（:388-391），無符合者 warn 回 None；floor=0.0 向後相容。
- 功能測試：floor 快照正確；開始後無新檔 → None；新檔正確選取；**舊檔（floor 前）被排除**；多檔取最新；result.json 快照寫入 jobs/。✅
- **⚠️ 殘餘風險（屬一審已知的 Phase 2 範圍，非本次修法錯誤）**：ai_report.py:1581 把結果寫成 **固定檔名 `artifacts/metadata_{date}.json`**。兩個並行 job 若**同日期**（預設 end_date=今天 → 同檔名），後完成的 job 會覆寫前者的檔；且由於 floor 只保證「>開始時快照」，**後完成的 job 可能讀到並行 job 覆寫後的檔 → 結果仍可能互掛**。mtime floor 完整解決的是「殘留舊檔被誤取」與「不同日期並行」兩類，同日期並行（MAX_CONCURRENT=2 下的常態）仍受影響。
  - **建議**：Phase 2 做輸出目錄隔離（`artifacts/runs/<job_id>/`，一審已列）；過渡期可考慮 production 設 `BACKTEST_MAX_CONCURRENT=1`，或至少知道此殘餘風險存在。

### P1-5：`stop()` 追蹤 tasks + terminate — ⚠️ 主路徑正確，SIGKILL 保險路徑有缺陷（需修復）

**主路徑（子程序正常回應 SIGTERM）— ✅ 正確**：
- `_job_tasks` 追蹤（:303、:356）+ stop() 三階段（SIGTERM → `wait_for(gather, 15s)` → 逾時 SIGKILL → 殘留非終態標 failed）。
- 整合測試：submit → running → stop() → **status=failed 且不回歸 done**（terminate 後 returncode=-15 ≠ 0，狀態回歸 bug 已修）、`_job_tasks` 清空、`ps -p` 無孤兒、elapsed≈0s（快速路徑，未誤走逾時）。lifespan 關閉路徑（TestClient exit）同樣通過。✅

**SIGKILL 保險路徑（子程序忽略/拖延 SIGTERM）— ❌ 有缺陷，孤兒仍會產生**：

- 重現（假腳本 `signal.signal(SIGTERM, SIG_IGN)` + sleep 300）：stop() 走完 15s 逾時並印出「強制 SIGKILL」後，**子程序仍存活**（`ps` stat=S，非 zombie）。
- **根因**：`asyncio.wait_for`（3.11 實作 `_cancel_and_wait`）逾時時會 **cancel gather → cancel `_run_job` task**，且**等取消完全處理完才拋 TimeoutError**。被取消的 `_run_job` 在 `finally` 已把自己從 `_running` / `_job_tasks` pop 掉 → stop() 的 SIGKILL 迴圈（:178-183）掃 `_running` 時是**空的** → `job._proc.kill()` 從未被呼叫 → 忽略 SIGTERM 的子程序變成孤兒繼續跑（佔 RAM/CPU）。診斷 log 佐證：`_run_job` 的 CancelledError handler 沒執行、job 的 failed 是 stop() 收尾迴圈標的（error=「服務關閉（process 結束）」→ 實際上 handler 內 error 是「job 被取消」，見下）。
  - 修正前版 handler 標的 error 是「服務關閉（job 被取消）」；meta 檔確認 CancelledError handler 有跑，但 `_proc.kill()` 未執行（stop() 的 kill 迴圈找不到 proc）。Hmm—實測 meta error 為「服務關閉（job 被取消）」，代表 handler 有執行，但**原版 handler 只標 failed 不殺 proc**，殺戮任務完全指望 stop() 的 SIGKILL 迴圈——而那迴圈掃不到已被 pop 的 job。兩者疊加 = 孤兒。
- **影響範圍**：正常 ai_report.py（Python 預設 SIGTERM → kernel 立即終止）走主路徑不受影響；僅「忽略 SIGTERM 或 15s 內退不掉」的子程序會孤兒化——正是 SIGKILL 保險設計要擋的情境，所以是保險失效。
- **修復（已在隔離副本驗證）**：在 `_run_job` 的 `except asyncio.CancelledError` 內自行 kill + wait 回收子程序（任務自己擁有 proc，不受 stop() bookkeeping race 影響）：

```python
except asyncio.CancelledError:
    # 服務關閉中：先強制結束並回收子程序（P1-5 補強 — 若只靠 stop()
    # 的 SIGKILL 迴圈，任務被 wait_for 取消時已在 finally 移出
    # _running，stop() 掃不到 proc 可殺 → 忽略 SIGTERM 的子程序
    # 會變成孤兒）
    if job._proc is not None:
        try:
            job._proc.kill()
            await job._proc.wait()
        except ProcessLookupError:
            pass
    job.status = STATUS_FAILED
    job.error = "服務關閉（job 被取消）"
    raise
```

- A/B 對照（隔離副本、同一假腳本）：原版 stop() 後 pid 存活（stat=S）；套 patch 後 stop() 後 pid 消失（gone）、elapsed 仍 15s、status=failed。正常 SIGTERM 路徑不受影響（快速結束、無孤兒）。

---

## 2. 其他觀察（非本次 P1 範圍，供 backlog）

- P1-4 殘餘（同日期並行結果互掛）→ Phase 2 輸出目錄隔離，同 §P1-4。
- 一審 P2 清單仍有效（`_run_job` finally 先 push 後 wake 的空槽延遲、`_jobs` 無上限成長、stop() 串行 push、sync body 大小上限、雙 Service Token、schema↔_CLI_MAP↔argparse 三方一致性自動化測試）。
- `--rank-weight` 補上後，三方一致性檢查建議自動化（一審已列）。

---

## 3. 整體評價

**分數：8.0 / 10**

| 項目 | 判定 |
|---|---|
| P1-1 rank_weight | ✅ 正確 |
| P1-2 空 schema 400 | ✅ 正確 |
| P1-3 capital + total_mv | ✅ 正確 |
| P1-4 mtime floor | ✅ 正確（同日期並行殘餘風險列入 Phase 2） |
| P1-5 stop() 三階段 | ⚠️ 主路徑正確；**SIGKILL 保險路徑失效（孤兒），補強 patch 已驗證** |

**可否上線：可上線，但建議先套用 §P1-5 的補強 patch（約 10 行，已在隔離副本完整驗證）再部署。** 若接受風險先行上線，須在 runbook 註明「子程序忽略 SIGTERM 時可能孤兒化，systemd 層建議加 `TimeoutStopSec` + 啟動時清理殘留 ai_report 程序」。

理由：5 個 P1 中 4 個修法正確且經功能測試佐證；P1-5 的主路徑（真實 ai_report.py 的預設行為）正確、狀態回歸 bug 確實修掉；唯一缺陷在「最後一道保險」對忽略 SIGTERM 的子程序失效，屬邊緣但可重現的品質缺口。以使用者（車用電子 / ISO 21434 背景）的驗證標準，此缺口應在部署前關閉——補強 patch 已提供並驗證。

---

## 4. 附錄：測試 harness 位置

- `/tmp/vps_p2_review/test_p1_fixes.py`（29 項斷言主測試）
- `/tmp/vps_p2_review/diag_compare.py`（SIGTERM/SIGKILL A/B 對照，隔離 import）
- 測試皆以 env 重導（TW_STOCKER_ROOT / JOBS_DIR / AI_REPORT_SCRIPT）至 temp，**未污染 repo**；`vps_api/jobs/` 僅餘 `.gitkeep`。

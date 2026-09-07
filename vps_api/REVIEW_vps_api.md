# VPS FastAPI 骨架審查報告（REVIEW_vps_api.md）

> 審查日期：2026-08-16
> 審查對象：`/root/work/tw_stocker/vps_api/`（8 模組，共 1,461 行 Python，agy / Gemini 3.7 Flash 開發）
> 對照基準：`PLAN_web_ui_v2.md`（v2.0，2026-08-16）+ tw_stocker 核心（ai_report.py / paper_tracker.py，量化版 944b24fe 之後）
> 驗證方式：靜態閱讀 × 全部 8 檔 + 動態驗證（import / py_compile / TestClient 端點實測 / CLI flag 與 artifacts 結構交叉比對）

---

## 0. 驗證執行記錄

| 驗證項 | 指令 | 結果 |
|---|---|---|
| 語法 | `python3 -m py_compile vps_api/*.py` | ✅ 通過 |
| Import | `python3 -c "from vps_api.main import app; print('OK')"` | ✅ 通過 |
| /health | TestClient GET（AUTH_DISABLED=1） | ✅ 200，欄位齊全（disk/last_sync/memory/now/queue/status/tw_stocker_root/uptime_s/version） |
| /strategies | TestClient GET | ✅ 200，三策略註冊 |
| /strategies/{key} | TestClient GET | ✅ 200，schema 20 欄 |
| /paper/state | TestClient GET（本機無 paper_equity.json） | ✅ 404（行為正確） |
| /backtest/jobs | TestClient GET | ✅ 200，stats 正確 |
| 未知策略提交 | POST /backtest/jobs | ✅ 400 |
| Auth fail-closed | 無 token 設定 → GET /health | ✅ 503 |
| Auth 錯 token / 缺 header | GET /health | ✅ 401 |
| Auth 正確 token | GET /health | ✅ 200 |
| _CLI_MAP 16 flags | regex 比對 ai_report.py argparse | ✅ 全數存在，預設值一致 |
| paper_equity.json 結構 | 對照 paper_tracker.py 寫入端 | ✅ capital=現金、equity_curve 尾筆=總權益 |

---

## 1. 逐模組審查

### 1.1 main.py（FastAPI app + CORS + lifespan）— ✅ 正確

- `create_app()` 工廠 + 模組級 `app` singleton，uvicorn `vps_api.main:app --workers 1` 可直接啟動 ✅
- lifespan 順序正確：`ensure_dirs → sync.init → backtest.start`，關閉依序 `backtest.stop → sync.close` ✅
- CORS `allow_origins=["*"]` + `allow_credentials=False`（星號與 credentials 並存屬合法組合）；註解說明由隧道 + Service Token 保護，符合 PLAN §6.4（VPS 不綁公開 IP）✅

⚠️ 需注意：
- **P2**：`GET /` root 端點未掛 auth dependency（僅列端點清單與版本，無敏感資料，但違反「全端點 Service Token」的自我宣稱；`/docs` 同理，實際由隧道層 Access 擋住，可接受但建議記錄或補 dependency）。
- **P2**：`AUTH_DISABLED` 等設定在 import 時固定（module 常數），env 中途變更不生效 — 部署上沒問題，但測試/除錯時需注意（本次驗證即踩到）。

### 1.2 auth.py（Service Token 驗證）— ✅ 正確

- `secrets.compare_digest` 常數時間比較，ID 與 Secret 雙重驗證 ✅
- Token 未設定 → **fail-closed 503**（實測 ✅，與 docstring 宣稱一致）✅
- 缺 header / 錯 token → 401（實測 ✅）✅
- `AUTH_DISABLED=1` 逃生門預設關閉，僅供本機開發 ✅

⚠️ 需注意：
- **P2**：401 帶 `WWW-Authenticate: Bearer` 語意不精確（非 OAuth2 Bearer，是自訂 Service Token），無實質危害，可改 `Basic` 或省略。

### 1.3 health.py（GET /health）— ✅ 正確

- 欄位完整：status/version/uptime_s/now/queue/mem/disk/last_sync/tw_stocker_root，對應 PLAN P2-7 ✅
- `time.monotonic()` 計 uptime（不受 NTP 跳動影響）✅
- psutil / shutil.disk_usage 失敗時回傳 None 值而非崩潰（防禦式）✅

### 1.4 backtest.py（job 佇列 + subprocess + 優先序 + 狀態機）— ⚠️ 需注意（P1×3）

**✅ 正確部分**
- 佇列排序 `heapq (-priority, seq)` = (priority DESC, created_at ASC)，高優先插隊、不中斷執行中 job — 完全符合 PLAN P1-6 ✅
- 狀態機 queued → running → done|failed|cancelled，`TERMINAL_STATES` 定義清楚 ✅
- 並行上限 2 / 等候上限 5 / 滿回 429（3.7GB RAM 設計，符合 PLAN §5.3）✅
- per-job log（stdout+stderr 合流）+ jobs/<id>.json meta 持久化 + 啟動 recovery（非終態 → failed 並推送）✅
- cancel 語意正確：queued 直接取消、running 送 SIGTERM（ProcessLookupError 防護）✅
- heartbeat 30s 補推 push_dirty，Worker 端冪等 upsert 安全 ✅
- `_run_job` finally 先 `_running.pop` 再 push → 執行槽立即釋放 ✅
- CLI 組裝 `sys.executable`（沿用 venv python）✅

**❌ 有問題**
1. **P1（結果正確性）— 並行 job 結果交叉污染**：`_extract_result` 讀 `artifacts/metadata_*.json` 最新 mtime，2 個並行 job 同時完成時，可能把 A 的結果掛到 B 名下（D1 摘要錯誤）。程式碼註解已承認此限制（Phase 2 需輸出目錄隔離），屬**已知但未緩解**。建議：Phase 2 前至少限制並行=1，或記錄 job 開始前的 metadata mtime、只接受開始後產生的檔。
2. **P1（生命週期）— `stop()` 未終止子程序**：關閉時只把 running 標 failed，**不 terminate subprocess**；`_run_job` task 未追蹤，uvicorn 退出後 ai_report.py 可能成孤兒繼續跑（佔 RAM/CPU）。且子程序正常結束後 `_run_job` 會以 returncode==0 把 stop() 標的 failed **覆寫回 done** → 狀態回歸。修法：追蹤 `_run_job` tasks，stop 時先 `proc.terminate()` 再 await tasks。
3. **P1（靜默錯誤）— 非 momentum 策略提交會跑錯腳本**：`_run_job` 一律 `[python, config.AI_REPORT_SCRIPT, ...]`（= ai_report.py），registry 的 `script` 欄位（sector_rotation_report.py / meal_money_report.py）**從未被使用**。提交 `sector_rotation_v2` 會真的去跑 momentum 回測並掛上 sector 標籤。修法：依 `STRATEGIES[key]["script"]` 組 cmd；或 schema 未齊的策略直接 400 拒絕（見 1.6 P1）。

⚠️ 需注意：
- **P2**：`_run_job` finally 先 `await _push_status` 再 `_wake.set()` → push 重試期間（最壞 5×15s+退避 ≈ 90s）空執行槽不補位，下一個 job 被延遲。建議先 `_wake.set()` 再 push。
- **P2**：`stop()` 對每個 job 串行 `await _push_status`，多 job 時 shutdown 可能拖到分鐘級。
- **P2**：`_jobs` dict 無上限成長（`stats().total` 與記憶體隨提交數線性成長）— 建議終態 job 加 retention/清理。
- **P2**：submit 的 queued-push task 與 worker 的 running-push 存在理論上的任務序 race（running 可能先於 queued 到達 Worker → 狀態回歸）；子程序分鐘級，機率低，但 heartbeat 不會修正已成功的 dirty 狀態 — 可接受，記錄即可。

### 1.5 paper.py（paper_equity.json + yfinance 重算）— ❌ 有問題（P1×1）

**✅ 正確部分**
- `_load_paper_file` 回傳 (data, mtime)，缺失 404、損壞 500，處理正確 ✅
- `_fetch_latest_prices` 用 `run_in_executor` 包 blocking yfinance，不卡 event loop ✅
- 單檔/多檔 DataFrame 結構雙路徑處理（含 MultiIndex columns、NaN 過濾、`.TW` 後綴剝離）✅
- `/paper/state` 欄位與 paper_equity.json 實際結構（skill 記錄）一致 ✅
- `build_snapshot` 正規化輸出與 D1 三表（paper_snapshots/positions/trades）欄位一一對應 ✅

**❌ 有問題**
1. **P1（估算公式錯誤）— `/paper/live` 總權益雙重扣減成本**（paper.py:161-165）：
   ```
   invested_cost = Σ(entry × shares)
   total_equity_est = capital - invested_cost + total_mv
   ```
   但 paper_equity.json 的 `capital` **是純現金**（paper_tracker.py:387 開倉 `capital -= (cost+buy_cost)`、:295 平倉 `capital += proceeds`），正確總權益 = `capital + Σ(price×shares)`（paper_tracker.py:446-449 同式）。現程式在有持倉時多扣一次 invested_cost → **權益低估達 2×投入成本**。例：現金 50k + 持倉市值 50k（成本 40k）→ 真值 100k，程式回 60k。修法：`total_equity_est = capital + total_mv`。

⚠️ 需注意：
- **P2**：`/paper/refresh` 推送的 snapshot 是檔案內 equity_curve 尾筆（昨日值），**不是**即時重算結果 — 命名與行為有落差（docstring 已註明 Phase 3 完整版會呼叫 paper_tracker，可接受但建議前端文案避免誤導）。
- **P2**：`realized` 變數（:140）為 dead code。

### 1.6 strategies.py（策略 registry）— ⚠️ 需注意（P1×1）

**✅ 正確部分**
- 20 欄 schema 與 ai_report.py argparse **全部對上**（本次逐一 regex 比對：pool/tp-sl-mode 枚舉、tp/sl/tp_atr/sl_atr/hold_days/gap_filter/slippage/capital/position_size/buy_cost/sell_cost/days 預設值全一致）✅
- `regime_filter=False → --no-regime-filter` 特殊處理對應 ai_report 的 store_true/store_false 配對 ✅
- `tickers` nargs='+' 特殊處理 ✅
- `validate_params` 型別/枚舉/範圍檢查（含 bool 是 int 子類的陷阱排除）✅
- advanced_json 逃生門（kebab-case 轉換 + bool/list 處理）✅

**❌ 有問題**
1. **P1（靜默參數丟棄）— `rank_weight` 在 schema 有、在 `_CLI_MAP` 沒有**：schema 暴露 `rank_weight`（boolean，預設 False），但 `_CLI_MAP` 無此 key 且無特殊處理 → `params={"rank_weight": true}` 時 `build_cli_args` 回傳 `[]`（已實測），**使用者勾選「排名加權」實際不生效、無任何警告**。與核心端「多跳欄位傳遞」pitfall 同型（寫入端有、消費端有、中間跳丟掉）。修法：加入 `_CLI_MAP`（boolean→flag 邏輯已存在），或從 schema 移除。
2. **P1（同 1.4-P1-3）— registry `script` 欄位是死欄位**：`backtest.py` 不讀它；`sector_rotation_v2` / `meal_money_v2` 目前 schema 為空，提交後會跑 ai_report.py 並 mislabel。至少應在 `create_job` 對 schema 為空的策略回 400（「Phase 3 未開放」）。

⚠️ 需注意：
- **P2**：`validate_params` 對 schema 外的未知 key 靜默放行（typo 參數如 `slipage` 會被靜默丟棄，與 rank_weight 同源）— 建議未知 key 回 warning 或 422。
- **P2**：slippage 預設 0.001 與 PLAN §3.2 範例 0.003、paper slippage（P0-4 已修為 0.003）不一致 — 需確認回測預設是否該對齊（核心 ai_report 預設仍 0.001，此為三方落差，非單方錯誤）。
- **P2**：PLAN §3.2 範例註解 `pool: full | static | custom` 與實際枚舉 `full | legacy` 不一致（plan 文件問題）。

### 1.7 sync.py（D1 push client）— ✅ 正確

- 指數退避 1s→2s→4s→8s + jitter、最多 5 次（符合 PLAN §1/§5.2；註：PLAN §3.3 文字寫「重試 3 次」為文件內部不一致，code 取 5 次）✅
- 4xx 不重試（請求本身有問題，符合最佳實務）✅
- 失敗不拋例外、記錄 `_last_sync` 供 /health、heartbeat 補推 — 完全符合「盡力而為」設計 ✅
- `init()` 惰性建立 httpx client（event loop 內），headers 含 Service Token + Content-Type ✅
- 模組層級 singleton + 便捷函式，`if __name__ == "__main__"` 除錯入口 ✅

⚠️ 需注意：
- **P2**：push body 無大小上限（PLAN §6.4 明訂 paper <5MB、backtest <64KB）— 建議在 `_push` 加 body size guard。
- **P2**：`SYNC_CLIENT_*` 缺省沿用入站 token（config 有文件註明）— 雙向共用同一 token 屬安全弱化（任一側洩漏即可雙向冒充），production 應照 PLAN §5.2 使用兩組獨立 Service Token。
- **P2**：`_json_dumps` 為 dead code（`json` import 另有用途，可刪）。

### 1.8 config.py（環境變數）— ✅ 正確

- 集中管理、`_env_bool` 解析、路徑 resolve、預設值與實際部署（/root/work/tw_stocker、port 8100、JOBS_DIR）一致 ✅
- 敏感值一律 env、fail-closed 設計（token 缺 → auth 層 503）✅
- `ensure_dirs()` 供 lifespan 呼叫 ✅

---

## 2. 與 PLAN_web_ui_v2.md 對照總表

| PLAN 要求 | 落點 | 判定 |
|---|---|---|
| §3.3 佇列 (priority DESC, created_at ASC) | backtest.py heapq(-priority, seq) | ✅ |
| §3.3 並行 2 / 佇列上限 5 / 429 | config + backtest.submit | ✅ |
| §3.3 狀態機每轉移 push + heartbeat 補推 | _push_status + _heartbeat_loop 30s | ✅ |
| §3.3 狀態 push 失敗重試（§1 為 5 次） | sync 退避 5 次 | ✅ |
| §3.2 策展子集 + advanced_json 逃生門 | strategies schema + build_cli_args | ⚠️ rank_weight 漏接（P1） |
| §3.3 多策略 script | strategies `script` 欄位 | ❌ 未接線（P1） |
| P2-7 /health 六欄 | health.py | ✅ |
| §6.3 雙向 Service Token + fail-closed | auth.py / sync.py | ✅（出站沿用入站 token 為 P2） |
| §1 方向鐵則（VPS→Worker→D1，VPS 不直連 D1） | sync.py 只呼叫 Worker API | ✅ |
| §3.3 jobs/ per-job log + meta 復原 | backtest.py | ✅ |
| P1-4 paper 正規化三表 | build_snapshot 欄位對齊 | ✅ |
| §6.4 sync body 大小上限 | — | ❌ 未實作（P2） |

---

## 3. 問題清單總覽

| # | 嚴重度 | 位置 | 問題 | 建議修法 |
|---|---|---|---|---|
| 1 | **P1** | strategies.py | `rank_weight` schema 有、_CLI_MAP 無 → 靜默丟棄（實測） | 加入 _CLI_MAP 或移除暴露 |
| 2 | **P1** | backtest.py / strategies.py | `script` 欄位未接線 → sector/meal_money 提交會跑錯腳本 | 依 script 組 cmd；空 schema 策略先 400 |
| 3 | **P1** | paper.py:161-165 | /paper/live 權益 = capital - cost + mv → 雙重扣減，低估 2×成本 | 改 `capital + total_mv` |
| 4 | **P1** | backtest.py _extract_result | 並行 job 讀最新 metadata → 結果交叉污染（已註解承認） | 並行=1 或 mtime 門檻，Phase 2 輸出目錄隔離 |
| 5 | **P1** | backtest.py stop() | 不終止子程序 → 孤兒 process；_run_job 覆寫 failed→done | 追蹤 tasks，先 terminate 再 await |
| 6 | **P2** | backtest.py | 先 push 後 _wake.set() → 空槽補位延遲（最壞 ~90s） | 先 wake 再 push |
| 7 | **P2** | backtest.py | _jobs 無上限成長；stop 串行 push 拖慢 shutdown | retention / 併行 push |
| 8 | **P2** | strategies.py | 未知 params key 靜默放行 | 未知 key 422 或 warning |
| 9 | **P2** | strategies.py | slippage 預設 0.001 vs PLAN/paper 0.003 三方落差 | 確認後對齊 |
| 10 | **P2** | sync.py | push body 無大小上限（PLAN §6.4） | 加 size guard |
| 11 | **P2** | config.py | 出站沿用入站 token（雙向共用） | production 用兩組 token |
| 12 | **P2** | main.py | `/` root 與 /docs 未掛 auth dependency | 補 dependency 或註明由隧道擋 |
| 13 | **P2** | paper.py | /refresh 推昨日 snapshot 非即時值；`realized` dead code | 文案釐清 + 刪除 |
| 14 | **P2** | sync.py / auth.py | `_json_dumps` dead code；WWW-Authenticate: Bearer 語意 | 清理 |
| 15 | **P2** | backtest.py | queued/running push 任務序理論 race（低機率） | 記錄即可 |

---

## 4. 整體評價

**架構品質：優。** 骨架完整覆蓋 PLAN v2 的核心要求（佇列優先序、狀態機 + 主動推送 + heartbeat、指數退避 sync、fail-closed 認證、health 六欄、paper 正規化 snapshot），模組職責單一、docstring 引用 PLAN 段落、防禦式處理（psutil 缺失、4xx 不重試、recovery 機制）到位。CLI flag 對照表經逐一比對與 ai_report.py **全數一致**，顯示 agy 對核心端有確實做過交叉驗證 — 這在 AI 生成的程式碼中相當少見。

**主要扣分：3 個功能性 P1（rank_weight 靜默丟棄、多策略跑錯腳本、paper 權益雙重扣減）** — 全部屬於「跨模組/跨跳接線」型錯誤（schema 有、執行端沒接），與 tw_stocker 核心歷史上的 P0-3 同型，正好印證了本專案的「多跳欄位傳遞要逐跳驗證」鐵律。另 2 個 P1 屬並行/生命週期（結果交叉污染、stop 不殺子程序）。

**分數：7.5 / 10**

**可否上線：**
- ❌ **現狀不可直接上線**（P1×5 未解）
- ✅ **修完 P1 後可上 Phase 2**：修法皆為小改動（估 0.5–1 天）：
  1. `rank_weight` 加入 `_CLI_MAP`（或移除 schema 暴露）
  2. `create_job` 對 schema 為空的策略回 400（多策略接線留 Phase 3）
  3. `/paper/live` 公式改 `capital + total_mv`（一行）
  4. `_extract_result` 加 mtime 門檻或並行暫設 1（Phase 2 輸出目錄隔離前）
  5. `stop()` 追蹤並 terminate 子程序
- P2 項可併入 Phase 2 收尾（皆為加固/清理，無架構變動）

**給 agy 的回饋重點**：接線完整性（registry → CLI 執行端）與並行隔離是這次審查的兩大缺口；建議補一個「schema ↔ _CLI_MAP ↔ argparse」三方一致性測試（類似 test_validation_gates.py 的風格），防止未來加參數再次漏接。

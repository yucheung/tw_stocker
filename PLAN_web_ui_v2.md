# tw_stocker Web UI 架構規劃（v2 — 審查修正版）

> 版本：v2.0（2026-08-16）
> 系統：tw_stocker v8.5（量化版 commit `944b24fe`）＋ 多策略模組（Momentum / Sector Rotation / Meal Money）
> 目標：在既有 CLI + Telegram 日報之上，建立多使用者 Web UI，支援回測、Paper Trading 追蹤、多策略管理，並為未來國泰證券 API 串接預留擴展點。
> 本版變更：依專家審查 9 項問題（P0×3 + P1×3 + P2×3）修正，逐項處置見 **附錄 C**。v1 的整體架構（Workers 只做輕量 API + D1 mirror、重運算留 VPS、Zero Trust 認證、增量不取代既有管線）保留不變。

---

## 0. 設計前提（先釐清再動工）

| 事實 | 影響 |
|---|---|
| 核心運算（yfinance 下載、回測、訊號產生、paper 更新）是 **Python + pandas**，單次全池回測 2–3 分鐘、佔記憶體 <1GB | **不能在 Cloudflare Workers 跑**（免費方案 CPU 10ms 限制）；必須留在 CX23 VPS |
| Cloudflare Workers 免費方案：100k req/day、D1 5GB / 5M read/day、KV 100k read/day、R2 10GB、subrequest 50 個 | Workers 只放「輕量 API + 靜態前端 + 認證閘門」；重運算全部 proxy 到 VPS。**認證驗證成本（JWKS fetch + JWT verify）必須壓到 10ms 內**（見 §6.1） |
| paper 狀態目前是 repo 內 `paper_equity.json`（GH Actions 每日 17:17 TW 自動更新並 commit） | **single source of truth 維持在 VPS/Repo**；D1 只當「顯示用 mirror」。**所有寫入 D1 的路徑只有一條：Worker API（VPS/GH Actions 一律經隧道呼叫 Worker，不直接碰 D1）** |
| GH Actions 每日自動跑、Telegram 18:10 日報 cron 已穩定運作 | Web UI 是**增量**，不取代既有管線；Phase 1 先做唯讀鏡像即可上線 |
| 已有 guanlan-rss-reader 的 Workers + D1 部署經驗（wrangler + API token + D1 migration 流程） | 部署流程直接沿用既有成功模式 |
| 審查發現：sync 方向需明確化、JWT 驗證需 KV cache、回測狀態需 VPS 主動推、D1 JSON 欄位需正規化、缺 session refresh/佇列優先序/health/backup/結果版本化 | 本版已逐項修正（附錄 C） |

---

## 1. 系統架構圖（文字）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 使用者瀏覽器（桌機/手機）                                            │
│    │                                                               │
│    ▼                                                               │
│ ① Cloudflare Zero Trust Access  ←── Google / GitHub OAuth SSO      │
│    （domain 層認證牆，發 CF-Access-JWT-Assertion；session 30 天）    │
│    ▼                                                               │
│ ② Cloudflare Workers（tw-stocker-web）                             │
│    ├─ 靜態 SPA（React + Vite build 產物，assets 直接服務）          │
│    ├─ /api/* 輕量端點：auth 檢查、D1 CRUD、日報/訊號/paper 唯讀     │
│    ├─ /api/vps/* proxy：帶 Access Service Token 轉發到 VPS 隧道    │
│    ├─ /api/vps/sync/*：**D1 唯一寫入入口**（Service Token 驗證）    │
│    ├─ KV：JWKS 快取 + JWT 驗證快取（認證效能，§6.1）               │
│    ├─ R2：D1 每日備份 + 大 JSON 原始檔（§5.4）                      │
│    │                                                               │
│    ├──► ③ D1 資料庫（tw-stocker-db）                               │
│    │     users / strategies / backtest_runs / paper mirror（正規化）/│
│    │     signals / audit_log / settings                             │
│    │                                                               │
│    └──► ④ Cloudflare Tunnel（cloudflared，私有 hostname）          │
│           │  Service Token 驗證（Worker↔VPS 雙向）                 │
│           ▼                                                        │
│ ⑤ CX23 VPS（3.7GB RAM）                                            │
│    ├─ FastAPI（uvicorn + systemd）                                  │
│    │    ├─ GET  /health            → 健康狀態（供 /admin 聚合）     │
│    │    ├─ POST /backtest/jobs     → 子程序跑 ai_report.py（含優先序）│
│    │    ├─ GET  /backtest/jobs/{id}/log → 即時 log tail（選用）     │
│    │    ├─ GET  /paper/live        → yfinance 即時報價重算          │
│    │    ├─ GET  /paper/state       → paper_equity.json 即時讀取     │
│    │    ├─ CRUD /strategies        → 策略參數管理                   │
│    │    └─ /broker/*               → 券商 adapter（Phase 4）        │
│    ├─ tw_stocker 核心（ai_report.py / paper_tracker.py / strategy/）│
│    ├─ sync.py：狀態/結果**主動推送**到 Worker（回測、paper、訊號）  │
│    └─ cron：每日收盤後 paper_tracker 更新 → sync.py 推 snapshot     │
│                                                                    │
│ ⑥ GitHub Actions（既有 update_ai_report.yml，不變）                 │
│    └─ 每日 17:17 回測 + paper 更新 + commit 回 repo                │
└─────────────────────────────────────────────────────────────────────┘
```

**資料流摘要**

- **唯讀路徑（快）**：瀏覽器 → Access → Worker → D1（日報、歷史訊號、paper 歷史、回測結果清單）。全程不進隧道、不碰 VPS，幾 ms 內回。
- **運算路徑（慢）**：瀏覽器 → Access → Worker（寫 D1 `backtest_runs` status=queued）→ 隧道 → VPS FastAPI → 子程序跑 Python 核心 → **VPS 在各狀態轉移時主動 push 到 Worker**（running/progress/done 摘要/failed/cancelled）→ 前端**輪詢 Worker 讀 D1**（不走隧道）。
- **每日同步路徑（單向，VPS → Worker API → D1）**：
  ```
  GH Actions 17:17 更新 paper_equity.json（repo，source of truth）
      │（commit 完成後）
      ▼
  VPS cron 17:45：paper_tracker 產出 snapshot → sync.py
      │  POST https://tw-stocker-web.yucheung.workers.dev/api/vps/sync/paper
      │  帶 tw-stocker-web Service Token（CF-Access-Client-Id/Secret）
      ▼
  Worker：驗證 Service Token → 寫入 D1 paper_* 表 → 回 200
      │（失敗時 sync.py 指數退避重試，最多 5 次）
      ▼
  D1 mirror 更新完成（前端唯讀即時可見）
  ```
  > ⚠️ **方向鐵則**：VPS 永不直連 D1。D1 的唯一寫入入口是 Worker `/api/vps/sync/*`（Service Token 保護）。架構圖中任何「VPS → D1」的箭頭都必須畫成「VPS →（隧道）→ Worker → D1」。

---

## 2. 前端模組（React + Vite + TypeScript SPA）

**框架選擇**：React 19 + Vite（非 Next.js）。理由：
- 純 SPA + Worker API，不需要 SSR；Vite build 產物可直接用 `assets:` 部署（與 guanlan/ziwei-web 相同模式，驗證過）
- Workers 免費方案不適合跑 Next.js SSR 的 Node 相容層，SPA 最輕
- 圖表用 `lightweight-charts`（TradingView，台股慣用）或 `echarts`；表格用 TanStack Table

### 頁面清單

| 頁面 | 路由 | 功能 | 資料來源 |
|---|---|---|---|
| **登入** | `/login` | Zero Trust 牆後的 app 內入口（選 Google/GitHub）；顯示目前使用者、角色 | Worker `/api/me` |
| **儀表板** | `/` | 總權益、今日訊號 Top-7（代碼+中文名）、持倉摘要、近 7 日平倉、大盤 regime 狀態 | D1 `signals` + `paper_*` mirror |
| **Paper Trading** | `/paper` | 持倉表（entry/TP/SL/浮動損益）、權益曲線圖、平倉歷史、pending orders；**即時價**按鈕觸發 VPS 重算 | D1 mirror + VPS `/paper/live` |
| **回測** | `/backtest` | 參數表單（見 §3.2）、**優先序選擇**、提交任務、進度輪詢（**讀 D1，不走隧道**）、結果圖表、**比較檢視**（同策略不同參數 side-by-side，§3.4） | D1 `backtest_runs`（狀態/摘要）+ VPS（僅 log tail） |
| **策略管理** | `/strategies` | 策略列表（啟用/停用）、參數編輯（版本化）、universe 設定、策略說明文件 | D1 `strategies` |
| **訊號日報** | `/signals` | 歷史日報瀏覽（對應 repo 內 `stock_report.html` 內容）、月報 | D1 `signals` |
| **管理**（admin only） | `/admin` | 使用者角色管理、系統狀態（VPS `/health`、隧道、上次 sync、**D1 備份狀態**）、audit log | D1 `users` + Worker `/api/system` |
| **券商**（Phase 4） | `/broker` | 訂單審批佇列、下單紀錄、連線狀態（先 dry-run） | VPS `/broker/*` |

### 共用元件

- `Layout`（側欄 + 頂欄 + 手機底部導覽，沿用 guanlan 響應式斷點經驗）
- `AuthProvider`（讀 `CF-Access-JWT-Assertion` → `/api/me` 拿 email/role；**401 時自動靜默續期**，§6.2）
- `ChartCard`（權益曲線、回撤、drawdown 圖）
- `DataTable`（排序/分頁/匯出 CSV）
- `JobProgress`（回測任務進度條 + 日誌 tail；狀態來自 D1、log 選用走隧道）
- `StockNameTag`（代碼 → 中文名，用既有 `tw_stock_names.json` 對照）
- `CompareView`（回測比較：指標並排表 + 權益曲線疊圖 + 參數 diff，§3.4）

---

## 3. 後端 API 設計

### 3.1 Worker 端點（Hono/Itty Router，直接讀 D1）

| Method | Path | 功能 | 權限 |
|---|---|---|---|
| GET | `/api/me` | 回傳 Access JWT 內的 email + D1 角色；JWT 過期時 fallback 到 get-identity 續期（§6.2） | 任何已登入者 |
| GET | `/api/dashboard` | 今日訊號 + 權益摘要（一個查詢組裝） | 已登入 |
| GET | `/api/signals?date=&limit=` | 歷史訊號日報 | 已登入 |
| GET | `/api/paper/snapshot?date=` | paper mirror 最新/指定日快照（positions/trades/equity） | 已登入 |
| GET | `/api/paper/equity?range=` | 權益曲線序列 | 已登入 |
| GET | `/api/backtest/runs?strategy=&status=&priority=` | 回測任務清單（metadata + 結果摘要） | 已登入 |
| GET | `/api/backtest/runs/{id}` | 單一任務詳情 + 結果摘要（**前端輪詢此端點**） | 已登入 |
| GET | `/api/backtest/compare?ids=a,b,c` | 多任務指標並排（§3.4） | 已登入 |
| GET | `/api/strategies` | 策略清單（含啟用狀態） | 已登入 |
| GET | `/api/system/health` | VPS `/health` + 隧道 + 上次 sync + D1 備份狀態聚合 | 已登入 |
| **→ VPS proxy（以下全部經隧道 + Service Token）** | | | |
| POST | `/api/vps/backtest/jobs` | 提交回測任務（含 priority） | admin（trader 限 normal） |
| GET | `/api/vps/backtest/jobs/{id}/log` | 即時 log tail（**選用**；狀態一律讀 D1） | 已登入 |
| POST | `/api/vps/backtest/jobs/{id}/cancel` | 取消任務 | admin |
| POST | `/api/vps/paper/refresh` | 觸發即時報價重算 | 已登入 |
| GET | `/api/vps/paper/positions/live` | 持倉含浮動損益（yfinance 即時） | 已登入 |
| PUT | `/api/vps/strategies/{id}` | 更新策略參數（validate + 重啟生效） | admin |
| POST | `/api/vps/strategies/{id}/activate` / `deactivate` | 啟停策略 | admin |
| GET | `/api/vps/backtest/jobs/{id}/artifact?file=` | 下載 VPS artifacts（equity.csv 等，經隧道） | 已登入 |
| **→ D1 寫入入口（由 VPS / GH Actions 呼叫，Service Token）** | | | |
| POST | `/api/vps/sync/paper` | 推 paper snapshot（**正規化後寫入 paper_snapshots + paper_positions + paper_trades**） | Service Token |
| POST | `/api/vps/sync/report` | 推日報摘要進 D1（signals + signal_items） | Service Token |
| POST | `/api/vps/sync/backtest` | **推回測任務狀態轉移**：`{job_id, status, progress?, result_json?, error?}` → upsert `backtest_runs` | Service Token |
| POST | `/api/vps/broker/orders`（Phase 4） | 下單請求（先進審批佇列） | admin + 雙重確認 |
| GET | `/api/vps/broker/status`（Phase 4） | 券商連線/憑證狀態 | admin |

> **同步方向鐵則**（P0-1 修正）：`/api/vps/sync/*` 由 **VPS 側 cron / sync.py 主動呼叫**（帶 `tw-stocker-web` Service Token），Worker 驗證後寫 D1。Worker 永不反向拉取 VPS；VPS 永不直連 D1。

### 3.2 回測任務參數（VPS 端點 body）

對應 `ai_report.py` 既有 80+ CLI 參數，**Web 只暴露策展子集**（避免 UI 爆炸），完整參數留 `advanced_json` 逃生門：

```jsonc
{
  "strategy": "momentum_v85",          // 對應 strategy 註冊表
  "name": "TP4.0/SL3.0 基線",           // 任務命名（建議含版本語意，如「基線 v1」）
  "priority": 1,                        // 0=low | 1=normal | 2=high（admin 可選 2；trader 鎖 0–1）
  "params": {
    "tickers": null,                    // null = 動態 universe 全池
    "pool": "full",                     // full | static | custom
    "days": 1200,
    "start_date": null, "end_date": null,
    "top_k": 7,
    "capital": 200000,
    "tp_atr": 4.0, "sl_atr": 3.0, "hold_days": 20,
    "tp_sl_mode": "atr",                // atr | pct
    "gap_filter": 1.5,
    "slippage": 0.003,
    "position_size": null, "rank_weight": null,
    "regime_filter": true,
    "sector_flow_tilt": null,
    "buy_cost": null, "sell_cost": null
  },
  "advanced_json": null,                // 完整 CLI 參數字典（選填，直接透傳）
  "notify": { "telegram": true }        // 完成後推 Telegram（沿用既有機制）
}
```

VPS 收到後：**先 push 狀態 queued 到 Worker**（寫 D1 `backtest_runs`）→ 依優先序入佇列 → `subprocess` 跑
`python3 ai_report.py <params>`（log 檔 per-job）→ **每個狀態轉移都 push**（running → progress → done 摘要 / failed 錯誤 / cancelled）→ 完整結果（equity CSV、trades）存 VPS `artifacts/runs/{job_id}/`，D1 只存摘要 + artifacts 下載路徑（經隧道取檔）。

### 3.3 VPS FastAPI 內部結構

```
vps_api/
├── main.py            # FastAPI app、Service Token middleware、router 掛載
├── health.py          # GET /health：version/uptime/queue/mem/disk/last_sync（P2-7）
├── auth.py            # Access Service Token 驗證（Worker 帶的 CF-Access-Client-Secret）
├── backtest.py        # 任務佇列（asyncio + subprocess、優先序、狀態機 + 狀態 push 回呼）
├── paper.py           # paper_equity.json 讀取、yfinance 即時重算、snapshot 產出
├── strategies.py      # 策略 registry 包裝（strategy/ 模組 + 參數 schema）
├── sync.py            # D1 push client（呼叫 Worker /api/vps/sync/*，指數退避重試）
├── broker/            # Phase 4：adapter 介面 + cathay.py 實作（先 dry-run）
└── jobs/              # 每個 backtest run 的 log / 結果檔
```

**佇列設計（含優先序，P1-6）**：
- 佇列排序：`(priority DESC, created_at ASC)`；高優先 job 插隊（不中斷正在跑的 job）
- **並行限制**：3.7GB RAM 上同時最多 2 個回測（yfinance 下載 + pandas 約 <1GB/run）；佇列一次跑一個、其餘排隊（queue 上限 5，超出回 429 — 429 前先檢查是否可改低優先）
- 狀態機：`queued → running → done | failed | cancelled`；每次轉移 call `sync.push_backtest_status()`，失敗重試 3 次（不阻塞主流程，留待下次 heartbeat 補推 — Worker 端 upsert 冪等）

### 3.4 回測結果比較（P2-9）

- Worker 計算 `params_hash = sha256(strategy_key + canonical(sorted(params)))`，存入 `backtest_runs`
- 前端 `/backtest`：「比較模式」勾選同策略 2–4 個 run → `/api/backtest/compare` 回並排指標（年化/Sharpe/MDD/勝率/PF/交易數）+ 權益曲線疊圖 + 參數 diff（`params_json` 差異高亮）
- `name` 欄位建議語意化（如「基線 v1」「加滑價 0.005」「regime off」），搭配 `params_hash` 自動群組「同參數重跑」偵測（相同 hash 的多次 run 標示為重跑）

---

## 4. 資料庫 Schema（D1）

> D1 只存**顯示與管理所需**資料；`paper_equity.json`、回測完整結果留在 VPS/Repo 為 source of truth。
> **正規化原則（P1-4 修正）**：不把大 JSON 塞單欄。持倉、成交、訊號拆成明細表（每列 <1KB），原始大 JSON 選配存 R2（§5.4）。任何表單列不得超過 ~64KB（D1/SQLite 保守上限，避免 5MB 單查詢限制風險）。

```sql
-- 使用者（由 Access JWT email 自動建立，admin 指派角色）
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE,          -- Access JWT 的 email
  name          TEXT,                          -- 顯示名（從 OAuth profile）
  role          TEXT NOT NULL DEFAULT 'viewer' -- viewer | trader | admin
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);

-- 策略註冊表（多策略管理核心）
CREATE TABLE strategies (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  key           TEXT NOT NULL UNIQUE,   -- 'momentum_v85' | 'sector_rotation_v2' | 'meal_money_v2'
  name_zh       TEXT NOT NULL,          -- 中文名
  description   TEXT,
  version       TEXT NOT NULL,          -- 策略版本
  enabled       INTEGER NOT NULL DEFAULT 1,
  params_json   TEXT NOT NULL DEFAULT '{}',  -- 當前參數（對應 ai_report CLI 子集，<4KB）
  schema_json   TEXT NOT NULL DEFAULT '{}',  -- 參數 schema（前端表單自動產生）
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 回測任務（含優先序 + 參數指紋，P1-6 / P2-9）
CREATE TABLE backtest_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_key  TEXT NOT NULL REFERENCES strategies(key),
  name          TEXT,
  status        TEXT NOT NULL DEFAULT 'queued', -- queued|running|done|failed|cancelled
  priority      INTEGER NOT NULL DEFAULT 1,     -- 0=low|1=normal|2=high（VPS 佇列排序用）
  params_json   TEXT NOT NULL,                  -- 提交參數（<4KB）
  params_hash   TEXT,                           -- sha256(strategy_key+canonical params)，比較群組用
  result_json   TEXT,                   -- 摘要：年化/Sharpe/MDD/勝率/PF/trades 數（<4KB）
  progress      REAL,                   -- 0–1（VPS 推送，選用）
  job_id        TEXT,                   -- VPS 端任務 id（log 檔對應）
  artifact_path TEXT,                   -- VPS artifacts/runs/{job_id}/ 相對路徑（下載經隧道）
  requested_by  TEXT NOT NULL,          -- 使用者 email
  error         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  started_at    TEXT,
  finished_at   TEXT
);
CREATE INDEX idx_backtest_status ON backtest_runs(status, created_at);
CREATE INDEX idx_backtest_hash  ON backtest_runs(strategy_key, params_hash);

-- paper mirror：快照主表（P1-4 正規化）
CREATE TABLE paper_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,          -- YYYY-MM-DD
  equity        REAL NOT NULL,          -- 總權益（最後一筆 equity_curve）
  capital       REAL NOT NULL,
  n_positions   INTEGER NOT NULL,
  n_closed      INTEGER NOT NULL,       -- 當日平倉筆數
  raw_json_key  TEXT,                   -- R2 key（完整 paper_equity.json 原始檔，選配）
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(snapshot_date)
);

-- 持倉明細（一列一檔，<1KB/列）
CREATE TABLE paper_positions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id   INTEGER NOT NULL REFERENCES paper_snapshots(id),
  ticker        TEXT NOT NULL,
  entry         REAL NOT NULL,
  tp            REAL,
  sl            REAL,
  shares        REAL NOT NULL,
  entry_date    TEXT NOT NULL,
  day_count     INTEGER,
  max_hold_days INTEGER,
  UNIQUE(snapshot_id, ticker)
);
CREATE INDEX idx_positions_snap ON paper_positions(snapshot_id);

-- 平倉明細（一列一筆成交）
CREATE TABLE paper_trades (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id   INTEGER NOT NULL REFERENCES paper_snapshots(id),
  ticker        TEXT NOT NULL,
  entry         REAL NOT NULL,
  exit          REAL NOT NULL,
  shares        REAL NOT NULL,
  pnl           REAL,
  pnl_pct       REAL,
  reason        TEXT,                   -- TP|SL|TIME
  entry_date    TEXT NOT NULL,
  exit_date     TEXT NOT NULL,
  days_held     INTEGER
);
CREATE INDEX idx_trades_snap ON paper_trades(snapshot_id);

-- 訊號日報 mirror（每日 Top-7 + 中文名，供 /signals 瀏覽）
CREATE TABLE signals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_date   TEXT NOT NULL,
  report_url    TEXT,                   -- repo 內 stock_report.html 連結
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(signal_date)
);
CREATE TABLE signal_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id     INTEGER NOT NULL REFERENCES signals(id),
  rank          INTEGER NOT NULL,
  code          TEXT NOT NULL,
  name_zh       TEXT
);
CREATE INDEX idx_signal_items ON signal_items(signal_id);

-- 審計日誌（admin 操作、下單請求、異常）
CREATE TABLE audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_email    TEXT NOT NULL,
  action        TEXT NOT NULL,          -- 'strategy.update' | 'backtest.submit' | 'broker.order_request' ...
  target        TEXT,                   -- 目標 id/名稱
  detail_json   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_audit_user ON audit_log(user_email, created_at);

-- 設定（系統層 key-value）
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- 種子資料：last_paper_sync_at、last_report_sync_at、last_backup_at、system_version 等
```

**遷移管理**：沿用 guanlan 模式 — `wrangler d1 execute tw-stocker-db --file migrations/0001_init.sql --remote`，每個 migration 一個檔、按序執行。migration 前先跑一次 R2 備份（§5.4）。

---

## 5. 部署流程

### 5.1 倉庫結構（新 repo：`yucheung/tw_stocker-web`）

```
tw_stocker-web/
├── web/                    # React + Vite SPA
│   ├── src/pages/          # §2 頁面
│   └── vite.config.ts
├── worker/                 # Cloudflare Worker（Hono）
│   ├── src/index.ts        # 路由 + Access JWT 驗證 + D1 + VPS proxy + sync 入口
│   ├── src/db.ts           # D1 queries
│   ├── src/auth.ts         # JWT 驗證（KV 快取 JWKS）+ Service Token
│   └── src/backup.ts       # 每日 D1 → R2 備份（cron trigger）
├── migrations/             # 0001_init.sql, 0002_*.sql ...
├── vps_api/                # VPS FastAPI（§3.3）
├── wrangler.jsonc          # 主 config（D1 binding `DB`、KV binding `KV`、R2 binding `BACKUP`、cron）
├── package.json            # worker + web 共用 workspace
└── deploy.sh               # build → dedupe bindings → wrangler deploy（沿用 guanlan 修正模式）
```

### 5.2 部署步驟

**Cloudflare 側（一次設定）**
1. `npx wrangler d1 create tw-stocker-db` → 記下 `database_id` 寫入 `wrangler.jsonc`
2. `wrangler d1 execute tw-stocker-db --file migrations/0001_init.sql --remote`（依序每個 migration；**migration 前先跑備份**）
3. `npx wrangler kv namespace create JWKS_CACHE` → binding `KV`（認證快取，§6.1）
4. `npx wrangler r2 bucket create tw-stocker-backup` → binding `BACKUP`（§5.4）
5. Zero Trust → Applications → 新增 **Self-hosted**：public hostname = `tw-stocker-web.yucheung.workers.dev`
   - Policy：允許 Google/GitHub SSO（Zero Trust → Settings → Authentication → Add Google/GitHub，填入 OAuth client）
   - **Session duration 設 30 天**（remember-me；JWT assertion 過期由 §6.2 靜默續期處理）
   - Phase 3 再細分 policy（admin group）
6. Zero Trust → Access → Service Tokens：建立 `tw-stocker-vps`（Worker 呼叫 VPS 用）與 `tw-stocker-web`（VPS 呼叫 Worker 用），secret 存兩端
7. 部署：`CLOUDFLARE_API_TOKEN`（已在 `~/.hermes/.env`）→ `bash deploy.sh`

**VPS 側（CX23）**
1. `git clone https://github.com/yucheung/tw_stocker-web`（或直接收進既有 repo 的 `web/` 子目錄 — 見 §8 決策）
2. `python3 -m venv .venv && pip install -r requirements.txt fastapi uvicorn httpx`
3. `cloudflared tunnel create tw-stocker` → 設 `config.yml` 指到 `http://localhost:8100` → DNS route 私有 hostname（`tw-stocker-vps.yucheung.workers.dev`，**只允許 Service Token 存取**）
4. systemd unit：`tw-stocker-api.service`（uvicorn，`--workers 1`，8100 port）
5. cron（沿用既有 Telegram cron 模式）：每日 17:45 跑 `paper_tracker.py` 更新 → `sync.py` 推 snapshot 到 Worker `/api/vps/sync/paper`；**sync.py 內建指數退避重試（5 次）+ audit log**，失敗時 Telegram 告警
6. 驗證：`curl -s https://tw-stocker-vps.yucheung.workers.dev/health -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..."` 回 `{"status":"ok"}`

**CI/CD**
- GitHub Actions `ci.yml`：`npm ci && npm run build`（web + worker）→（可選）`wrangler deploy`
- 與既有 `update_ai_report.yml` 完全獨立，互不干擾

### 5.3 免費額度檢核

| 資源 | 用量估算 | 額度 | 餘裕 |
|---|---|---|---|
| Workers 請求 | 個人/小團隊 < 5k/day | 100k/day | ✅ |
| Workers CPU | 全走 D1 或 proxy；**JWT 驗證用 KV 快取 JWKS（<1ms）** | 10ms/req | ✅（**切勿**在 Worker 內做運算） |
| KV read | JWKS 每 6h 1 次 + 驗證快取（60s TTL，回測輪詢期最多 ~1k/day） | 100k read/day | ✅ |
| D1 儲存 | 正規化後純 metadata + mirror < 100MB（遠小於 v1 估） | 5GB | ✅ |
| D1 read | 每頁 2–5 query × 200 次/日 | 5M/day | ✅ |
| R2 | 每日備份 < 1MB × 30 天 + 原始 JSON < 100MB | 10GB / 1M Class A 月 | ✅ |
| Cron trigger | 每日 1 次備份 | 免費方案可（本專案只需 1 個） | ✅ |
| VPS RAM | 1 回測 <1GB、並行上限 2 | 3.7GB | ✅ |

### 5.4 D1 備份策略（P2-8）

- **每日自動**：Worker cron trigger（`0 2 * * *` UTC = 10:00 TW）`src/backup.ts`：讀全部表 → 單一 JSON → PUT R2 `backups/d1/YYYY-MM-DD.json`；同日再更新 `settings.last_backup_at`。R2 lifecycle rule 保留 30 天
- **migration 前手動快照**：`wrangler d1 export tw-stocker-db --remote --output=backup.sql`（D1 原生 export，比 R2 JSON 更完整）
- **還原程序**：`wrangler d1 execute tw-stocker-db --file backup.sql --remote`；R2 JSON 供跨帳號/災難復原（配合 D1 原生 point-in-time restore）
- **驗證**：備份 cron 完成後寫 `settings.last_backup_at`，`/admin` 顯示「上次備份時間」；超過 48h 未備份 → 告警
- 註：D1 是顯示用 mirror（真 source of truth 在 VPS/Repo），備份是**便宜的安全網**，不當主要災難復原機制

---

## 6. 認證流程（SSO + Zero Trust）

### 6.1 外層：Zero Trust Access（domain 閘門）+ 高效能 JWT 驗證（P0-2 修正）

```
1. 未登入訪客 → https://tw-stocker-web.yucheung.workers.dev
2. Access 302 → 登入選擇頁（Google / GitHub / 備援 OTP email）
3. 使用者完成 OAuth → Access 發 session cookie（Cf_Authorization，30 天）
4. 後續請求帶 cookie → Access 驗證通過 → 注入 header：
   CF-Access-JWT-Assertion: <JWT>
   （JWT claims：email、identity_nonce、exp、common_name）
5. Worker 驗證 JWT（**全程無阻塞式網路 I/O**）：
   a. 解析 JWT header 取 kid + payload 取 exp（base64 解碼，0 網路）
   b. exp 過期 → 走 §6.2 續期路徑（get-identity fallback），不回 401
   c. JWKS 從 KV 讀（key: `jwks:<aud>`，TTL 6h，**首次才 fetch**）
      - KV miss 或 kid 不在快取 → fetch https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
        一次 → 寫回 KV；「kid 未知」再觸發一次強制 refetch（防 key rotation 競態）
   d. crypto.subtle.verify（Ed25519，µs 級）+ aud（Access AUD tag）+ exp 檢查
   e. 驗證結果快取 KV（key: sha256(token)，value: {email, role}，TTL min(60s, exp-now)）
      → 回測輪詢等高頻重複請求（同 JWT）直接命中，CPU 成本近零
   f. 成功 → email 進 request context
```

> 效能要點：**JWKS 永不 per-request fetch**（那是 v1 的主要 CPU/延遲殺手）；驗證快取吃下輪詢型流量。實測預算：KV read + Ed25519 verify < 1ms，遠低於 10ms 上限。

### 6.2 內層：角色授權 + Session 靜默續期（P1-5 修正）

- 首次登入：`users` 表無此 email → 自動建立 `viewer` 角色（可看不可操作）
- admin 在 `/admin` 提升角色：`viewer`（唯讀）→ `trader`（可跑回測/paper 操作）→ `admin`（策略/使用者/券商）
- Worker 每個 API 依 route 檢查角色（§3.1 權限欄）
- **Session refresh 流程**（解決 JWT 1h 過期要重登的問題）：
  ```
  前端 AuthProvider：任一 API 回 401
      │（Access session cookie 還在有效期的情況）
      ▼
  前端 302 → https://<team>.cloudflareaccess.com/cdn-cgi/access/callback
             ?redirect_uri=<目前 URL>（全頁導向，非 iframe）
      ▼
  Access 驗證 Cf_Authorization session cookie：
    ├─ 有效 → 重新簽發 assertion → redirect 回原 URL → 前端續用（使用者無感）
    └─ 過期 → Access 顯示登入頁（這是預期行為，30 天一次）
  ```
- Worker 端雙保險：`/api/me` 收到過期 JWT 時，fallback `fetch("https://<team>.cloudflareaccess.com/cdn-cgi/access/get-identity")`（帶入原始 cookie）→ 成功則回傳 identity 並更新 users.last_login_at，前端可續用而不必整頁跳轉
- 前端不再自己發 JWT（v1 的「SPA 不做 session cookie」維持），但 **401 處理從『跳登入頁』改為『先試靜默續期』**
- **Policy 進階選項**（Phase 3）：Zero Trust Access 可依群組細分 — Google Workspace group / GitHub team → Access policy 直接給 admin group 較嚴格 session 或 MFA

### 6.3 VPS 間認證（Service Token，雙向）

```
Worker → VPS：  請求帶 CF-Access-Client-Id + CF-Access-Client-Secret（tw-stocker-vps）
                VPS middleware 驗證（cloudflared 隧道 + Access 會先擋一層）
VPS  → Worker： sync.py 帶 tw-stocker-web service token 呼叫 /api/vps/sync/*
                （P0-1：此方向是唯一正確方向 — VPS 主動推，Worker 寫 D1）
```

### 6.4 安全注意事項

- VPS FastAPI **不綁公開 IP/port**（只從 tunnel 進），防火牆擋 8100 對外
- Access session 30 天 + 靜默續期（§6.2）；角色權限仍以 D1 users 表為準（每請求檢查）
- CORS：SPA 與 API 同域（Worker assets + routes），無跨域問題；VPS 只接受 Worker 轉發，不直接暴露給瀏覽器
- audit_log 記錄所有寫操作（回測提交、策略變更、下單請求、sync 失敗）
- `/api/vps/sync/*` 僅接受 Service Token（拒絕任何瀏覽器 session 直接寫入）；sync body 設大小上限（paper 推 <5MB、backtest 推 <64KB）

---

## 7. 未來擴展

### 7.1 國泰證券 API（Phase 4）

```
策略引擎（VPS） ── broker_adapter 介面 ──┬── DryRunBroker（現在，paper 用）
                                        ├── CathayBroker（國泰，Phase 4）
                                        └── (未來) OtherBroker

interface BrokerAdapter:
    connect(credentials)            # 憑證/憑證檔（ca.pfx）載入
    get_account_balance() -> Money
    get_positions() -> [Position]
    place_order(order) -> OrderId   # 限價/市價、整股/零股
    cancel_order(order_id)
    get_order_status(order_id)
    subscribe_ticks(callback)       # 即時報價（如支援）
```

- **風險**：國泰證券的程式化 API 支援度需先確認（部分券商只提供 Windows/.NET SDK，或僅限特定帳戶類型）；adapter 介面包住差異，實際 SDK 確認後才實作
- **安全設計**：下單一律先進審批佇列（`audit_log` + 管理員確認 + 二次驗證碼），先 dry-run 數週驗證與 paper 一致後才開放實單
- 憑證（私鑰）存 VPS 加密檔或 KMS，**絕不進 repo / D1 / Worker**

### 7.2 新策略加入流程（多策略管理）

1. 在 `strategy/` 新增模組（沿用既有 `event_backtest.py` 風格：`run_backtest(...) -> trades_df, equity_df`）
2. 在 strategies registry 註冊：`key`、中文名、參數 `schema_json`（前端表單自動生成）
3. `INSERT INTO strategies` → Web `/strategies` 即可設定參數、跑回測、看結果
4. 策略間用獨立 `backtest_runs` 隔離，互不影響；paper trading 可選指定策略跟單（Phase 3）
5. **無需改前端**：schema 驅動表單（策略參數變更 = D1 資料變更，不需重新部署）

### 7.3 其他

- 通知：完成回測 / paper TP/SL 觸發 → Telegram（既有機制）+ 可選 Email
- 匯出：回測結果 / paper 績效 CSV（D1 mirror + VPS artifacts 下載）
- 多語言：繁中為主，i18n 字典預留（沿用 guanlan 的 dict 模式）
- 回測參數實驗管理（Phase 3+）：以 `params_hash` 為錨，支援「從上次參數複製 + 微調」的實驗流（與 §3.4 比較檢視互補）

---

## 8. 開發順序（Phase 1–4，含審查修正）

> 每個 Phase 有明確驗收標準，獨立可上線。**審查修正已折入對應 Phase**（附錄 C 對照）；總時程較 v1 增加約 **1.5–2.5 週**。

### Phase 1 — 地基：認證 + 唯讀儀表板（1–1.5 週）

**目標**：能登入、能看到 paper 現況與日報 — 立即有價值

- [ ] 建 repo `tw_stocker-web`（SPA 骨架 + Worker + D1 migrations）
- [ ] Zero Trust Access 設定（Google/GitHub SSO + 既有 email OTP 備援；**session 30 天**）
- [ ] Worker：Access JWT 驗證（**KV 快取 JWKS + 驗證快取，P0-2**）+ `/api/me` + users 表自動建立
- [ ] **Session 靜默續期（P1-5）**：401 → Access callback 續期 + get-identity fallback
- [ ] D1 migration 0001（全部表，**paper 正規化三表，P1-4**）
- [ ] 頁面：Dashboard、Paper（唯讀 mirror）、Signals
- [ ] VPS sync cron：每日推 `paper_equity.json` snapshot 進 D1（**方向明確：VPS → Worker API → D1，P0-1**；先吃 GH Actions 產物，**不動既有管線**）
- [ ] **D1 每日備份到 R2（P2-8）**：cron trigger + migration 前快照
- [ ] ✅ 驗收：登入 → 看到今日訊號 + paper 權益曲線；`curl` 無 token 被 302/401；**JWKS 只 fetch 一次（之後全命中 KV）；登入後 1h JWT 過期仍能無感續用**

### Phase 2 — 回測引擎上線（1.5–2.5 週）

**目標**：Web 上能跑回測、看結果、比結果

- [ ] VPS FastAPI 骨架 + cloudflared 隧道 + Service Token 雙向驗證 + **`/health`（P2-7）**
- [ ] 回測 job 佇列（subprocess 包 `ai_report.py`、per-job log、取消、**優先序排序，P1-6**）
- [ ] **狀態主動推送（P0-3）**：VPS 狀態機每轉移 push 到 Worker `/api/vps/sync/backtest` → 前端輪詢 Worker 讀 D1（**不走隧道**）；log tail 選用經隧道
- [ ] `/backtest` 頁面：參數表單（§3.2 子集）、優先序、進度輪詢（D1）、結果圖表
- [ ] **結果版本化 + 比較檢視（P2-9）**：`params_hash` 計算 + 同策略多 run 並排比較
- [ ] 結果摘要寫 D1 `backtest_runs` + 完整結果存 VPS artifacts（下載經隧道）
- [ ] ✅ 驗收：Web 提交全池回測 → 2–3 分鐘 → 圖表 + 指標摘要，與 CLI 結果一致；**高優先 job 插隊；隧道中斷時前端仍能顯示「running」狀態（D1 為主）**

### Phase 3 — Paper 即時化 + 策略管理（1–2 週）

**目標**：可操作、可管理多策略

- [ ] `/paper` 即時報價重算（VPS `/paper/live`，yfinance 盤中/盤後）→ 結果**回推 D1**（沿用 sync 方向）
- [ ] 手動觸發 paper 更新（取代只靠每日 cron）
- [ ] `/strategies` 頁面 + 策略 registry（Momentum / Sector Rotation / Meal Money 註冊）
- [ ] 角色權限分級（viewer/trader/admin）+ `/admin` 使用者管理（含 **VPS health 聚合 + 備份狀態顯示**）
- [ ] 參數變更版本化（audit_log + strategies.updated_at + `params_hash` 對照）
- [ ] ✅ 驗收：trader 可改策略參數並重跑回測**用比較檢視對比**；admin 可管理使用者；paper 盤中看到浮動損益

### Phase 4 — 券商整合準備 + 收尾（視國泰 API 支援度，2–4 週）

- [ ] broker adapter 介面 + DryRunBroker（正式化 paper 下單流程）
- [ ] 確認國泰 API 形態（REST / SDK / Windows-only）→ 實作 CathayBroker
- [ ] 訂單審批佇列 + 二次驗證 + audit log
- [ ] 實單 dry-run 對比（paper vs 實單滑價差異）
- [ ] ✅ 驗收：審批流程完整、dry-run 數週與 paper 偏差 < 設定門檻；**未達標不開實單**（沿用「6 個月 paper 驗證」原則）

### 關鍵決策點（動工前確認）

1. **repo 位置**：新 repo `tw_stocker-web` 與 tw_stocker 分離（推薦，Worker/D1/前端不相關，且 tw_stocker 上游已轉型資料庫存庫不宜混入）— 或收進 `tw_stocker/web/` 子目錄（VPS 上 clone 較方便）
2. **Access 登入範圍**：全站都需登入（推薦，私人交易資料）— 或公開部分頁面（日報可公開）
3. **國泰 API 確認**：Phase 4 前先查國泰證券官方程式化交易 API 文件（是否有 REST/WebSocket、憑證申請流程），避免 adapter 設計落空
4. **回測 log tail 是否要即時**（Phase 2）：要即時 log 需保留隧道 proxy polling（成本低）；只接受「完成後下載 log 檔」可再省一層

---

## 附錄 A：與既有系統的關係（不破壞原則）

| 既有元件 | 角色 | Web UI 介入 |
|---|---|---|
| GH Actions `update_ai_report.yml` | 每日回測 + paper 更新 + commit | **不動**；產物被 sync cron 讀取 |
| Telegram 日報 cron（18:10） | 推播 | **不動**；Web 顯示同一資料 |
| `paper_equity.json` | paper 狀態 source of truth | 只讀 + mirror 進 D1（正規化三表） |
| `ai_report.py` CLI | 回測引擎 | 由 VPS FastAPI 以 subprocess 調用（同參數、同輸出） |
| `strategy/` 模組 | 策略實作 | registry 包裝，參數經 D1 管理 |
| `tw_stock_names.json` | 代碼→中文名 | 前端元件共用（同步一份到 Worker KV 或 build 內嵌） |

## 附錄 B：技術棧總結

| 層 | 技術 | 備註 |
|---|---|---|
| 認證 | Cloudflare Zero Trust Access | Google/GitHub OAuth + email OTP 備援；JWT 注入 header；KV 快取 JWKS；靜默續期 |
| 前端 | React 19 + Vite + TS + TanStack Table + lightweight-charts | SPA，assets-only 部署 |
| Edge API | Cloudflare Workers（Hono）+ D1 + KV + R2 | 唯讀 + proxy + sync 寫入入口；不運算 |
| 資料庫 | D1（SQLite，正規化 schema） | metadata + mirror；每日備份 R2 |
| 運算 | CX23 VPS + FastAPI + subprocess | ai_report.py / paper_tracker.py；/health |
| 內網 | cloudflared Tunnel + Access Service Token | 雙向認證，VPS 不開公網 |
| 排程 | 既有 GH Actions + VPS cron + Worker cron trigger | sync 進 D1（VPS→Worker 單向）；每日備份 |
| 券商 | BrokerAdapter 介面（Phase 4） | 國泰為首個實作目標 |

## 附錄 C：審查問題處置表（9 項，v1 → v2 對照）

| # | 嚴重度 | 問題 | 判定 | 修正方案（v2 落點） | 工作量 |
|---|---|---|---|---|---|
| 1 | P0 | VPS → Worker sync 方向搞反 | **部分成立**：§3.1/§5.2/§6.3 文字方向其實正確（VPS 呼叫 Worker API），但 §1 架構圖「同步 snapshot 到 D1」未標明經 Worker，易誤讀為 VPS 直寫 D1 | 明確畫出「VPS →（隧道）→ Worker → D1」序列 + 方向鐵則 + sync.py 指數退避重試與告警（§1、§3.1、§5.2） | 0.5 天 |
| 2 | P0 | JWT 驗證太重（per-request JWKS fetch） | **成立**：v1 §6.1 每請求 fetch JWKS，免費方案 10ms CPU 風險 | JWKS 存 KV（TTL 6h，kid miss 才 refetch）+ 驗證結果快取 KV（60s TTL，吃下輪詢流量）+ Web Crypto Ed25519（§6.1）；前端輪詢改讀 D1 後驗證次數也下降 | 1.0 天 |
| 3 | P0 | 回測 job 狀態同步單向（前端輪詢 VPS） | **成立**：v1 前端經隧道 proxy 輪詢 VPS，隧道中斷即狀態失聯；且結果只在完成時寫 D1 | VPS 狀態機每轉移主動 push 到 Worker `/api/vps/sync/backtest`（冪等 upsert）；前端輪詢 Worker 讀 D1；log tail 才走隧道（§3.1、§3.3、Phase 2） | 1.5 天 |
| 4 | P1 | D1 JSON 欄位太大（positions_json） | **成立**：完整持倉/成交塞單欄有單列大小與查詢限制風險 | 正規化三表：`paper_snapshots`（摘要）+ `paper_positions` + `paper_trades`（每列 <1KB）；原始 JSON 選配存 R2（§4） | 2.0 天 |
| 5 | P1 | 認證缺 session refresh（JWT 1h 重登） | **成立**：v1 §6.4 明說不做 session cookie | Access session 設 30 天 + 401 → Access callback 靜默續期 + Worker get-identity fallback（§6.2、Phase 1） | 1.0 天 |
| 6 | P1 | 回測佇列無優先序 | **成立**：佇列 FIFO 無法區分輕重 | `backtest_runs.priority`（0–2）+ VPS 佇列 `(priority DESC, created_at ASC)` 排序；trader 鎖 0–1、admin 可 2（§3.2、§3.3、§4） | 0.5 天 |
| 7 | P2 | VPS 缺 /health | **成立**（低成本高價值） | VPS `GET /health`（version/uptime/queue/mem/disk/last_sync）+ Worker `/api/system/health` 聚合 + `/admin` 顯示（§3.3、Phase 2/3） | 0.5 天 |
| 8 | P2 | D1 缺 backup 策略 | **成立**（便宜安全網） | Worker cron trigger 每日全表 → R2 `backups/d1/YYYY-MM-DD.json`（保留 30 天）+ migration 前 `wrangler d1 export` 快照 + 備份時間告警（§5.4、Phase 1） | 1.0 天 |
| 9 | P2 | 回測結果缺版本控制 | **成立**（與 Phase 3 參數版本化互補） | `params_hash`（sha256 of strategy+canonical params）+ `/api/backtest/compare` 並排指標/疊圖/參數 diff + 同參數重跑偵測（§3.4、§4、Phase 2） | 1.0 天 |
| | | **合計** | 9 項全數納入修正（P0×3、P1×3、P2×3 全採） | v1 優點保留：Workers 輕量化、D1 mirror 單向、VPS 運算、Zero Trust、增量上線、Phase 驗收制 | **+8.0 工作天（≈1.5–2.5 週）** |

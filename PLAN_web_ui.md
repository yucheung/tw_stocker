# tw_stocker Web UI 架構規劃

> 版本：v1.0（2026-08-16）
> 系統：tw_stocker v8.5（量化版 commit `944b24fe`）＋ 多策略模組（Momentum / Sector Rotation / Meal Money）
> 目標：在既有 CLI + Telegram 日報之上，建立多使用者 Web UI，支援回測、Paper Trading 追蹤、多策略管理，並為未來國泰證券 API 串接預留擴展點。

---

## 0. 設計前提（先釐清再動工）

| 事實 | 影響 |
|---|---|
| 核心運算（yfinance 下載、回測、訊號產生、paper 更新）是 **Python + pandas**，單次全池回測 2–3 分鐘、佔記憶體 <1GB | **不能在 Cloudflare Workers 跑**（免費方案 CPU 10ms 限制）；必須留在 CX23 VPS |
| Cloudflare Workers 免費方案：100k req/day、D1 5GB / 5M read/day、subrequest 50 個 | Workers 只放「輕量 API + 靜態前端 + 認證閘門」；重運算全部 proxy 到 VPS |
| paper 狀態目前是 repo 內 `paper_equity.json`（GH Actions 每日 17:17 TW 自動更新並 commit） | **single source of truth 維持在 VPS/Repo**；D1 只當「顯示用 mirror」，避免雙寫衝突 |
| GH Actions 每日自動跑、Telegram 18:10 日報 cron 已穩定運作 | Web UI 是**增量**，不取代既有管線；Phase 1 先做唯讀鏡像即可上線 |
| 已有 guanlan-rss-reader 的 Workers + D1 部署經驗（wrangler + API token + D1 migration 流程） | 部署流程直接沿用既有成功模式 |

---

## 1. 系統架構圖（文字）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 使用者瀏覽器（桌機/手機）                                            │
│    │                                                               │
│    ▼                                                               │
│ ① Cloudflare Zero Trust Access  ←── Google / GitHub OAuth SSO      │
│    （domain 層認證牆，發 CF-Access-JWT-Assertion）                   │
│    ▼                                                               │
│ ② Cloudflare Workers（tw-stocker-web）                             │
│    ├─ 靜態 SPA（React + Vite build 產物，assets 直接服務）          │
│    ├─ /api/* 輕量端點：auth 檢查、D1 CRUD、日報/訊號/paper 唯讀     │
│    └─ /api/vps/* proxy：帶 Access Service Token 轉發到 VPS 隧道    │
│    │                                                               │
│    ├──► ③ D1 資料庫（tw-stocker-db）                               │
│    │     users / strategies / backtest_runs / paper mirror /       │
│    │     signals / audit_log                                        │
│    │                                                               │
│    └──► ④ Cloudflare Tunnel（cloudflared，私有 hostname）          │
│           │  Service Token 驗證（Worker↔VPS 雙向）                 │
│           ▼                                                        │
│ ⑤ CX23 VPS（3.7GB RAM）                                            │
│    ├─ FastAPI（uvicorn + systemd）                                  │
│    │    ├─ POST /backtest/jobs      → 子程序跑 ai_report.py        │
│    │    ├─ GET  /backtest/jobs/{id} → 狀態輪詢 + 結果               │
│    │    ├─ GET  /paper/live         → yfinance 即時報價重算        │
│    │    ├─ GET  /paper/state        → paper_equity.json 即時讀取   │
│    │    ├─ CRUD /strategies         → 策略參數管理                 │
│    │    └─ /broker/*                → 券商 adapter（Phase 4）      │
│    ├─ tw_stocker 核心（ai_report.py / paper_tracker.py / strategy/）│
│    └─ cron：每日收盤後 paper_tracker 更新 → 同步 snapshot 到 D1    │
│                                                                    │
│ ⑥ GitHub Actions（既有 update_ai_report.yml，不變）                 │
│    └─ 每日 17:17 回測 + paper 更新 + commit 回 repo                │
└─────────────────────────────────────────────────────────────────────┘
```

**資料流摘要**

- **唯讀路徑（快）**：瀏覽器 → Access → Worker → D1（日報、歷史訊號、paper 歷史、回測結果清單）
- **運算路徑（慢）**：瀏覽器 → Access → Worker → 隧道 → VPS FastAPI → 子程序跑 Python 核心 → 結果寫 D1 → 前端輪詢
- **每日同步路徑**：GH Actions / VPS cron 更新 `paper_equity.json` → VPS sync 腳本推 snapshot 到 Worker API → 寫入 D1 mirror

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
| **回測** | `/backtest` | 參數表單（見 §3.2）、提交任務、進度輪詢、結果圖表（權益曲線/回撤/損益分布）、歷史任務清單與對比 | VPS job + D1 `backtest_runs` |
| **策略管理** | `/strategies` | 策略列表（啟用/停用）、參數編輯（版本化）、universe 設定、策略說明文件 | D1 `strategies` |
| **訊號日報** | `/signals` | 歷史日報瀏覽（對應 repo 內 `stock_report.html` 內容）、月報 | D1 `signals` |
| **管理**（admin only） | `/admin` | 使用者角色管理、系統狀態（VPS 健康度、上次同步時間、GH Actions run 狀態）、audit log | D1 `users` + Worker `/api/system` |
| **券商**（Phase 4） | `/broker` | 訂單審批佇列、下單紀錄、連線狀態（先 dry-run） | VPS `/broker/*` |

### 共用元件

- `Layout`（側欄 + 頂欄 + 手機底部導覽，沿用 guanlan 響應式斷點經驗）
- `AuthProvider`（讀 `CF-Access-JWT-Assertion` → `/api/me` 拿 email/role）
- `ChartCard`（權益曲線、回撤、drawdown 圖）
- `DataTable`（排序/分頁/匯出 CSV）
- `JobProgress`（回測任務進度條 + 日誌 tail）
- `StockNameTag`（代碼 → 中文名，用既有 `tw_stock_names.json` 對照）

---

## 3. 後端 API 設計

### 3.1 Worker 端點（Hono/Itty Router，直接讀 D1）

| Method | Path | 功能 | 權限 |
|---|---|---|---|
| GET | `/api/me` | 回傳 Access JWT 內的 email + D1 角色 | 任何已登入者 |
| GET | `/api/dashboard` | 今日訊號 + 權益摘要（一個查詢組裝） | 已登入 |
| GET | `/api/signals?date=&limit=` | 歷史訊號日報 | 已登入 |
| GET | `/api/paper/snapshot` | paper mirror 最新快照（positions/trades/equity） | 已登入 |
| GET | `/api/paper/equity?range=` | 權益曲線序列 | 已登入 |
| GET | `/api/backtest/runs?strategy=&status=` | 回測任務清單（metadata + 結果摘要） | 已登入 |
| GET | `/api/backtest/runs/{id}` | 單一任務詳情 + 結果 JSON（存 D1 的摘要部分） | 已登入 |
| GET | `/api/strategies` | 策略清單（含啟用狀態） | 已登入 |
| GET | `/api/system/health` | VPS 隧道健康度、上次 sync 時間戳 | 已登入 |
| **→ VPS proxy（以下全部經隧道 + Service Token）** | | | |
| POST | `/api/vps/backtest/jobs` | 提交回測任務 | admin |
| GET | `/api/vps/backtest/jobs/{id}` | 任務狀態/進度/結果 | 已登入 |
| POST | `/api/vps/backtest/jobs/{id}/cancel` | 取消任務 | admin |
| POST | `/api/vps/paper/refresh` | 觸發即時報價重算 | 已登入 |
| GET | `/api/vps/paper/positions/live` | 持倉含浮動損益（yfinance 即時） | 已登入 |
| PUT | `/api/vps/strategies/{id}` | 更新策略參數（validate + 重啟生效） | admin |
| POST | `/api/vps/strategies/{id}/activate` / `deactivate` | 啟停策略 | admin |
| POST | `/api/vps/sync/paper` | 由 VPS cron 呼叫：推 paper snapshot 進 D1 | Service Token |
| POST | `/api/vps/sync/report` | 由 GH Actions / VPS cron 呼叫：推日報摘要進 D1 | Service Token |
| POST | `/api/vps/broker/orders`（Phase 4） | 下單請求（先進審批佇列） | admin + 雙重確認 |
| GET | `/api/vps/broker/status`（Phase 4） | 券商連線/憑證狀態 | admin |

### 3.2 回測任務參數（VPS 端點 body）

對應 `ai_report.py` 既有 80+ CLI 參數，**Web 只暴露策展子集**（避免 UI 爆炸），完整參數留 `advanced_json` 逃生門：

```jsonc
{
  "strategy": "momentum_v85",          // 對應 strategy 註冊表
  "name": "TP4.0/SL3.0 基線",           // 任務命名
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

VPS 收到後：寫入 D1 `backtest_runs`（status=running）→ 以 `subprocess` 跑
`python3 ai_report.py <params>`（log 檔 per-job）→ 完成後解析輸出（年化/Sharpe/MDD/勝率/PF）寫回 D1 → 完整結果（equity CSV、trades）存 VPS `artifacts/runs/{job_id}/`，D1 只存摘要 + 下載連結。

### 3.3 VPS FastAPI 內部結構

```
vps_api/
├── main.py            # FastAPI app、Service Token middleware、router 掛載
├── auth.py            # Access Service Token 驗證（Worker 帶的 CF-Access-Client-Secret）
├── backtest.py        # 任務佇列（asyncio + subprocess）、狀態機 running→done|failed|cancelled
├── paper.py           # paper_equity.json 讀取、yfinance 即時重算、snapshot 產出
├── strategies.py      # 策略 registry 包裝（strategy/ 模組 + 參數 schema）
├── sync.py            # D1 push（呼叫 Worker /api/vps/sync/*）
├── broker/            # Phase 4：adapter 介面 + cathay.py 實作（先 dry-run）
└── jobs/              # 每個 backtest run 的 log / 結果檔
```

**並行限制**：3.7GB RAM 上同時最多 2 個回測（yfinance 下載 + pandas 約 <1GB/run）；佇列一次跑一個、其餘排隊（queue 上限 5，超出回 429）。

---

## 4. 資料庫 Schema（D1）

> D1 只存**顯示與管理所需**資料；`paper_equity.json`、回測完整結果留在 VPS/Repo 為 source of truth。

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
  params_json   TEXT NOT NULL DEFAULT '{}',  -- 當前參數（對應 ai_report CLI 子集）
  schema_json   TEXT NOT NULL DEFAULT '{}',  -- 參數 schema（前端表單自動產生）
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 回測任務
CREATE TABLE backtest_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_key  TEXT NOT NULL REFERENCES strategies(key),
  name          TEXT,
  status        TEXT NOT NULL DEFAULT 'queued', -- queued|running|done|failed|cancelled
  params_json   TEXT NOT NULL,
  result_json   TEXT,                   -- 摘要：年化/Sharpe/MDD/勝率/PF/trades 數
  job_id        TEXT,                   -- VPS 端任務 id（log 檔對應）
  requested_by  TEXT NOT NULL,          -- 使用者 email
  error         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  started_at    TEXT,
  finished_at   TEXT
);
CREATE INDEX idx_backtest_status ON backtest_runs(status, created_at);

-- paper mirror（VPS/Repo 的 paper_equity.json 定期推入）
CREATE TABLE paper_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,          -- YYYY-MM-DD
  equity        REAL NOT NULL,          -- 總權益（最後一筆 equity_curve）
  capital       REAL NOT NULL,
  n_positions   INTEGER NOT NULL,
  positions_json TEXT NOT NULL,         -- 完整持倉（含 TP/SL/浮動）
  trades_json   TEXT,                   -- closed_trades（當日變動）
  signals_json  TEXT,                   -- daily_signals
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(snapshot_date)
);

-- 訊號日報 mirror（每日 Top-7 + 中文名，供 /signals 瀏覽）
CREATE TABLE signals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_date   TEXT NOT NULL,
  tickers_json  TEXT NOT NULL,          -- [{"code":"3231","name":"緯創"}, ...]
  report_url    TEXT,                   -- repo 內 stock_report.html 連結
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(signal_date)
);

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
-- 種子資料：last_paper_sync_at、last_report_sync_at、system_version 等
```

**遷移管理**：沿用 guanlan 模式 — `wrangler d1 execute tw-stocker-db --file migrations/0001_init.sql --remote`，每個 migration 一個檔、按序執行。

---

## 5. 部署流程

### 5.1 倉庫結構（新 repo：`yucheung/tw_stocker-web`）

```
tw_stocker-web/
├── web/                    # React + Vite SPA
│   ├── src/pages/          # §2 頁面
│   └── vite.config.ts
├── worker/                 # Cloudflare Worker（Hono）
│   ├── src/index.ts        # 路由 + Access JWT 驗證 + D1 + VPS proxy
│   ├── src/db.ts           # D1 queries
│   └── src/auth.ts         # JWT 驗證 + Service Token
├── migrations/             # 0001_init.sql, 0002_*.sql ...
├── vps_api/                # VPS FastAPI（§3.3）
├── wrangler.jsonc          # 主 config（D1 binding `DB`）
├── package.json            # worker + web 共用 workspace
└── deploy.sh               # build → dedupe bindings → wrangler deploy（沿用 guanlan 修正模式）
```

### 5.2 部署步驟

**Cloudflare 側（一次設定）**
1. `npx wrangler d1 create tw-stocker-db` → 記下 `database_id` 寫入 `wrangler.jsonc`
2. `wrangler d1 execute tw-stocker-db --file migrations/0001_init.sql --remote`（依序每個 migration）
3. Zero Trust → Applications → 新增 **Self-hosted**：public hostname = `tw-stocker-web.yucheung.workers.dev`
   - Policy：允許 Google/GitHub SSO（Zero Trust → Settings → Authentication → Add Google/GitHub，填入 OAuth client）
   - Session duration 預設；Phase 3 再細分 policy（admin group）
4. Zero Trust → Access → Service Tokens：建立 `tw-stocker-vps`（Worker 呼叫 VPS 用）與 `tw-stocker-web`（VPS 呼叫 Worker 用），secret 存兩端
5. 部署：`CLOUDFLARE_API_TOKEN`（已在 `~/.hermes/.env`）→ `bash deploy.sh`

**VPS 側（CX23）**
1. `git clone https://github.com/yucheung/tw_stocker-web`（或直接收進既有 repo 的 `web/` 子目錄 — 見 §8 決策）
2. `python3 -m venv .venv && pip install -r requirements.txt fastapi uvicorn httpx`
3. `cloudflared tunnel create tw-stocker` → 設 `config.yml` 指到 `http://localhost:8100` → DNS route 私有 hostname（`tw-stocker-vps.yucheung.workers.dev`，**只允許 Service Token 存取**）
4. systemd unit：`tw-stocker-api.service`（uvicorn，`--workers 1`，8100 port）
5. cron（沿用既有 Telegram cron 模式）：每日收盤後跑 `paper_tracker.py` 更新 → `sync.py` 推 snapshot 到 Worker `/api/vps/sync/paper`

**CI/CD**
- GitHub Actions `ci.yml`：`npm ci && npm run build`（web + worker）→（可選）`wrangler deploy`
- 與既有 `update_ai_report.yml` 完全獨立，互不干擾

### 5.3 免費額度檢核

| 資源 | 用量估算 | 額度 | 餘裕 |
|---|---|---|---|
| Workers 請求 | 個人/小團隊 < 5k/day | 100k/day | ✅ |
| D1 儲存 | 純 metadata + mirror < 100MB | 5GB | ✅ |
| D1 read | 每頁 2–5 query × 200 次/日 | 5M/day | ✅ |
| Workers CPU | 全走 D1 或 proxy，< 5ms/req | 10ms/req | ✅（**切勿**在 Worker 內做運算） |
| VPS RAM | 1 回測 <1GB、並行上限 2 | 3.7GB | ✅ |

---

## 6. 認證流程（SSO + Zero Trust）

### 6.1 外層：Zero Trust Access（domain 閘門）

```
1. 未登入訪客 → https://tw-stocker-web.yucheung.workers.dev
2. Access 302 → 登入選擇頁（Google / GitHub / 備援 OTP email）
3. 使用者完成 OAuth → Access 發 session cookie（Cf_Authorization）
4. 後續請求帶 cookie → Access 驗證通過 → 注入 header：
   CF-Access-JWT-Assertion: <JWT>
   （JWT claims：email、identity_nonce、exp、common_name）
5. Worker 收到請求，先驗證 JWT：
   - 用 JWKS（https://<team>.cloudflareaccess.com/cdn-cgi/access/certs）
   - 驗簽名 + aud（Access Application AUD tag）+ exp
   - 成功 → email 進 request context
```

### 6.2 內層：角色授權（D1 users 表）

- 首次登入：`users` 表無此 email → 自動建立 `viewer` 角色（可看不可操作）
- admin 在 `/admin` 提升角色：`viewer`（唯讀）→ `trader`（可跑回測/paper 操作）→ `admin`（策略/使用者/券商）
- Worker 每個 API 依 route 檢查角色（§3.1 權限欄）
- **Policy 進階選項**（Phase 3）：Zero Trust Access 可依群組細分 — Google Workspace group / GitHub team → Access policy 直接給 admin group 較嚴格 session 或 MFA

### 6.3 VPS 間認證（Service Token，雙向）

```
Worker → VPS：  請求帶 CF-Access-Client-Id + CF-Access-Client-Secret
                VPS middleware 驗證（cloudflared 隧道 + Access 會先擋一層）
VPS  → Worker： sync 腳本帶 tw-stocker-web service token 呼叫 /api/vps/sync/*
```

### 6.4 安全注意事項

- VPS FastAPI **不綁公開 IP/port**（只從 tunnel 進），防火牆擋 8100 對外
- Access JWT 過期（預設 1h）→ 前端 401 → 重新導向 Access 登入；SPA 不做自己的 session cookie（Phase 3 若要 remember-me 再評估）
- CORS：SPA 與 API 同域（Worker assets + routes），無跨域問題；VPS 只接受 Worker 轉發，不直接暴露給瀏覽器
- audit_log 記錄所有寫操作（回測提交、策略變更、下單請求）

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

---

## 8. 開發順序（Phase 1–4）

> 每個 Phase 有明確驗收標準，獨立可上線。建議 Phase 1 一週內完成。

### Phase 1 — 地基：認證 + 唯讀儀表板（0.5–1 週）

**目標**：能登入、能看到 paper 現況與日報 — 立即有價值

- [ ] 建 repo `tw_stocker-web`（SPA 骨架 + Worker + D1 migrations）
- [ ] Zero Trust Access 設定（Google/GitHub SSO + 既有 email OTP 備援）
- [ ] Worker：Access JWT 驗證 + `/api/me` + users 表自動建立
- [ ] D1 migration 0001（全部表）
- [ ] 頁面：Dashboard、Paper（唯讀 mirror）、Signals
- [ ] VPS sync cron：每日推 `paper_equity.json` snapshot + 訊號進 D1（先吃 GH Actions 產物，**不動既有管線**）
- [ ] ✅ 驗收：登入 → 看到今日訊號 + paper 權益曲線；`curl` 無 token 被 302/401

### Phase 2 — 回測引擎上線（1–2 週）

**目標**：Web 上能跑回測、看結果

- [ ] VPS FastAPI 骨架 + cloudflared 隧道 + Service Token 雙向驗證
- [ ] 回測 job 佇列（subprocess 包 `ai_report.py`、per-job log、取消）
- [ ] `/backtest` 頁面：參數表單（§3.2 子集）、進度輪詢、結果圖表
- [ ] 結果摘要寫 D1 `backtest_runs` + 完整結果存 VPS artifacts
- [ ] ✅ 驗收：Web 提交全池回測 → 2–3 分鐘 → 圖表 + 指標摘要；與 CLI 結果一致

### Phase 3 — Paper 即時化 + 策略管理（1–2 週）

**目標**：可操作、可管理多策略

- [ ] `/paper` 即時報價重算（VPS `/paper/live`，yfinance 盤中/盤後）
- [ ] 手動觸發 paper 更新（取代只靠每日 cron）
- [ ] `/strategies` 頁面 + 策略 registry（Momentum / Sector Rotation / Meal Money 註冊）
- [ ] 角色權限分級（viewer/trader/admin）+ `/admin` 使用者管理
- [ ] 參數變更版本化（audit_log + strategies.updated_at）
- [ ] ✅ 驗收：trader 可改策略參數並重跑回測對比；admin 可管理使用者；paper 盤中看到浮動損益

### Phase 4 — 券商整合準備 + 收尾（視國泰 API 支援度，2–4 週）

**目標**：實盤前的最後一哩

- [ ] broker adapter 介面 + DryRunBroker（正式化 paper 下單流程）
- [ ] 確認國泰 API 形態（REST / SDK / Windows-only）→ 實作 CathayBroker
- [ ] 訂單審批佇列 + 二次驗證 + audit log
- [ ] 實單 dry-run 對比（paper vs 實單滑價差異）
- [ ] ✅ 驗收：審批流程完整、dry-run 數週與 paper 偏差 < 設定門檻；**未達標不開實單**（沿用「6 個月 paper 驗證」原則）

### 關鍵決策點（動工前確認）

1. **repo 位置**：新 repo `tw_stocker-web` 與 tw_stocker 分離（推薦，Worker/D1/前端不相關，且 tw_stocker 上游已轉型資料庫存庫不宜混入）— 或收進 `tw_stocker/web/` 子目錄（VPS 上 clone 較方便）
2. **Access 登入範圍**：全站都需登入（推薦，私人交易資料）— 或公開部分頁面（日報可公開）
3. **國泰 API 確認**：Phase 4 前先查國泰證券官方程式化交易 API 文件（是否有 REST/WebSocket、憑證申請流程），避免 adapter 設計落空

---

## 附錄 A：與既有系統的關係（不破壞原則）

| 既有元件 | 角色 | Web UI 介入 |
|---|---|---|
| GH Actions `update_ai_report.yml` | 每日回測 + paper 更新 + commit | **不動**；產物被 sync cron 讀取 |
| Telegram 日報 cron（18:10） | 推播 | **不動**；Web 顯示同一資料 |
| `paper_equity.json` | paper 狀態 source of truth | 只讀 + mirror 進 D1 |
| `ai_report.py` CLI | 回測引擎 | 由 VPS FastAPI 以 subprocess 調用（同參數、同輸出） |
| `strategy/` 模組 | 策略實作 | registry 包裝，參數經 D1 管理 |
| `tw_stock_names.json` | 代碼→中文名 | 前端元件共用（同步一份到 Worker KV 或 build 內嵌） |

## 附錄 B：技術棧總結

| 層 | 技術 | 備註 |
|---|---|---|
| 認證 | Cloudflare Zero Trust Access | Google/GitHub OAuth + email OTP 備援；JWT 注入 header |
| 前端 | React 19 + Vite + TS + TanStack Table + lightweight-charts | SPA，assets-only 部署 |
| Edge API | Cloudflare Workers（Hono）+ D1 | 唯讀 + proxy；不運算 |
| 資料庫 | D1（SQLite） | metadata + mirror |
| 運算 | CX23 VPS + FastAPI + subprocess | ai_report.py / paper_tracker.py |
| 內網 | cloudflared Tunnel + Access Service Token | 雙向認證，VPS 不開公網 |
| 排程 | 既有 GH Actions + VPS cron | sync 進 D1 |
| 券商 | BrokerAdapter 介面（Phase 4） | 國泰為首個實作目標 |

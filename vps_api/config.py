"""
tw_stocker VPS API — 共用設定

集中管理所有環境變數的讀取與預設值，避免各模組重複解析。
所有敏感值（Service Token）一律從環境變數讀取，不寫死在程式碼。

環境變數一覽：
  TW_STOCKER_ROOT              tw_stocker 核心 repo 根目錄（ai_report.py / paper_equity.json 所在）
  VPS_API_VERSION              健康檢查回報的版本字串
  VPS_API_PORT                 uvicorn 監聽 port（預設 8100，僅從 cloudflared 隧道進入）
  CF_ACCESS_CLIENT_ID          入站驗證：Worker → VPS 的 Service Token Client ID
  CF_ACCESS_CLIENT_SECRET      入站驗證：Worker → VPS 的 Service Token Secret
  SYNC_CF_ACCESS_CLIENT_ID     （選用）出站推送用 Client ID，缺省時沿用 CF_ACCESS_CLIENT_ID
  SYNC_CF_ACCESS_CLIENT_SECRET （選用）出站推送用 Secret，缺省時沿用 CF_ACCESS_CLIENT_SECRET
  WORKER_URL                   出站推送目標 Worker 根網址（如 https://tw-stocker-web.yucheung.workers.dev）
  AI_REPORT_SCRIPT             回測子程序腳本路徑（預設 {TW_STOCKER_ROOT}/ai_report.py）
  CORS_ORIGINS                 允許的 CORS origins，逗號分隔（預設 *，VPS 僅經隧道存取）
  AUTH_DISABLED                設為 1 時停用 Service Token 驗證（僅限本機開發，預設停用此逃生門）
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    """讀取環境變數並轉為布林（'1'/'true'/'yes' 為真）。"""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- 路徑 ---
TW_STOCKER_ROOT = Path(os.getenv("TW_STOCKER_ROOT", "/root/work/tw_stocker")).resolve()
VPS_API_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.getenv("JOBS_DIR", str(VPS_API_DIR / "jobs"))).resolve()   # 每個 backtest run 的 log / 結果檔
PAPER_FILE = TW_STOCKER_ROOT / "paper_equity.json"   # paper 狀態 source of truth（repo 內）

# 回測子程序腳本（可用環境變數覆寫，便於測試）
AI_REPORT_SCRIPT = os.getenv("AI_REPORT_SCRIPT", str(TW_STOCKER_ROOT / "ai_report.py"))

# --- 版本 ---
VERSION = os.getenv("VPS_API_VERSION", "0.1.0")

# --- 入站 Service Token（Worker → VPS 驗證用） ---
CF_ACCESS_CLIENT_ID = os.getenv("CF_ACCESS_CLIENT_ID", "")
CF_ACCESS_CLIENT_SECRET = os.getenv("CF_ACCESS_CLIENT_SECRET", "")

# --- 出站推送 Service Token（VPS → Worker 用；可與入站不同 token） ---
SYNC_CLIENT_ID = os.getenv("SYNC_CF_ACCESS_CLIENT_ID", CF_ACCESS_CLIENT_ID)
SYNC_CLIENT_SECRET = os.getenv("SYNC_CF_ACCESS_CLIENT_SECRET", CF_ACCESS_CLIENT_SECRET)

# --- 出站推送目標 Worker ---
WORKER_URL = os.getenv("WORKER_URL", "").rstrip("/")

# --- 其他 ---
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
AUTH_DISABLED = _env_bool("AUTH_DISABLED", default=False)

# 回測佇列參數（3.7GB RAM 限制：並行最多 2、等候佇列上限 5）
MAX_CONCURRENT = int(os.getenv("BACKTEST_MAX_CONCURRENT", "2"))
QUEUE_LIMIT = int(os.getenv("BACKTEST_QUEUE_LIMIT", "5"))


def ensure_dirs() -> None:
    """確保執行所需目錄存在。"""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

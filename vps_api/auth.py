"""
Service Token 驗證（CF-Access-Client-Id / CF-Access-Client-Secret）

架構角色（見 PLAN_web_ui_v2.md §6.3）：
  - Worker → VPS：請求帶 tw-stocker-vps Service Token，VPS 以本模組驗證
  - VPS 只從 cloudflared 隧道進入（不綁公開 IP），此處是第二道驗證

安全要求：
  - 使用 secrets.compare_digest 做常數時間比較，避免 timing attack
  - Token 未設定（環境變數缺失）時 fail-closed：直接 503，拒絕所有請求
  - AUTH_DISABLED=1 僅供本機開發除錯，production 不得開啟
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from . import config

# Cloudflare Access Service Token 慣用 header 名稱
HEADER_CLIENT_ID = "CF-Access-Client-Id"
HEADER_CLIENT_SECRET = "CF-Access-Client-Secret"


def _configured() -> bool:
    """檢查入站驗證用的 Service Token 是否已設定。"""
    return bool(config.CF_ACCESS_CLIENT_ID and config.CF_ACCESS_CLIENT_SECRET)


def verify_service_token(request: Request) -> str:
    """
    FastAPI dependency：驗證請求是否攜帶正確的 Service Token。

    成功回傳 client_id（供 audit log 使用）；失敗拋出 HTTPException。
    """
    if config.AUTH_DISABLED:
        # 僅限本機開發的逃生門（預設關閉）
        return "dev-local"

    if not _configured():
        # fail-closed：Token 未設定代表服務未正確部署，拒絕一切請求
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service Token 未設定（CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET）",
        )

    client_id = request.headers.get(HEADER_CLIENT_ID, "")
    client_secret = request.headers.get(HEADER_CLIENT_SECRET, "")

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Service Token header（CF-Access-Client-Id / CF-Access-Client-Secret）",
            headers={"WWW-Authenticate": "Bearer"},
        )

    id_ok = secrets.compare_digest(client_id, config.CF_ACCESS_CLIENT_ID)
    secret_ok = secrets.compare_digest(client_secret, config.CF_ACCESS_CLIENT_SECRET)

    if not (id_ok and secret_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service Token 驗證失敗",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return client_id

"""
tw_stocker VPS FastAPI 服務（回測引擎後端）

啟動方式（VPS，systemd unit 建議；需以套件方式載入，勿直接 cd 進 vps_api）：
    cd /root/work/tw_stocker
    .venv/bin/uvicorn vps_api.main:app --host 127.0.0.1 --port 8100 --workers 1

環境變數（見 config.py）：CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET / WORKER_URL
架構對應 PLAN_web_ui_v2.md §3.3：VPS 只從 cloudflared 隧道進入，不綁公開 IP。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth, backtest, config, health, paper, strategies, sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("vps_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動：確保目錄、初始化 sync client、啟動回測 worker；關閉：依序清理。"""
    config.ensure_dirs()
    await sync.init()
    await backtest.start()
    logger.info("tw_stocker VPS API v%s 啟動完成（WORKER_URL=%s, sync=%s）",
                config.VERSION, config.WORKER_URL or "(未設定)", "啟用" if sync.get_client().enabled else "停用")
    try:
        yield
    finally:
        logger.info("關閉中：停止回測 worker…")
        await backtest.stop()
        await sync.close()


def create_app() -> FastAPI:
    """建立 FastAPI app（供 uvicorn / 測試使用）。"""
    app = FastAPI(
        title="tw_stocker VPS API",
        version=config.VERSION,
        description="tw_stocker 回測引擎後端（Service Token 保護，僅經 cloudflared 隧道存取）",
        lifespan=lifespan,
    )

    # CORS：VPS 僅接受 Worker 轉發（同域），此處寬鬆設定由隧道保護；
    # 如需收緊可用 CORS_ORIGINS 環境變數指定白名單。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 掛載 router（全部套用 Service Token 驗證 dependency）
    app.include_router(health.router, dependencies=[Depends(auth.verify_service_token)])
    app.include_router(backtest.router, dependencies=[Depends(auth.verify_service_token)])
    app.include_router(paper.router, dependencies=[Depends(auth.verify_service_token)])
    app.include_router(strategies.router, dependencies=[Depends(auth.verify_service_token)])

    @app.get("/")
    async def root() -> dict:
        """服務總覽（除錯用）。"""
        return {
            "service": "tw_stocker VPS API",
            "version": config.VERSION,
            "endpoints": ["/health", "/backtest/jobs", "/paper/state", "/paper/live",
                          "/strategies"],
            "auth": "CF-Access-Client-Id / CF-Access-Client-Secret",
        }

    return app


app = create_app()

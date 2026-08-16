"""
GET /health — 健康狀態（供 Worker /api/system/health 聚合顯示，P2-7）

欄位：version / uptime / queue / mem / disk / last_sync
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone

from typing import Any

from fastapi import APIRouter

from . import backtest, config, sync

router = APIRouter(tags=["health"])

# 服務啟動時間（module import 時記錄）
_start_time = time.monotonic()


def _mem_info() -> dict[str, Any]:
    """記憶體使用（MB）。psutil 不可用時回傳 None 值。"""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "used_mb": round(vm.used / 1024 / 1024, 1),
            "total_mb": round(vm.total / 1024 / 1024, 1),
            "percent": round(vm.percent, 1),
        }
    except ImportError:
        return {"used_mb": None, "total_mb": None, "percent": None}


def _disk_info() -> dict[str, Any]:
    """磁碟空間（GB，以 tw_stocker repo 所在 filesystem 為準）。"""
    try:
        usage = shutil.disk_usage(str(config.TW_STOCKER_ROOT))
        return {
            "free_gb": round(usage.free / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1),
            "percent": round(usage.used / usage.total * 100, 1),
        }
    except OSError:
        return {"free_gb": None, "total_gb": None, "percent": None}


@router.get("/health")
async def health() -> dict:
    """回傳服務健康狀態。"""
    uptime_s = int(time.monotonic() - _start_time)
    return {
        "status": "ok",
        "version": config.VERSION,
        "uptime_s": uptime_s,
        "now": datetime.now(timezone.utc).isoformat(),
        "queue": backtest.queue.stats(),
        "memory": _mem_info(),
        "disk": _disk_info(),
        "last_sync": sync.last_sync(),
        "tw_stocker_root": str(config.TW_STOCKER_ROOT),
    }

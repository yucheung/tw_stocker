"""
策略 registry 包裝

對應 tw_stocker 的 strategy/ 模組（momentum / sector_rotation / meal_money），
為 VPS API 提供：
  - 策略中繼資料（中文名、版本、說明、參數 schema）→ 前端表單自動生成
  - 策展參數 → ai_report.py CLI flags 的轉換器（完整參數走 advanced_json 逃生門）

新策略加入流程（見 PLAN_web_ui_v2.md §7.2）：
  1. 在 strategy/ 新增模組
  2. 在本 registry 註冊 key / 中文名 / schema
  3. 無需改前端（schema 驅動表單）
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/strategies", tags=["strategies"])

# ---------------------------------------------------------------------------
# 策略註冊表
# key → 中繼資料 + 參數 schema（前端表單 + 後端驗證共用）
# ---------------------------------------------------------------------------

_MOMENTUM_SCHEMA: dict[str, dict[str, Any]] = {
    # 對應 PLAN_web_ui_v2.md §3.2 策展子集（ai_report.py CLI 的對應 flags 見 _cli_map）
    "pool": {"type": "string", "enum": ["full", "legacy"], "default": "full",
             "description": "動態 Universe 母體：full=TWSE 全體上市普通股, legacy=舊手選 140 檔"},
    "tickers": {"type": "array", "items": {"type": "string"}, "default": None,
                "description": "手動指定股池（null = 動態 universe 全池）"},
    "days": {"type": "integer", "default": 1200, "min": 100, "max": 5000,
             "description": "歷史回測天數"},
    "start_date": {"type": "string", "format": "date", "default": None,
                   "description": "回測起始日期 YYYY-MM-DD（優先於 days）"},
    "end_date": {"type": "string", "format": "date", "default": None,
                 "description": "回測結束日期（預設今天）"},
    "top_k": {"type": "integer", "default": 7, "min": 1, "max": 20,
              "description": "每日最多進場股票數"},
    "capital": {"type": "number", "default": 200000, "min": 10000,
                "description": "初始模擬資金"},
    "tp_sl_mode": {"type": "string", "enum": ["atr", "fixed"], "default": "atr",
                   "description": "TP/SL 模式：atr=ATR倍數, fixed=固定百分比"},
    "tp_atr": {"type": "number", "default": 4.0, "min": 0.5, "max": 10.0,
               "description": "ATR 模式停利倍數"},
    "sl_atr": {"type": "number", "default": 3.0, "min": 0.5, "max": 10.0,
               "description": "ATR 模式停損倍數"},
    "tp": {"type": "number", "default": 0.15, "description": "固定模式停利百分比"},
    "sl": {"type": "number", "default": 0.08, "description": "固定模式停損百分比"},
    "hold_days": {"type": "integer", "default": 20, "min": 1, "max": 120,
                  "description": "最大持倉交易日數"},
    "gap_filter": {"type": "number", "default": 1.5, "min": 0,
                   "description": "跳空過濾 ATR 倍數（0=停用）"},
    "slippage": {"type": "number", "default": 0.001, "min": 0, "max": 0.05,
                 "description": "滑價模型（0.001=10bps）"},
    "regime_filter": {"type": "boolean", "default": True,
                      "description": "大盤過濾（0050 > 60MA 才允許進場）"},
    "position_size": {"type": "number", "default": 0.10, "min": 0.01, "max": 1.0,
                      "description": "每筆倉位佔當前權益比例"},
    "rank_weight": {"type": "boolean", "default": False,
                    "description": "排名加權 sizing"},
    "buy_cost": {"type": "number", "default": 0.001425, "description": "買入手續費率"},
    "sell_cost": {"type": "number", "default": 0.004425, "description": "賣出成本率"},
}

# 策展參數 → ai_report.py CLI flag 對照（key → (flag, 型別處理)）
_CLI_MAP: dict[str, str] = {
    "pool": "--pool",
    "days": "--days",
    "start_date": "--start-date",
    "end_date": "--end-date",
    "top_k": "--top-k",
    "capital": "--capital",
    "tp_sl_mode": "--tp-sl-mode",
    "tp_atr": "--tp-atr",
    "sl_atr": "--sl-atr",
    "tp": "--tp",
    "sl": "--sl",
    "hold_days": "--hold-days",
    "gap_filter": "--gap-filter",
    "slippage": "--slippage",
    "position_size": "--position-size",
    "rank_weight": "--rank-weight",
    "buy_cost": "--buy-cost",
    "sell_cost": "--sell-cost",
}

STRATEGIES: dict[str, dict[str, Any]] = {
    "momentum_v85": {
        "name_zh": "動量策略 v8.5",
        "version": "v8.5",
        "description": "事件驅動動量策略（ai_report.py 主策略，含 regime/ATR/風控模組）",
        "module": "strategy.ai_strategy",
        "script": "ai_report.py",
        "schema": _MOMENTUM_SCHEMA,
        "defaults": {k: v.get("default") for k, v in _MOMENTUM_SCHEMA.items()},
    },
    "sector_rotation_v2": {
        "name_zh": "板塊輪動 v2",
        "version": "v2",
        "description": "Sector Rotation 板塊資金流輪動策略",
        "module": "strategy.sector_rotation_backtest",
        "script": "sector_rotation_report.py",
        "schema": {},          # Phase 3 補齊 schema（前端表單自動生成）
        "defaults": {},
    },
    "meal_money_v2": {
        "name_zh": "便當錢 v2",
        "version": "v2",
        "description": "Meal Money 高勝率短線策略（嚴格停損紀律）",
        "module": "strategy.meal_money",
        "script": "meal_money_report.py",
        "schema": {},          # Phase 3 補齊 schema
        "defaults": {},
    },
}

# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------


def list_strategies() -> list[dict[str, Any]]:
    """回傳策略清單（不含 defaults，供前端列表顯示）。"""
    return [
        {
            "key": key,
            "name_zh": meta["name_zh"],
            "version": meta["version"],
            "description": meta["description"],
            "enabled": True,          # Phase 3 改由 D1 strategies 表管理
            "schema": meta["schema"],
        }
        for key, meta in STRATEGIES.items()
    ]


def get_strategy(key: str) -> Optional[dict[str, Any]]:
    """取得單一策略中繼資料；不存在回傳 None。"""
    return STRATEGIES.get(key)


def validate_params(key: str, params: dict[str, Any]) -> list[str]:
    """
    依 schema 驗證參數（基本型別/枚舉/範圍檢查）。

    回傳錯誤訊息清單；空清單代表通過。advanced_json 不做此驗證（逃生門）。
    """
    meta = STRATEGIES.get(key)
    if meta is None:
        return [f"未知策略: {key}"]
    errors: list[str] = []
    for field, spec in meta["schema"].items():
        if field not in params:
            continue
        val = params[field]
        if val is None:
            continue
        if spec.get("type") in ("number", "integer"):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                errors.append(f"{field}: 需要{'整數' if spec.get('type') == 'integer' else '數字'}")
                continue
            if spec.get("type") == "integer" and not isinstance(val, int):
                errors.append(f"{field}: 需要整數")
            if "min" in spec and val < spec["min"]:
                errors.append(f"{field}: 不得小於 {spec['min']}")
            if "max" in spec and val > spec["max"]:
                errors.append(f"{field}: 不得大於 {spec['max']}")
        elif spec.get("type") == "boolean":
            if not isinstance(val, bool):
                errors.append(f"{field}: 需要布林")
        elif spec.get("type") == "string":
            if not isinstance(val, str):
                errors.append(f"{field}: 需要字串")
            elif "enum" in spec and val not in spec["enum"]:
                errors.append(f"{field}: 必須是 {spec['enum']} 之一")
        elif spec.get("type") == "array":
            if not isinstance(val, list):
                errors.append(f"{field}: 需要陣列")
    return errors


def build_cli_args(key: str, params: dict[str, Any],
                   advanced_json: Optional[dict[str, Any]] = None) -> list[str]:
    """
    把策展 params（+ 選用 advanced_json 逃生門）轉成 ai_report.py CLI args。

    規則：
      - 策展參數走 _CLI_MAP；False 布林旗標省略（argparse store_true 語意）
      - True 布林旗標只加 flag 不加值
      - advanced_json 的 key 直接轉 kebab-case flag（對應 argparse dest）
    """
    args: list[str] = []

    for field, flag in _CLI_MAP.items():
        if field not in params or params[field] is None:
            continue
        val = params[field]
        if isinstance(val, bool):
            if val:
                args.append(flag)
            # False → 省略（對應 argparse store_true / store_false 配對）
            continue
        args.extend([flag, str(val)])

    # tickers 特殊處理（nargs='+'）
    if params.get("tickers"):
        args.append("--tickers")
        args.extend(str(t) for t in params["tickers"])

    # regime_filter 預設 True，關閉時要傳 --no-regime-filter
    if params.get("regime_filter") is False:
        args.append("--no-regime-filter")

    # advanced_json 逃生門：完整 CLI 參數透傳（key → --kebab-case）
    for k, v in (advanced_json or {}).items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                args.append(flag)
        elif isinstance(v, (list, tuple)):
            args.append(flag)
            args.extend(str(x) for x in v)
        else:
            args.extend([flag, str(v)])

    return args


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_strategies_api() -> dict[str, Any]:
    """策略清單（含啟用狀態與參數 schema，供前端表單自動生成）。"""
    return {"strategies": list_strategies()}


@router.get("/{key}")
async def get_strategy_api(key: str) -> dict[str, Any]:
    """單一策略詳情（含 schema 與 defaults）。"""
    meta = get_strategy(key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"未知策略: {key}")
    return {"key": key, **meta}

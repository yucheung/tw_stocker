# Review of SCRIPT_INVENTORY.md

**Reviewer**: Hermes Agent  
**Date**: 2026-08-16  
**Target**: `/root/work/tw_stocker/SCRIPT_INVENTORY.md`

---

## Overall Assessment

The inventory is **well-structured and mostly accurate** in its core descriptions, but has several errors in dependency listings, a duplication, missing directories, and an incorrect total count. The I/O file descriptions and ASCII graphs are largely correct.

---

## Issues Found

### 🔴 CRITICAL: Incorrect Dependencies (2 scripts)

| Script | Inventory Claims | Actual Imports | Verdict |
|---|---|---|---|
| `ablation_study.py` | `strategy/benchmark.py` (line 121) | Does NOT import `strategy/benchmark.py` | **WRONG** |
| `factor_grid_search.py` | `strategy/benchmark.py` (line 134) | Does NOT import `strategy/benchmark.py` | **WRONG** |

Both scripts import `strategy/ai_strategy.py`, `strategy/event_backtest.py`, `strategy/risk_metrics.py`, but **neither imports `strategy/benchmark.py`**. The actual imports are:

- `ablation_study.py`: `strategy.ai_strategy`, `strategy.event_backtest`, `strategy.risk_metrics`, `research.experiment_registry`, `validation.deflated_sharpe`, `validation.pbo_cscv`
- `factor_grid_search.py`: `strategy.ai_strategy`, `strategy.event_backtest`, `strategy.risk_metrics`, `strategy.finlab_factors`, `research.experiment_registry`, `validation.deflated_sharpe`, `validation.pbo_cscv`

### 🟡 DUPLICATION: `strategy/news_sentiment.py` Listed Twice

In the **Strategy Modules** table, `strategy/news_sentiment.py` appears at both line 172 and line 179 with identical content. One should be removed.

### 🟡 MISSING: `vps_api/` Directory (9 files)

The `vps_api/` directory contains 9 Python files not covered by the inventory:
- `vps_api/__init__.py`, `vps_api/main.py`, `vps_api/config.py`, `vps_api/auth.py`
- `vps_api/backtest.py`, `vps_api/paper.py`, `vps_api/strategies.py`
- `vps_api/sync.py`, `vps_api/health.py`

This appears to be a VPS deployment/API layer that should be documented.

### 🟡 MISSING: `sync_to_web.py`

A standalone script at the project root, not covered by the inventory. Likely related to deployment/sync.

### 🟠 MISSING: `__init__.py` files

The inventory doesn't mention:
- `validation/__init__.py`
- `research/__init__.py`

(No `strategy/__init__.py` exists, so that's fine.)

### 🟠 WRONG TOTAL COUNT

The inventory states **42** total Python files. Actual count on disk: **54** files. The discrepancy is explained by the missing `vps_api/` directory (9 files), `sync_to_web.py` (1 file), and 2 `__init__.py` files.

---

## Spot-Check Results (3 scripts)

### ✅ `paper_tracker.py`
- **Functions listed**: All 11 functions confirmed (`load_data`, `save_data`, `get_current_bars`, `recompute_tp_sl`, `get_current_prices`, `extract_signals_from_orders`, `extract_signals_from_report`, `update_tracker`, `generate_html`, `main`). Also has `_opt_float` (private, not listed — acceptable).
- **Dependencies**: Confirmed — stdlib + `pandas`, dynamic `yfinance`.
- **I/O files**: Confirmed — reads `paper_equity.json`, writes `paper_trading.html`.
- **Script deps**: Confirmed — reads from `ai_report.py` outputs.

### ✅ `crisis_test.py`
- **Functions listed**: `run_backtest_range()`, `main()` — confirmed.
- **Dependencies**: Confirmed — `subprocess`, `re`, `sys`, `argparse`, `datetime`.
- **Script deps**: Confirmed — invokes `ai_report.py` via subprocess.
- **I/O**: Correct — console output only.
- **Note**: Inventory says "5 known crisis periods" — actual code defines 5 periods in `CRISIS_PERIODS` dict. ✓

### ✅ `monte_carlo.py`
- **Functions listed**: `get_equity_curve()`, `get_trades()`, `equity_curve_bootstrap()`, `legacy_simulate()`, `main()` — confirmed.
- **Dependencies**: Confirmed — `re`, `sys`, `argparse`, `random`, `statistics`, `csv`, `os`, `glob`, `datetime`, `pandas`, `numpy`.
- **I/O**: Confirmed — reads `artifacts/equity_*.csv` and `artifacts/trades_*.csv`.

### ✅ `deep_crisis_test.py` (bonus check)
- **Functions listed**: All 6 functions confirmed (`run_sr_backtest`, `run_v85_backtest`, `_parse_output`, `get_benchmark_return`, `get_us_indicators`, `main`).
- **Script deps**: Confirmed — calls both `ai_report.py` and `sector_rotation_report.py` via subprocess.

---

## Dependency Graph Accuracy

### Script-level ASCII graph (lines 217-304)
- **Mostly accurate.** The flow from `ai_report.py` → `paper_tracker.py` and `crisis_test.py` is correct.
- `deep_crisis_test.py` → `ai_report.py` + `sector_rotation_report.py` is correct.
- `monte_carlo.py` reading `artifacts/` from `ai_report.py` is correct.
- `meal_money_report.py` and `daily_meal_money_report.py` → `strategy/meal_money.py` and `strategy/daily_meal_money.py` → `ai_report.py` (for tickers) is correct.

### Module dependency tree (lines 309-363)
- **`ai_report.py` tree**: Accurate. All listed module imports confirmed.
- **`paper_tracker.py` tree**: Accurate. Stdlib + pandas + dynamic yfinance.
- **`meal_money_report.py` tree**: Accurate. Shows `strategy/meal_money.py` → pandas.
- **`daily_meal_money_report.py` tree**: Accurate.
- **`crisis_test.py` tree**: Accurate. Shows subprocess → ai_report.py.
- **`deep_crisis_test.py` tree**: Accurate. Shows subprocess → both ai_report.py and sector_rotation_report.py.
- **`monte_carlo.py` tree**: Accurate.
- **`ablation_study.py` tree**: ⚠️ **Lists `strategy/benchmark.py` but script does NOT import it.**
- **`factor_grid_search.py` tree**: ⚠️ **Lists `strategy/benchmark.py` but script does NOT import it.**

---

## I/O File Descriptions

All I/O file entries in the Data Files table (lines 195-209) are accurate based on spot-checks:
- `paper_equity.json` — read/written by `paper_tracker.py` ✓
- `stock_report.html` — written by `ai_report.py`, read by `paper_tracker.py` ✓
- `artifacts/orders_*.json` — written by `ai_report.py`, read by `paper_tracker.py` ✓
- `artifacts/equity_*.csv` — written by `ai_report.py`, read by `monte_carlo.py` ✓
- `artifacts/trades_*.csv` — written by `ai_report.py`, read by `monte_carlo.py` (legacy) ✓
- `data/*.csv` — read by meal money scripts ✓
- `research/experiments.db` — written by ablation/factor scripts ✓

---

## Summary of Required Fixes

| Priority | Issue | Location |
|---|---|---|
| 🔴 Critical | Remove `strategy/benchmark.py` from `ablation_study.py` dependencies | Lines 121, 347-353 |
| 🔴 Critical | Remove `strategy/benchmark.py` from `factor_grid_search.py` dependencies | Lines 134, 355-363 |
| 🟡 Medium | Remove duplicate `strategy/news_sentiment.py` entry | Line 179 |
| 🟡 Medium | Add `vps_api/` section (9 files) | New section needed |
| 🟡 Medium | Add `sync_to_web.py` to supporting scripts | Supporting scripts table |
| 🟠 Low | Add `__init__.py` files (validation, research) | Strategy/validation sections |
| 🟠 Low | Fix total count: 42 → 54 | Summary Statistics |

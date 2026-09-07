# tw_stocker Script Inventory

> Auto-generated inventory for Phase 2 web planning.
> Last updated: 2026-08-16

---

## Table of Contents

1. [Core Scripts (Focused)](#core-scripts-focused)
2. [Supporting Scripts](#supporting-scripts)
3. [Strategy Modules](#strategy-modules)
4. [Research & Validation Modules](#research--validation-modules)
5. [Data Files](#data-files)
6. [Dependency Graph](#dependency-graph)

---

## Core Scripts (Focused)

### 1. `paper_tracker.py`

| Field | Detail |
|---|---|
| **Purpose** | Daily post-market paper trading tracker v8.5. Simulates live execution of the AI strategy: reads signals, tracks TP/SL/time exits, accumulates equity curve, generates a performance HTML report. |
| **Key Functions** | `load_data()`, `save_data()`, `get_current_bars()`, `recompute_tp_sl()`, `get_current_prices()`, `extract_signals_from_report()`, `extract_signals_from_orders()`, `update_tracker()`, `generate_html()`, `main()` |
| **Input Files** | `paper_equity.json` (state), `artifacts/orders_*.json` (signal orders), `stock_report.html` (fallback signals) |
| **Output Files** | `paper_equity.json` (updated state), `paper_trading.html` (performance report with Chart.js) |
| **Dependencies** | `json`, `glob`, `os`, `re`, `sys`, `datetime`, `argparse`, `pandas`, `yfinance` (dynamic import) |
| **Script Dependencies** | Reads output of `ai_report.py` (via `artifacts/orders_*.json` or `stock_report.html`) |

---

### 2. `ai_report.py`

| Field | Detail |
|---|---|
| **Purpose** | The main AI trading pipeline: data download → dynamic Universe → AI feature ranking → event-driven backtest → risk analysis → HTML report. Produces the daily `stock_report.html` with buy signals, benchmark comparison, and institutional flow data. |
| **Key Functions** | `resolve_dynamic_pool()`, `enforce_data_integrity()`, `get_next_n_trading_days()`, `_build_inst_section()`, `generate_report()`, `main()` |
| **Input Files** | (none — downloads live data via yfinance and TWSE API) |
| **Output Files** | `stock_report.html` (daily signals), `backtest_chart.png` (equity curve), `artifacts/orders_YYYYMMDD.json` (machine-readable orders), `artifacts/equity_*.csv`, `artifacts/trades_*.csv` |
| **Dependencies** | `argparse`, `json`, `os`, `sys`, `subprocess`, `datetime`, `matplotlib`, `pandas`, `numpy`, `exchange_calendars` (optional) |
| **Script Dependencies** | `strategy/ai_strategy.py`, `strategy/universe.py`, `strategy/event_backtest.py`, `strategy/evaluation.py`, `strategy/risk_metrics.py`, `strategy/benchmark.py`, `strategy/institutional_flow.py`, `strategy/news_sentiment.py` |

---

### 3. `meal_money_report.py`

| Field | Detail |
|---|---|
| **Purpose** | CLI for the Meal Money v1 morning strategy — a short-duration day-trading strategy targeting 09:05–09:40 window. Backtests local 5-min CSV data or generates next-session watchlists. |
| **Key Functions** | `parse_args()`, `resolve_tickers()`, `make_config()`, `print_summary()`, `main()` |
| **Input Files** | `data/*.csv` (5-min intraday data), optional night-market CSV |
| **Output Files** | `artifacts/meal_money_trades_*.csv`, `artifacts/meal_money_daily_*.csv`, `artifacts/meal_money_sector_*.csv`, `artifacts/meal_money_time_focus_*.csv`, `artifacts/meal_money_metadata_*.json`, `artifacts/meal_money_watchlist_*.csv` (signals mode) |
| **Dependencies** | `argparse`, `json`, `os`, `datetime` |
| **Script Dependencies** | `strategy/meal_money.py` (core logic), `ai_report.py` (for DEFAULT_TICKERS / EXTENDED_TICKERS constants), `strategy/us_market.py` (optional) |

---

### 4. `daily_meal_money_report.py`

| Field | Detail |
|---|---|
| **Purpose** | CLI for the Daily Meal Money bidirectional day-trading strategy. Supports long + short sides, multiple score modes, and preset configurations (small-cap-daily, small-cap-one-shot). |
| **Key Functions** | `parse_args()`, `apply_preset()`, `resolve_tickers()`, `make_config()`, `print_summary()`, `main()` |
| **Input Files** | `data/*.csv` (5-min intraday data) |
| **Output Files** | `artifacts/daily_meal_money_trades_*.csv`, `artifacts/daily_meal_money_daily_*.csv`, `artifacts/daily_meal_money_summary_*.json`, `artifacts/daily_meal_money_signals_*.csv` (signals mode) |
| **Dependencies** | `argparse`, `json`, `os`, `datetime`, `pandas` |
| **Script Dependencies** | `strategy/daily_meal_money.py` (core logic), `ai_report.py` (for ticker constants) |

---

### 5. `crisis_test.py`

| Field | Detail |
|---|---|
| **Purpose** | Historical crisis stress test — runs the v8.5 strategy backtest across 5 known crisis periods (COVID crash/recovery, rate hike, AI rally, recent). |
| **Key Functions** | `run_backtest_range()`, `main()` |
| **Input Files** | (none — downloads data via subprocess call to `ai_report.py`) |
| **Output Files** | Console output only (summary table + worst-case analysis) |
| **Dependencies** | `subprocess`, `re`, `sys`, `argparse`, `datetime` |
| **Script Dependencies** | `ai_report.py` (invoked via subprocess with date ranges) |

---

### 6. `deep_crisis_test.py`

| Field | Detail |
|---|---|
| **Purpose** | Deep crisis stress test with 00981A benchmark comparison + weakness analysis. Tests both Sector Rotation v2 and v8.5 momentum across 11 crisis periods (GFC through tariff shock). Includes US market indicators (SPY, VIX, SOX). |
| **Key Functions** | `run_sr_backtest()`, `run_v85_backtest()`, `_parse_output()`, `get_benchmark_return()`, `get_us_indicators()`, `main()` |
| **Input Files** | (none — downloads live data) |
| **Output Files** | Console output only (comparison table, weakness analysis, weight adjustment suggestions) |
| **Dependencies** | `subprocess`, `re`, `sys`, `numpy`, `pandas`, `yfinance`, `datetime`, `warnings` |
| **Script Dependencies** | `ai_report.py` (for v8.5 backtest), `sector_rotation_report.py` (for SR v2 backtest) |

---

### 7. `monte_carlo.py`

| Field | Detail |
|---|---|
| **Purpose** | Monte Carlo stress test v3 — Equity-curve block bootstrap. Resamples daily portfolio returns to estimate the distribution of total return, max drawdown, and Sharpe ratio under uncertainty. |
| **Key Functions** | `get_equity_curve()`, `get_trades()`, `equity_curve_bootstrap()`, `legacy_simulate()`, `main()` |
| **Input Files** | `artifacts/equity_*.csv` (required, via `--equity`), `artifacts/trades_*.csv` (optional, legacy mode) |
| **Output Files** | Console output only (distribution table, risk assessment, capital suggestions) |
| **Dependencies** | `re`, `sys`, `argparse`, `random`, `statistics`, `csv`, `os`, `glob`, `datetime`, `pandas`, `numpy` |
| **Script Dependencies** | None (reads output files produced by `ai_report.py`) |

---

### 8. `ablation_study.py`

| Field | Detail |
|---|---|
| **Purpose** | Factor ablation study — tests marginal contribution of each factor (momentum, trend, volume, stability) and hold-day variants. Records experiments to SQLite registry with deflated Sharpe and PBO validation. |
| **Key Functions** | `compute_factor_scores()`, `run_single_ablation()`, `record_ablation_experiment()`, `main()` |
| **Input Files** | (none — downloads live data) |
| **Output Files** | `ablation_results.csv`, `ablation_chart.png`, experiment records in SQLite registry |
| **Dependencies** | `argparse`, `os`, `sys`, `datetime`, `itertools`, `matplotlib`, `pandas`, `numpy` |
| **Script Dependencies** | `strategy/ai_strategy.py`, `strategy/event_backtest.py`, `strategy/risk_metrics.py`, `strategy/benchmark.py`, `research/experiment_registry.py`, `validation/deflated_sharpe.py`, `validation/pbo_cscv.py` |

---

### 9. `factor_grid_search.py`

| Field | Detail |
|---|---|
| **Purpose** | Factor combination grid search engine (FinLab-inspired). Searches RSI, breakout, revenue momentum, and value factors for optimal combinations. Three modes: ablation, compare, grid. |
| **Key Functions** | `compute_all_factors()`, `run_factor_combination()`, `run_ablation()`, `print_results_table()`, `plot_results()`, `record_factor_search_experiment()`, `main()` |
| **Input Files** | (none — downloads live data) |
| **Output Files** | `factor_search_results.csv`, `factor_search_chart.png`, experiment records in SQLite registry |
| **Dependencies** | `argparse`, `os`, `sys`, `datetime`, `itertools`, `matplotlib`, `pandas`, `numpy` |
| **Script Dependencies** | `strategy/ai_strategy.py`, `strategy/event_backtest.py`, `strategy/risk_metrics.py`, `strategy/benchmark.py`, `strategy/finlab_factors.py`, `research/experiment_registry.py`, `validation/deflated_sharpe.py`, `validation/pbo_cscv.py` |

---

## Supporting Scripts

| Script | Purpose | Key Dependencies |
|---|---|---|
| `sector_rotation_report.py` | Sector Rotation v2 strategy report (macro regime + sector flow). Independent of v8.5. | `strategy/ai_strategy`, `strategy/us_market`, `strategy/sector_rotation_backtest`, `strategy/benchmark` |
| `sweep.py` | Parameter sweep runner | `ai_report` constants |
| `walk_forward.py` | Walk-forward validation | `strategy/event_backtest`, `strategy/risk_metrics` |
| `walk_forward_nested.py` | Nested walk-forward validation | `strategy/event_backtest` |
| `verify_strategy.py` | Quick strategy verification | `strategy/event_backtest` |
| `paper_trade.py` | Older paper trading script | `strategy/event_backtest` |
| `meal_money_sweep.py` | Parameter sweep for meal money | `strategy/meal_money` |
| `test_benchmark.py` | Tests for benchmark module | `strategy/benchmark` |
| `test_meal_money.py` | Tests for meal money | `strategy/meal_money` |
| `test_daily_meal_money.py` | Tests for daily meal money | `strategy/daily_meal_money` |
| `test_evaluation.py` | Tests for evaluation module | `strategy/evaluation` |
| `test_finlab_factors.py` | Tests for FinLab factors | `strategy/finlab_factors` |
| `test_paper_tracker_edge_cases.py` | Edge case tests for paper tracker | `paper_tracker` |
| `test_validation_gates.py` | Tests for validation gates | `validation/*` |
| `test_data_gate.py` | Tests for data integrity gates | `strategy/ai_strategy` |
| `test_experiment_registry.py` | Tests for experiment registry | `research/experiment_registry` |

---

## Strategy Modules

| Module | Purpose | Exported Functions/Classes |
|---|---|---|
| `strategy/ai_strategy.py` | AI multi-dimensional ranking strategy v8.5. Core feature engineering (momentum ×3 + trend ×1). | `fetch_panel_data()`, `build_liquid_universe()`, `engineer_features()` |
| `strategy/event_backtest.py` | Event-driven backtest engine v2. TP/SL/trailing/time-exit, position sizing, gap filter, regime filter, sector caps. | `EventDrivenBacktester` class |
| `strategy/risk_metrics.py` | Risk metrics calculator (Sharpe, Sortino, Calmar, MDD, win rate, profit factor, etc.). | `compute_risk_metrics()`, `format_metrics_summary()` |
| `strategy/benchmark.py` | Benchmark module (0050 Buy-and-Hold, equal-weight, excess return). | `fetch_benchmark()`, `equal_weight_benchmark()`, `compute_excess_return()` |
| `strategy/universe.py` | TWSE listed common stocks via ISIN API (replaces hardcoded ticker list). | `get_twse_common_stocks()` |
| `strategy/evaluation.py` | Evaluation window slicing. | `slice_evaluation_window()` |
| `strategy/institutional_flow.py` | Institutional investor flow data (3 major investors). | `build_inst_flow_df()`, `get_inst_flow_for_signals()`, `fetch_inst_rankings()` |
| `strategy/news_sentiment.py` | News sentiment data for signals. | `get_news_sentiment_for_signals()` |
| `strategy/meal_money.py` | Meal Money v1 core logic (morning strategy 09:05–09:40). | `MealMoneyConfig`, `build_latest_watchlist()`, `run_backtest()`, `load_intraday_data()`, etc. |
| `strategy/daily_meal_money.py` | Daily bidirectional meal money core logic (long+short). | `DailyMealMoneyConfig`, `build_daily_features()`, `run_daily_backtest()`, `select_daily_candidates()`, etc. |
| `strategy/finlab_factors.py` | FinLab-inspired factors (RSI-20, breakout-300, revenue momentum, PE/PB value). | `compute_rsi_rank()`, `compute_breakout_rank()`, `compute_value_rank()`, `compute_revenue_momentum()` |
| `strategy/sector_flow.py` | Sector-level fund flow computation. | `compute_sector_flow()` |
| `strategy/sector_rotation_backtest.py` | Sector rotation backtest engine. | `SectorRotationBacktester` class |
| `strategy/us_market.py` | US market context (SOX/SPY/VIX signals). | `fetch_us_signals()`, `align_us_to_tw()` |
| `strategy/news_sentiment.py` | News sentiment for stock signals. | `get_news_sentiment_for_signals()` |

---

## Research & Validation Modules

| Module | Purpose | Key Exports |
|---|---|---|
| `research/experiment_registry.py` | SQLite-backed audit log for strategy research experiments. Stores metrics, daily returns, decisions. | `ExperimentRegistry`, `daily_returns_from_equity()`, `series_from_daily_returns()`, `trial_record()`, `DEFAULT_REGISTRY_PATH` |
| `validation/deflated_sharpe.py` | Deflated Sharpe Ratio (Bailey/Lopez de Prado). Adjusts Sharpe for multiple testing. | `DeflatedSharpeResult`, `compute_deflated_sharpe()`, `annualized_sharpe()` |
| `validation/pbo_cscv.py` | Probability of Backtest Overfitting via CSCV. | `PBOResult`, `compute_pbo()` |

---

## Data Files

| File | Description | Read By | Written By |
|---|---|---|---|
| `paper_equity.json` | Paper trading state (positions, equity curve, closed trades, pending orders) | `paper_tracker.py` | `paper_tracker.py` |
| `stock_report.html` | Daily AI signals report (HTML) | `paper_tracker.py` (fallback) | `ai_report.py` |
| `paper_trading.html` | Paper trading performance page (HTML + Chart.js) | (viewed in browser) | `paper_tracker.py` |
| `artifacts/orders_*.json` | Machine-readable daily order signals | `paper_tracker.py` | `ai_report.py` |
| `artifacts/equity_*.csv` | Backtest equity curve CSV | `monte_carlo.py` | `ai_report.py` |
| `artifacts/trades_*.csv` | Backtest trade log CSV | `monte_carlo.py` (legacy) | `ai_report.py` |
| `ablation_results.csv` | Factor ablation comparison table | (manual review) | `ablation_study.py` |
| `ablation_chart.png` | Factor ablation equity curves + Sharpe bars | (manual review) | `ablation_study.py` |
| `factor_search_results.csv` | Factor grid search comparison table | (manual review) | `factor_grid_search.py` |
| `factor_search_chart.png` | Factor search equity curves + Sharpe bars | (manual review) | `factor_grid_search.py` |
| `backtest_chart.png` | AI report equity curve chart | (manual review) | `ai_report.py` |
| `data/*.csv` | 5-minute intraday data (for meal money strategies) | `meal_money_report.py`, `daily_meal_money_report.py` | External data source |
| `research/experiments.db` | SQLite experiment registry | `ablation_study.py`, `factor_grid_search.py` | `ablation_study.py`, `factor_grid_search.py` |

---

## Dependency Graph

### Script-level dependency graph (ASCII art)

```
                         ┌──────────────────────┐
                         │   ai_report.py       │
                         │ (Main AI Pipeline)   │
                         └──────┬───────┬───────┘
                                │       │
               ┌────────────────┘       └────────────────┐
               │                                         │
               ▼                                         ▼
   ┌───────────────────┐                   ┌──────────────────────┐
   │ paper_tracker.py  │                   │   crisis_test.py     │
   │ (Paper Trading)   │                   │ (Crisis Stress Test) │
   └───────────────────┘                   └──────────┬───────────┘
                                                       │
                                                       │ calls via subprocess
                                                       ▼
   ┌───────────────────┐                   ┌──────────────────────┐
   │   monte_carlo.py  │                   │ deep_crisis_test.py  │
   │ (MC Bootstrap)    │                   │ (Deep Crisis + 00981A│
   └───────┬───────────┘                   │  + Weakness Analysis)│
           │                               └──────────┬───────────┘
           │ reads artifacts/                        │
           │                                         │ calls via subprocess
           │                          ┌──────────────┴──────────────┐
           │                          │                             │
           ▼                          ▼                             ▼
   ┌──────────────┐        ┌─────────────────┐      ┌──────────────────────┐
   │  ai_report   │        │ ai_report.py    │      │sector_rotation_report│
   │  .py output  │        │ (v8.5 backtest) │      │    .py (SR v2)       │
   └──────────────┘        └─────────────────┘      └──────────────────────┘


   ┌──────────────────────────────────────────────────────────────────┐
   │                    FACTOR RESEARCH PIPELINE                      │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────┐        ┌──────────────────────────┐
   │ ablation_study.py│        │ factor_grid_search.py    │
   │ (Factor Ablation)│        │ (Factor Grid Search)     │
   └────────┬─────────┘        └────────────┬─────────────┘
            │                               │
            │  Both import:                 │
            ├───────────────────────────────┤
            ▼                               ▼
   ┌─────────────────────────────────────────────────────┐
   │  strategy/ai_strategy.py   (fetch_panel_data, etc.) │
   │  strategy/event_backtest.py (EventDrivenBacktester)  │
   │  strategy/risk_metrics.py  (compute_risk_metrics)    │
   │  strategy/benchmark.py     (fetch_benchmark)         │
   └─────────────────────────────────────────────────────┘
            │                               │
            ├──── ablation also uses: ──────┤
            ▼                               ▼
   ┌─────────────────────────────────────────────────────┐
   │  research/experiment_registry.py  (SQLite logging)  │
   │  validation/deflated_sharpe.py    (DSR correction)  │
   │  validation/pbo_cscv.py           (PBO overfitting) │
   └─────────────────────────────────────────────────────┘
            │
            ▼ (factor_grid_search also uses):
   ┌─────────────────────────────────────────────────────┐
   │  strategy/finlab_factors.py (RSI, breakout, value)  │
   └─────────────────────────────────────────────────────┘


   ┌──────────────────────────────────────────────────────────────────┐
   │                    MEAL MONEY PIPELINE                           │
   └──────────────────────────────────────────────────────────────────┘

   ┌────────────────────────┐      ┌─────────────────────────────┐
   │meal_money_report.py    │      │daily_meal_money_report.py   │
   │(Morning v1 strategy)   │      │(Bidirectional day strategy) │
   └───────────┬────────────┘      └──────────────┬──────────────┘
               │                                  │
               ▼                                  ▼
   ┌────────────────────────┐      ┌─────────────────────────────┐
   │strategy/meal_money.py  │      │strategy/daily_meal_money.py │
   │(core logic)             │      │(core logic)                  │
   └────────────────────────┘      └─────────────────────────────┘
               │                                  │
               └──────────┬───────────────────────┘
                          │ both import for ticker lists:
                          ▼
               ┌─────────────────────┐
               │  ai_report.py       │
               │  (DEFAULT_TICKERS,  │
               │   EXTENDED_TICKERS) │
               └─────────────────────┘
```

### Module dependency graph (who imports whom)

```
ai_report.py
  ├── strategy/ai_strategy.py ──→ yfinance, pandas, numpy
  │     └── strategy/finlab_factors.py (optional, for RSI in engineer_features)
  ├── strategy/universe.py ──→ urllib, json (TWSE ISIN API)
  ├── strategy/event_backtest.py ──→ pandas, numpy, yfinance
  │     └── strategy/sector_flow.py (lazy import in run())
  ├── strategy/evaluation.py ──→ pandas
  ├── strategy/risk_metrics.py ──→ pandas, numpy
  ├── strategy/benchmark.py ──→ yfinance, pandas, numpy
  ├── strategy/institutional_flow.py
  └── strategy/news_sentiment.py

paper_tracker.py
  ├── (stdlib only: json, glob, os, re, sys, datetime, argparse)
  ├── pandas
  └── yfinance (dynamic import in get_current_bars)

meal_money_report.py
  └── strategy/meal_money.py ──→ pandas

daily_meal_money_report.py
  └── strategy/daily_meal_money.py ──→ pandas

crisis_test.py
  └── subprocess → ai_report.py

deep_crisis_test.py
  ├── subprocess → ai_report.py
  ├── subprocess → sector_rotation_report.py
  ├── yfinance, numpy, pandas
  └── strategy/* (indirectly via subprocess)

monte_carlo.py
  ├── pandas, numpy, random, statistics, csv
  └── (reads artifacts/ output from ai_report.py)

ablation_study.py
  ├── strategy/ai_strategy.py
  ├── strategy/event_backtest.py
  ├── strategy/risk_metrics.py
  ├── strategy/benchmark.py
  ├── research/experiment_registry.py ──→ sqlite3, pandas
  ├── validation/deflated_sharpe.py ──→ numpy, pandas
  └── validation/pbo_cscv.py ──→ numpy, pandas

factor_grid_search.py
  ├── strategy/ai_strategy.py
  ├── strategy/event_backtest.py
  ├── strategy/risk_metrics.py
  ├── strategy/benchmark.py
  ├── strategy/finlab_factors.py ──→ pandas, numpy
  ├── research/experiment_registry.py
  ├── validation/deflated_sharpe.py
  └── validation/pbo_cscv.py
```

---

## Summary Statistics

| Category | Count |
|---|---|
| Focused scripts | 9 |
| Supporting scripts | 16 |
| Strategy modules | 14 |
| Research/Validation modules | 3 |
| Total Python files (excl. `__pycache__`, `.agents/`) | 42 |

### Script Roles by Function

- **Signal Generation**: `ai_report.py`, `meal_money_report.py`, `daily_meal_money_report.py`
- **Live/Paper Execution**: `paper_tracker.py`
- **Backtesting**: `strategy/event_backtest.py`, `strategy/sector_rotation_backtest.py`
- **Stress Testing**: `crisis_test.py`, `deep_crisis_test.py`, `monte_carlo.py`
- **Research/Validation**: `ablation_study.py`, `factor_grid_search.py`, `walk_forward.py`, `walk_forward_nested.py`
- **Reporting**: `ai_report.py`, `sector_rotation_report.py`

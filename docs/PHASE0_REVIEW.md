# Phase 0 Code Review

**Commit**: ed79e02  
**Reviewer**: Hermes Agent (manual review, Codex credits exhausted)  
**Date**: 2026-08-28  
**Score**: 8.5/10 ✅ PASS

## Summary

Phase 0 delivers 5 observability and correctness fixes to tw_stocker MR20 pipeline. No strategy logic changes. All 198 tests pass.

## Findings

### ✅ PASS — Funnel Diagnostics (strategy/mr20_strategy.py)
- `filter_mr20_candidates()` now accepts `funnel: Optional[dict]` parameter
- 8 gate counters: requested → valid_60d → liquid_top50 → trend → pullback → rsi → bounce → final
- Counters increment at correct filter points (verified via diff: `_f["trend"] += 1` after MA60 check, `_f["pullback"] += 1` after Close<MA20, etc.)
- `generate_mr20_orders()` now returns funnel + config in artifact JSON
- **Correctness**: ✅ counters match actual filter gate order

### ✅ PASS — RSI Default Unification
- CLI default changed from `35.0` to `40.0` (line 609)
- Now matches dataclass default `rsi_max=40`
- Help text updated to "預設 40.0"
- **Impact**: MR20 scans will now be slightly less restrictive (RSI threshold 35→40), may produce more candidates

### ✅ PASS — MR20 State Identity Fix
- `strategy_id`: `'top2_score_v1'` → `'mr20'`
- `initial_capital`: `20000` → `1000000`
- `cash`: `20000` → `1000000`
- Equity curve entries updated accordingly
- **Correctness**: ✅ prevents wrong strategy attribution and capital miscalculation

### ✅ PASS — Scheduler Contract (run_mr20.sh)
- Full rewrite with `set -euo pipefail`
- D/D+1 date logic: D = yesterday (signal), D+1 = today (execution)
- Steps: generate → close-and-plan → open (with freshness gate)
- Freshness gate prevents creating PENDING orders for stale signal dates
- **Safety**: ✅ expired signals won't create phantom PENDING orders

### ✅ PASS — Monitoring Separation (paper_tracker.py)
- New `📊 監控摘要` section with 5 separate counts:
  - fill_count (FILLED)
  - cancel_count (CANCELLED_*)
  - pending_count (pending_orders)
  - open_count (positions)
  - closed_count (closed_trades)
- **Impact**: `closed_trades=0` no longer misread as "zero fills"

### ℹ️ Minor Observations
1. `cx23-projects/` was removed (nested git repo artifact) — cleaned up
2. `artifacts/mr20/orders_mr20_*.json` files are untracked — expected (generated data)
3. Equity CSV changes are data accumulation, not code changes

## Verification

| Check | Status |
|---|---|
| 198/198 tests pass | ✅ |
| No strategy logic changes | ✅ |
| git diff --stat: +435/-73 | ✅ |
| Commit message accurate | ✅ |

## Recommendation

**PASS (8.5/10)** — Ready for Phase 1 (TradingAgents adapter). The observability improvements will make future debugging of zero-signal periods straightforward.

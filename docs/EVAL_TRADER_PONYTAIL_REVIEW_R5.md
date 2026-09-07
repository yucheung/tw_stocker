# Eval Trader Ponytail Review — Round 5

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R1 **4/10**, R2 **5/10**, R3 **6/10**, R4 **7/10**  
**Round 5 score:** **8/10**  
**Verdict:** Request changes. The NaN-open production crash is fixed and the requested Ruff F gate passes. Candidate filtering remains a bounded oversampling workaround, while the no-look-ahead test still does not prove sizing causality. Ruff's green result also does not mean all previously identified dead helpers were removed.

## R4 issue verification

| # | R4 issue | Round 5 verdict | Evidence |
|---|---|---|---|
| 1 | NaN held-position open falls back to entry cost | **Fixed in code; untested** | `eval_trader.py:L172-L182` uses a finite open when available and otherwise values the holding at `entry * shares`. A focused probe with a `NaN` held open completed with `FILLED`, 2,000 shares—the exact entry-fallback result. No committed test exercises this branch. |
| 2 | All dead code removed / Ruff F passes | **Ruff gate fixed; literal cleanup incomplete** | `ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F` exits 0 with `All checks passed!`. However, the previously identified unused `_make_uptrend` and `_merge_dfs` helpers remain at `tests/test_eval_trader.py:L27-L55`, and the new `close_for_aaaa` argument at `L364` is also unused. Ruff F does not detect unused function definitions or arguments. |
| 3 | No-look-ahead test proves sizing causality | **Not fixed** | `tests/test_eval_trader.py:L364-L410` calls `_run_with_close(70.0)` and `_run_with_close(200.0)`, but `close_for_aaaa` never enters either DataFrame or `_execute_orders`. Both scenarios therefore execute identical inputs. `fill_price == fill_open` proves fill-price selection, not that held-position opens cause sizing. |
| 4 | Candidates filtered before `top_n` truncation | **Not fixed; partially mitigated** | `eval_trader.py:L288-L296` requests `top_n * 3`, but `_baseline_signals` still truncates at `eval_trader.py:L94-L96` / `strategy/eval_baseline.py:L98`, and `_mr20_signals` still receives at most MR20's default seven candidates before slicing at `eval_trader.py:L99-L102`. A probe with 15 invalid ranked candidates followed by valid candidate 16 produced zero orders because candidate 16 was truncated first. |

## Findings

- `eval_trader.py:L289`: 🟡 risk: `top_n * 3` is a finite buffer, so enough held/pending/invalid names still hide lower-ranked tradable candidates. Return the full ranked candidate set (or pass portfolio eligibility into ranking), filter it, then apply `top_n` exactly once.
- `tests/test_eval_trader.py:L364`: 🟡 test: `close_for_aaaa` is unused, making the two sizing scenarios identical. Vary the held ticker's open across a board-lot boundary and assert exact, different share counts; retain `fill_price == open_price` as a separate execution-price assertion.
- `tests/test_eval_trader.py:L27-L55`: 🔵 cleanup: `_make_uptrend` and `_merge_dfs` remain unreferenced. Delete them or use them; Ruff `--select F` alone cannot prove all dead definitions are gone.
- `tests/test_eval_trader.py:L350`: 🟡 test: no regression test covers the new `NaN`-open fallback. Seed a held position with a `NaN` execution-day open and assert the exact shares implied by its entry valuation.

No additional production-correctness regression was found in the three reviewed files.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
............                                                             [100%]
12 passed in 1.09s

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F
All checks passed!

$ NaN held-open fallback probe
NAN_FALLBACK ... status='FILLED' ... shares=2000
EXPECTED_SHARES 2000

$ filter-before-truncation probe
BUFFER_TRUNCATION {'returned': 15, 'orders': [], 'hidden': 'GOOD16'}
```

Round 5 earns **8/10**: the crash and F-class cleanup are materially improved, but two core acceptance claims remain unproven or behaviorally incomplete, and the NaN fix lacks regression coverage.

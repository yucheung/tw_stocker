# Eval Trader Ponytail Review — Round 7

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R1 **4/10**, R2 **5/10**, R3 **6/10**, R4 **7/10**, R5 **8/10**, R6 **8.2/10**  
**Round 7 score:** **8.0/10**  
**Target:** **8.5+/10 — not met**  
**Verdict:** Request changes. All three R6 issues are fixed, but the new exclusion call breaks the MR20 strategy at runtime and the test suite does not cover that dispatch path.

## R6 issue verification

| # | Requested verification | Round 7 verdict | Evidence |
|---|---|---|---|
| 1 | NaN-open mutation is killed with distinctive sizing | **Fixed; mutant killed** | `tests/test_eval_trader.py:L388-L444` uses entry 200, 1,000 held shares, and cash 500,000. The real fallback yields 2,000 new shares; an in-memory mutation removing both entry fallback additions yields 1,000 and fails the exact-share assertion. |
| 2 | Filter candidates before `top_n` truncation; accept `exclude_tickers`; remove `top_n * 3` | **Fixed for baseline** | `strategy/eval_baseline.py:L22,L83-L84` accepts and applies `exclude_tickers`; truncation occurs later at `L101`. A runtime probe excluding the original top five returned the next five candidates. Repository search found no `top_n * 3` or `3 * top_n` in the reviewed files. |
| 3 | Remove all unused variables/arguments | **Fixed** | Ruff `F401,F841,RUF059,ARG` reports `All checks passed!` for all three reviewed files. Intentionally unused callback variables now use underscore-prefixed names. |

## Findings

- `eval_trader.py:L99,L291`: 🔴 bug: `run_backtest(strategy="mr20")` always passes `exclude_tickers`, but `_mr20_signals` does not accept that keyword; MR20 now raises `TypeError` on its first simulated date. Add `exclude_tickers` to the wrapper, filter the full ranked MR20 result before `[:top_n]`, and preserve filter-then-truncate semantics.
- `tests/test_eval_trader.py:L214-L317,L540-L565`: 🟡 risk: lifecycle and dispatch coverage exercises only `baseline`, so the MR20 signature regression passes all 13 tests. Add a focused `run_backtest(..., strategy="mr20")` test that also proves exclusions precede truncation.

No other production-correctness regression was found in the three reviewed files.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
.............                                                            [100%]
13 passed in 1.24s
exit 0

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F401,F841,RUF059,ARG
All checks passed!
exit 0

$ NaN fallback in-memory mutation probe
MUTATION_KILLED: Shares 1000 != expected 2000 (entry-fallback equity=700000.0)
exit 0

$ baseline exclusion-before-truncation probe
FILTER_THEN_TRUNCATE_PROVED: ['T05', 'T06', 'T07', 'T08', 'T09']
exit 0

$ search for top_n multiplied by 3
NO_TOP_N_TIMES_3
exit 0

$ MR20 dispatch probe
MR20_SIGNATURE_FAILURE: _mr20_signals() got an unexpected keyword argument 'exclude_tickers'
exit 1
```

Round 7 earns **8.0/10**. The R6 acceptance items are now conclusively complete, including a genuinely killing NaN mutation test and clean unused-code selectors. The score remains below **8.5** because the candidate-filter repair introduced a hard failure in one of the driver's two advertised strategy modes, and the focused suite provides no regression coverage for that mode.

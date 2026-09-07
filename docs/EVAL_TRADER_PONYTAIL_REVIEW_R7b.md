# Eval Trader Ponytail Review — Round 7b

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R6 **8.2/10**, R7 **8.0/10**  
**Round 7b score:** **8.4/10**  
**Verdict:** Request changes. The MR20 crash is fixed and the full suite passes, but `exclude_tickers` is accepted without being applied. This restores dispatch while leaving MR20's filter-before-truncate behavior incomplete and reintroducing an unused-argument lint failure.

## Requested verification

| # | Requested verification | Round 7b verdict | Evidence |
|---|---|---|---|
| 1 | MR20 no longer crashes | **Fixed** | `eval_trader.py:L99` adds `exclude_tickers=None` to `_mr20_signals`. A focused `run_backtest(..., strategy="mr20")` dispatch probe completed two simulated days without `TypeError`. |
| 2 | All R6 fixes remain intact | **Partially verified** | The distinctive NaN-open fallback and held-open sizing tests pass; baseline exclusions still occur before `top_n`; and no `top_n * 3` form remains. The R6 unused-code cleanup is not fully intact because Ruff reports the newly added but unused `exclude_tickers` argument at `eval_trader.py:L99`. |
| 3 | 211 tests pass | **Satisfied with a higher current count** | Fresh full-suite run: **259 passed in 11.77s**, exit 0. The current repository collects 259 tests rather than 211. The focused eval-trader suite also reports **13 passed in 1.20s**, exit 0. |

## Findings

- `eval_trader.py:L102`: 🟡 risk: `_mr20_signals` ignores `exclude_tickers` and slices the unfiltered ranking, so held/pending leaders can consume all `top_n` slots and starve valid lower-ranked candidates. Filter the MR20 result by `exclude_tickers` before `[:top_n]`.
- `eval_trader.py:L99`: 🔵 cleanup: Ruff `ARG001` flags `exclude_tickers` as unused. Applying the exclusion removes the lint regression.
- `tests/test_eval_trader.py:L214-L317,L540-L565`: 🟡 risk: lifecycle/dispatch coverage remains baseline-only, so both the former MR20 crash and the current ignored-exclusion defect pass the focused suite. Add an MR20 dispatch test that excludes the leading candidates and asserts lower-ranked replacements.

No additional production-correctness regression was found in the three reviewed files.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
.............                                                            [100%]
13 passed in 1.20s
exit 0

$ python -m pytest -q
........................................................................ [ 27%]
........................................................................ [ 55%]
........................................................................ [ 83%]
...........................................                              [100%]
259 passed in 11.77s
exit 0

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F401,F841,RUF059,ARG
ARG001 Unused function argument: `exclude_tickers`
  --> eval_trader.py:99:57
Found 1 error.
exit 1

$ MR20 dispatch probe
MR20_DISPATCH_OK: days=2
exit 0

$ MR20 exclusion probe (exclude T00 and T01, top_n=2)
MR20_EXCLUSION_RESULT: ['T00', 'T01']
MR20_EXCLUSION_IGNORED: excluded top-ranked tickers were returned
exit 0

$ baseline exclusion-before-truncation probe
BASELINE_FIRST: ['T09', 'T08', 'T07', 'T06', 'T05']
BASELINE_AFTER_EXCLUDE: ['T04', 'T03', 'T02', 'T01', 'T00']
BASELINE_FILTER_THEN_TRUNCATE_OK
exit 0

$ search for top_n multiplied by 3
NO_TOP_N_TIMES_3
exit 0
```

Round 7b earns **8.4/10**. The one-line signature repair removes the R7 hard crash, and all 259 currently collected tests pass. It does not complete the exclusion contract that motivated the parameter: MR20 still truncates before held/pending candidates are removed, the focused tests do not detect that behavior, and the strict unused-code gate now fails.

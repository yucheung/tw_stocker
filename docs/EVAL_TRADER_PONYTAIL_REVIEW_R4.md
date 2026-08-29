# Eval Trader Ponytail Review — Round 4

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R1 **4/10**, R2 **5/10**, R3 **6/10**  
**Round 4 score:** **7/10**  
**Verdict:** Request changes. The three production fixes are present: sizing no longer reads the execution-day close, execution enforces `MAX_POSITIONS`, and artifacts retain filled events. Dead-code cleanup is incomplete, the new test does not prove the sizing fix, and missing opens can now crash execution.

## R3 issue verification

| # | R3 issue | Round 4 verdict | Evidence |
|---|---|---|---|
| 1 | No same-day close look-ahead | **Fixed in code** | `eval_trader.py:L146-L181` removes `close_df` from `_execute_orders` and values held positions from `open_df`. A focused execution probe confirms the function has no close input. |
| 2 | `MAX_POSITIONS` enforced in execution | **Fixed in code; untested** | `eval_trader.py:L166-L170` checks capacity before each fill. Four held positions plus two due orders produced five holdings with statuses `FILLED` and `CANCELLED_MAX_POSITIONS`. No committed test covers this boundary. |
| 3 | Filled orders exported | **Fixed in code; untested** | `eval_trader.py:L396-L404` appends every `fill_log` record to `orders.jsonl`. A focused export probe produced `PENDING` and `FILLED`. No committed test reads `orders.jsonl` to prevent regression. |
| 4 | Dead code removed | **Not fixed** | `eval_trader.py:L114` retains unused `_close_df`; `tests/test_eval_trader.py:L27-L55` retains unused helpers; Ruff also reports unused imports/locals at `L244`, `L249`, `L306`, `L311`, `L360`, and `L507`. |
| 5 | New test proves no look-ahead | **Not proven** | `tests/test_eval_trader.py:L352-L404` creates no existing holding, so held-position market value is empty and sizing is independent of both open and close. It asserts fill price and lifecycle, but never asserts shares or invariance under a changed execution-day close. |

## New blocking finding

- `eval_trader.py:L172-L181`: 🔴 bug: one held ticker with a `NaN` D-open makes `mkt_val`, `equity_now`, and `slot` become `NaN`; `math.floor` then raises `ValueError` and aborts the backtest. Value each holding with a finite open and fall back per ticker to the latest known price or entry price.

## Test findings

- `tests/test_eval_trader.py:L352-L404`: 🟡 risk: `test_no_lookahead_sizing_uses_open_not_close` would not detect replacing held-position open valuation with close valuation because there are no pre-existing positions. Seed a holding, run identical opens with two different D closes, and assert identical fill status and exact share count.
- `tests/test_eval_trader.py`: 🟡 risk: the execution-cap and artifact-export fixes have no regression tests. Add exact tests for the fifth/sixth due fills and for a persisted `FILLED` JSONL record.

## Remaining prior-review issues

- `eval_trader.py:L94-L102,L123-L133`: 🟡 risk: signal wrappers truncate to `top_n` before held, pending, or invalid candidates are filtered, so skipped high-ranked names can hide lower-ranked tradable candidates. Filter portfolio eligibility before truncation or request a broader ranked set.
- `tests/test_eval_trader.py:L61-L95,L196-L236,L500-L526`: 🟡 risk: volume causality, tie-break levels, and cancellation counting remain weak or vacuous tests as detailed in Round 3.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
............                                                             [100%]
12 passed in 1.09s

$ execution-cap probe
EXECUTION_CAP 5 ['FILLED', 'CANCELLED_MAX_POSITIONS']

$ artifact-export probe
ARTIFACT_STATUSES ['PENDING', 'FILLED']

$ missing-open probe
NAN_OPEN ValueError cannot convert float NaN to integer

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F
eval_trader.py:465:11: f-string is missing placeholders
eval_trader.py:466:11: f-string is missing placeholders
eval_trader.py:467:11: f-string is missing placeholders
eval_trader.py:468:11: f-string is missing placeholders
eval_trader.py:469:11: f-string is missing placeholders
tests/test_eval_trader.py:244:5: 'eval_trader._new_state' imported but unused
tests/test_eval_trader.py:249:5: local variable 'tickers' is assigned to but never used
tests/test_eval_trader.py:306:5: 'eval_trader._new_state' imported but unused
tests/test_eval_trader.py:311:5: local variable 'tickers' is assigned to but never used
tests/test_eval_trader.py:360:5: 'eval_trader.INIT_CAPITAL' imported but unused
tests/test_eval_trader.py:360:5: 'eval_trader.MAX_POSITIONS' imported but unused
tests/test_eval_trader.py:507:5: local variable 'tickers' is assigned to but never used
```

Round 4 earns **7/10**: all three requested production behavior fixes work in focused probes, but two acceptance items remain unmet and the open-price change introduces an unhandled missing-data crash.

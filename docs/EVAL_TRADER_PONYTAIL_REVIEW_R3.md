# Eval Trader Ponytail Review — Round 3

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R1 **4/10**, R2 **5/10**  
**Round 3 score:** **6/10**  
**Verdict:** Request changes. MR20 dispatch, normal-path position capping, real lifecycle coverage, and terminal-date replacement improved. However, `MAX_POSITIONS` is not enforced at execution, dead code remains, filled events are still excluded from artifacts, and opening-order sizing has a newly identified same-day-close look-ahead bug.

## R2 issue verification

| # | R2 issue | Round 3 verdict | Evidence |
|---|---|---|---|
| 1 | MR20 no longer crashes | **Fixed in code; untested** | `eval_trader.py:L100-L103` now accepts `top_n` and truncates the MR20 result. A focused dispatch probe returned two candidates without `TypeError`. No test invokes `strategy="mr20"` or `_mr20_signals`. |
| 2 | `MAX_POSITIONS` enforced | **Partial** | `eval_trader.py:L117-L125` caps newly placed orders by held plus pending names; a four-held-position probe placed one order. `eval_trader.py:L147-L195` has no execution-time guard: seeding four positions and two due orders produced six positions. No test covers either boundary. |
| 3 | No duplicate final-date equity | **Fixed in code; inadequately tested** | `eval_trader.py:L305-L309` replaces the final MTM value when dates match. An end-to-end probe produced three observations on three unique dates. `tests/test_eval_trader.py:L280-L296` only asserts `len(equity_curve) >= 5`, so the former six-row duplicate would still pass. |
| 4 | Tests exercise real code paths | **Improved, still partial** | `tests/test_eval_trader.py:L268-L296` injects a deterministic signal and asserts a real D+1 fill plus force-close trade. `L340-L359` now proves one exact expiry. However, `L456-L482` still permits the vacuous `0 == 0` cancellation case; `L390-L430` calls `_close_position` directly instead of proving `run_backtest` terminal replacement; and there are no regressions for MR20, capacity, unique terminal dates, or artifact exports. |
| 5 | Dead code removed | **Not fixed** | `eval_trader.py:L32` leaves unused `SELL_COST`; `_place_orders.close_df` at `L115` is unused. `tests/test_eval_trader.py:L27-L55` leaves two unused helpers; `L306`, `L329-L339`, and `L392-L393` contain unused imports, functions, variables, and `SELL_COST`. |
| 6 | Fills/cancellations exported | **Partial** | Cancellations are appended at `eval_trader.py:L393-L395`, but the `status != "FILLED"` condition explicitly drops every fill. The export probe contained `PENDING` and `CANCELLED_EXPIRED`, but no `FILLED` record. |

## Blocking findings

- `eval_trader.py:L164-L169`: 🔴 bug: opening orders use `close_df.at[exec_date, ...]` to value existing positions, so the unknowable D close changes a D-open fill's size or cancellation. Mark holdings with the prior close or the current open before calculating `equity_now`.
- `eval_trader.py:L147-L195`: 🔴 bug: `_execute_orders` can fill beyond `MAX_POSITIONS`; four held positions plus two due orders produced six holdings. Recheck available slots during execution and cancel excess due orders with an explicit reason.
- `eval_trader.py:L389-L398`: 🔴 bug: `orders.jsonl` claims to combine placements, fills, and cancellations but excludes `FILLED`, losing execution price, shares, TP, and SL from persisted artifacts. Append all fill-log records or write a dedicated `fills.jsonl`.

## Test findings

- `tests/test_eval_trader.py:L280-L296`: 🟡 risk: the lifecycle test does not assert one equity point per simulated date or exact terminal cash, so duplicate terminal dates and incorrect liquidation accounting can regress green. Assert exact dates, exact row count, and cost-adjusted terminal equity.
- `tests/test_eval_trader.py:L456-L482`: 🟡 risk: flat volume produces no baseline signal or cancellation, making `sum(cancel_reasons) == cancelled_fill_count` pass as `0 == 0`. Inject a deterministic cancelled order and assert its exact status and count.
- `tests/test_eval_trader.py:L196-L236`: 🟡 risk: the fixture does not create controlled equal-score/equal-turnover cases, so its alphabetical assertion does not prove both documented tie-break levels. Construct explicit score and turnover ties and assert the exact order.
- `tests/test_eval_trader.py:L61-L95`: 🟡 risk: the test recomputes the volume formula outside production and only checks that production returns some candidate. Compare the target-date result before and after appending or perturbing future bars.
- `tests/test_eval_trader.py:L304-L350`: 🔵 nit: `mock_signals`, `orig_new_state`, `_new_state`, and `_place_orders` are unused scaffolding around the seeded-expiry test. Remove them.

## Remaining prior-review issue

- `eval_trader.py:L95-L97,L124-L129`: 🟡 risk: signal generation truncates to `top_n` before held, pending, or invalid candidates are filtered, so skipped high-ranked names hide lower-ranked tradable candidates. Filter against portfolio eligibility before truncation or request a sufficiently broad ranked list.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
...........                                                              [100%]
11 passed in 1.05s

$ MR20 dispatch probe
MR20 2 {'as_of_date': '2025-01-02', 'config': None}

$ placement-cap probe
PLACEMENT_CAP 4 1 1

$ execution-cap probe
EXECUTION_CAP 6 ['FILLED', 'FILLED']

$ terminal-date probe
EQUITY_DATES ['2025-01-01', '2025-01-02', '2025-01-03'] 3 3

$ artifact-export probe
ORDERS_JSONL ['{"ticker": "A", "status": "PENDING"}',
              '{"ticker": "B", "status": "CANCELLED_EXPIRED"}']

$ open-sizing causality probe
OPEN_SIZING_LOOKAHEAD (5000, 'FILLED') (None, 'CANCELLED_INSUFFICIENT_CASH')
```

The focused suite is green, but two requested R2 fixes remain partial and the new look-ahead defect can change whether an order fills using information unavailable at execution time. This is improved over Round 2 but not yet reliable for unbiased strategy comparison.

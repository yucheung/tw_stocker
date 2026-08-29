# Eval Trader Ponytail Review — Round 2

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Score:** **5/10**  
**Verdict:** Request changes. Four fixes are present in production code, one is only partial, and the claimed mocked-candidate coverage was not added. Round 2 also breaks the MR20 strategy and introduces duplicate terminal dates in the equity curve.

## Six claimed fixes

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Terminal equity includes forced-sale costs | **Code fixed; regression test inadequate** | `eval_trader.py:L292-L303` closes positions through `_close_position`, whose `L78` deducts `_sell_costs`, then records cash. However, `tests/test_eval_trader.py:L335-L359` calls `_close_position` directly, imports but never uses `SELL_COST`, and ends with the tautology `state["cash"] == state["cash"]`; it does not prove `run_backtest` updates terminal equity by the exact net proceeds. |
| 2 | Window uses `max(0, total_bars - days)` | **Fixed** | `eval_trader.py:L255-L259` selects the last `days` bars with the claimed clamp. A seven-bar/three-day probe simulated exactly the last three sessions. No focused test asserts the selected dates. |
| 3 | Execute → exits → signals, using updated capacity | **Partial / not fixed** | Event order is correct at `eval_trader.py:L266-L281`, but capacity is not: `_place_orders` stops at `top_n`, not `MAX_POSITIONS - held - pending` (`L116-L138`). A probe with four held positions placed five new pending orders despite `MAX_POSITIONS == 5`. |
| 4 | Final-day pending orders expire | **Outcome fixed; test vacuous** | `eval_trader.py:L277-L290` does not place an order when no next dataset bar exists and expires any pending remainder. But `tests/test_eval_trader.py:L302-L304` only checks that `expired` is a list, which passes when it is empty; the fixture produces no pending order to expire. |
| 5 | Tests use mocked candidates and are non-vacuous | **Not fixed** | `tests/test_eval_trader.py` contains no mock/monkeypatch of `SIGNAL_FUNCS`. The “full lifecycle” fixture has flat volume, produces zero baseline candidates on all five simulated days, and therefore proves no D+1 fill, exit, forced-sale cost, or lifecycle behavior (`L235-L271`). The baseline determinism fixture is non-empty (`L194-L229`), but it still does not assert the documented exact tie-break order. |
| 6 | Taiwan minimum commission is NT$20 | **Fixed** | `eval_trader.py:L28,L59-L67` applies `max(rate × notional, 20)` to both buys and sells. `tests/test_eval_trader.py:L310-L329` covers below- and above-minimum notionals on both sides. |

## Blocking findings

- `eval_trader.py:L274`: 🔴 bug: `run_backtest(..., strategy="mr20")` passes `top_n=` to `_mr20_signals`, whose `L101` signature does not accept it; the supported CLI strategy raises `TypeError` on its first signal day. Accept and map `top_n` into `MR20Config.max_candidates`, or dispatch strategy-specific arguments.
- `eval_trader.py:L116-L138`: 🔴 bug: `MAX_POSITIONS` is unused, so four held positions can create five new orders and execution has no explicit position-count guard. Compute available slots after exits and cap both order placement and fills.
- `eval_trader.py:L302-L303`: 🟡 risk: forced liquidation appends instead of replaces the final MTM point, creating two observations with the same date; `compute_risk_metrics` counts the forced-sale fee as another “day” and annualizes over `days + 1`. Replace the last point after liquidation or force-close before the final snapshot.

## Test findings

- `tests/test_eval_trader.py:L235-L271`: 🔴 bug: the end-to-end test's flat volume makes every baseline candidate list empty, so all lifecycle assertions pass without an order or trade. Inject a deterministic signal function and assert exact D signal, D+1 fill, trade, and terminal cash.
- `tests/test_eval_trader.py:L277-L304`: 🟡 risk: `assert isinstance(expired, list)` cannot prove expiry. Seed/inject a pending order and assert one exact `CANCELLED_EXPIRED` record and zero pending orders.
- `tests/test_eval_trader.py:L335-L359`: 🟡 risk: the terminal-equity test never exercises `run_backtest` and its final equality is tautological. Assert the exact `INIT_CAPITAL + sale_notional - buy commission - sell commission - tax - slippage - entry_notional` result from a forced-close lifecycle.
- `tests/test_eval_trader.py:L194-L229`: 🟡 risk: “Tie-break verified” only checks descending scores; it never asserts ticker order for equal score/turnover cases. Build explicit ties and assert exact ticker sequence.
- `tests/test_eval_trader.py:L61-L93`: 🟡 risk: the candidate result is not asserted non-empty or inspected; most of the test recomputes the same formula independently of production output. Assert the expected candidate, then append/perturb D+1 data and prove the D result is unchanged.

## Remaining prior-review issues

- `eval_trader.py:L96-L98,L116-L138`: 🟡 risk: baseline signal generation truncates to `top_n` before held/pending/invalid names are filtered, so skipped top-ranked names still hide lower-ranked tradable candidates. Request enough ranked candidates or filter against portfolio state before truncation.
- `eval_trader.py:L27`: 🔵 nit: `ETF_SELL_TAX_RATE` remains dead; every instrument is charged the ordinary-stock tax by `_sell_costs`. Remove it or make tax product-aware.
- `eval_trader.py:L116`: 🔵 nit: `_place_orders.close_df` is still unused. Delete the parameter.
- `eval_trader.py:L383-L385`: 🟡 risk: `orders.jsonl` still exports only placement records, while fills and cancellations are discarded from artifacts. Export the full lifecycle or a separate fills file.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
...........                                                              [100%]
11 passed in 1.05s

$ capacity probe
held=4 orders=5 pending=5 max=5

$ MR20 probe
TypeError: _mr20_signals() got an unexpected keyword argument 'top_n'

$ forced-liquidation lifecycle probe
equity dates: 2025-01-01, 2025-01-02, 2025-01-03, 2025-01-03
pre-liquidation final equity: 1009857.50
post-cost terminal equity:     1009040.75
```

The focused suite is green, but it misses both blocking regressions and does not prove three of the six claimed fixes. The implementation is improved from Round 1, but it is not yet reliable enough for strategy comparison or reported risk metrics.

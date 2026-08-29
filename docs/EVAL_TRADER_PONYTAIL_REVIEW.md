# Eval Trader Ponytail Review

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Score:** **4/10**  
**Verdict:** Request changes. The implementation is direct and avoids class-heavy design, but 755 new lines still contain redundant code, dead parameters/imports, vacuous tests, and several backtest-accounting defects.

## Highest-priority findings

- `eval_trader.py:L247`: 🔴 bug: the last equity point is recorded before forced liquidation, so `SELL_COST` is charged to cash/trades but omitted from reported terminal equity and return. Replace the last equity point after liquidation or liquidate before the final MTM snapshot.
- `eval_trader.py:L219`: 🔴 bug: `run_backtest` always starts at bar 60, while the CLI fetches `days + 120` **calendar** days; this normally simulates an older, shorter window instead of the latest requested trading days. Select the last `days` sessions after retaining enough warm-up history.
- `eval_trader.py:L227`: 🟡 risk: D-close signals are placed before D-open fills and D exits, so capacity is evaluated from stale state and valid replacement orders are suppressed. Execute due orders and process D exits before placing D-close signals for D+1.
- `eval_trader.py:L232`: 🟡 risk: the final simulated day can create an order for a date outside `sim_dates`; it remains pending forever yet is counted as a signal. Do not place it, extend the execution window, or terminate it with an explicit cancellation.
- `eval_trader.py:L82`: 🟡 risk: candidates are sliced to capacity before held/pending/invalid names are removed, so skipped names waste slots and lower-ranked tradable candidates are ignored. Filter while iterating, then stop when capacity is filled.
- `eval_trader.py:L343`: 🟡 risk: an empty run does not overwrite `trades.csv`, leaving a stale file from a prior run while the CLI claims a new file was written. Always write the empty dataframe.

## D+1 and look-ahead verdict

**The price-data timing itself has no direct look-ahead, but the portfolio event ordering is wrong.**

- `strategy/eval_baseline.py:L54`: rolling MA, ATR, return, and turnover select row D and use only D or earlier observations.
- `strategy/eval_baseline.py:L59`: the volume-ratio denominator correctly uses D-20 through D-1; its numerator correctly uses volume on D, which is available only after D closes.
- `eval_trader.py:L76`: the order limit is D close and `exec_date` is the next index session.
- `eval_trader.py:L102`: due orders use only D+1 open through the shared `evaluate_buy_limit_at_open`; open above limit cancels, while open at/below limit fills at open.
- `eval_trader.py:L227`: generating the D-close signal before processing events earlier on D does not reveal future prices, but it does use stale positions/capacity and therefore changes the strategy's decisions.

The D+1 fill model is therefore mechanically correct in isolation, but the end-to-end simulation is not yet correct enough to support performance conclusions.

## Taiwan commission and cost model

**Partially correct for ordinary, non-day-traded stocks; incomplete as a transaction-cost model.**

- `eval_trader.py:L29`: 0.1425% is a standard/ceiling brokerage rate, not a universal fixed fee; brokers set their own schedules. Make commission configurable.
- `eval_trader.py:L30`: the 0.3% seller-side securities transaction tax is correct for an ordinary stock held overnight. The reduced 0.15% day-trade tax does not apply to this strategy's forced next-day-or-later exits.
- `eval_trader.py:L30`: the extra 0.3% is slippage, not a TWSE fee, and it is charged only on sells. Name commission, tax, and slippage separately and apply an explicitly documented symmetric/asymmetric execution assumption.
- `eval_trader.py:L123`: sizing permits any integer share count but executes it at the regular opening price. Regular stock trading units are 1,000 shares; 1-999 shares are odd lots with a different matching mechanism. Either size board lots or explicitly model odd-lot execution.
- `eval_trader.py:L123`: no broker-specific minimum commission is modeled. This materially matters for odd-lot/small orders; make it configurable rather than hard-coding NT$20, because broker schedules differ.
- `eval_trader.py:L29`: ETF/custom ticker runs would be mis-taxed: ordinary ETFs are generally taxed at 0.1% on sale rather than 0.3%. Restrict/validate the universe as common stocks or make tax product-aware.

Official references: [TWSE 2025 investor guide: commissions and transaction taxes](https://www.twse.com.tw/en/about/company/guide.html), [Ministry of Finance: stock transaction tax](https://www.etax.nat.gov.tw/etwmain/tax-info/understanding/tax-knowledge/rwG2M1N), [TWSE: odd-lot fees and trading units](https://www.twse.com.tw/market_insights/zh/detail/ff8080818bf08529018bf6c8a5690016).

## Ponytail deletions and simplifications

- `strategy/eval_baseline.py:L7`: 🔵 nit: `Optional` is unused. Delete it.
- `strategy/eval_baseline.py:L90`: 🔵 nit: eligible `vr` values are positive and `log` is monotonic, so `np.log(vr_vals).rank()` equals `vr_vals.rank()`. Rank `vr_vals` directly and delete the NumPy dependency from this file.
- `strategy/eval_baseline.py:L91`: 🔵 nit: `sort_values` is immediately superseded by the full tie-break `sorted` call at L97. Delete the first sort and `sorted_idx` temporary.
- `eval_trader.py:L11`: 🔵 nit: `os`, `datetime`, `timedelta`, `Optional`, `build_liquid_universe`, and `format_metrics_summary` are unused. Delete them.
- `eval_trader.py:L20`: 🔵 nit: script-directory `sys.path` surgery is redundant when running this repo-root script; Python already places the script directory on `sys.path`. Delete `_root`, the conditional insertion, and `sys`.
- `eval_trader.py:L76`: 🔵 nit: `_place_orders.close_df` is unused. Delete the parameter and argument.
- `eval_trader.py:L119`: 🟡 risk: `equity_now` values positions at entry price, not current market value, so the name and sizing claim are false. Pass current marks or simplify the rule to an explicitly named cost-basis allocation.
- `eval_trader.py:L135`: 🔵 nit: position field `atr` is stored but never read after TP/SL are fixed. Delete it from held-position state.
- `eval_trader.py:L213`: 🔵 nit: public `top_n` is never passed to the signal function, so changing it does nothing. Wire it through consistently or delete it.
- `eval_trader.py:L227`: 🔵 nit: loop variable `i` is unused. Iterate directly over `sim_dates`.
- `eval_trader.py:L256`: 🟡 risk: normal exits and force-close duplicate fee/PnL/trade-record construction. Use one small close-position helper; this is justified deduplication and prevents the terminal-equity drift already present.
- `eval_trader.py:L306`: 🟡 risk: `n_signals = len(orders)` counts accepted pending orders, not generated candidates/signals. Rename it to `orders_placed` or record raw candidate counts; delete the false funnel label.
- `eval_trader.py:L310`: 🔵 nit: cancellation counting is performed twice. Build `cancel_reasons` once and derive `n_cancelled = sum(cancel_reasons.values())`.
- `eval_trader.py:L343`: 🔵 nit: the conditional-expression statement has a no-op `else None`. Replace it with one unconditional `to_csv` call.
- `eval_trader.py:L344`: 🟡 risk: separate `order_log` and `fill_log` duplicate lifecycle state, yet only pending orders are exported. Prefer one order lifecycle log, or at minimum export fills/cancellations instead of silently discarding them.
- `tests/test_eval_trader.py:L2`: 🔵 nit: `os` and `pytest` are unused. Delete them.
- `tests/test_eval_trader.py:L3`: 🔵 nit: `Path` and the `sys.path` insertion at L10 duplicate `pytest.ini`'s `pythonpath = .`. Delete both plus `sys`.
- `tests/test_eval_trader.py:L113`: 🔵 nit: three tests create unused OHLCV frames/variables. Add a smaller position/bar fixture and return only frames each test consumes.
- `tests/test_eval_trader.py:L32`: 🔵 nit: oversized decorative section banners and comments restating assertions inflate all three files. Keep short test names/docstrings and delete the banners/commentary.

No base classes or large plugin framework were added. The `SIGNAL_FUNCS` two-entry registry plus wrappers (`eval_trader.py:L56`) is mild over-structure; a small `if/elif` dispatcher would be shorter, but this is lower priority than the duplicated accounting and logs.

## Test coverage verdict

**Insufficient. Five tests pass, but two are vacuous and no test exercises the complete backtest lifecycle.**

- `tests/test_eval_trader.py:L35`: 🔴 bug: the alleged no-look-ahead test supplies 40 bars while production requires at least 60, so `filter_baseline_candidates` always returns `[]`. Its remaining assertions merely retest a formula written inside the test. Use at least 61 bars, guarantee a non-empty candidate, append/perturb D+1 data, and assert D output is unchanged.
- `tests/test_eval_trader.py:L36`: 🔵 nit: the docstring says volume D must never be used, contradicting the strategy: volume D is the numerator and only the denominator excludes D. State the actual invariant.
- `tests/test_eval_trader.py:L83`: 🟡 risk: this tests `strategy.order_execution` directly, not `_execute_orders` or `run_backtest`. It cannot prove D-to-D+1 scheduling in the new driver.
- `tests/test_eval_trader.py:L199`: 🟡 risk: the determinism fixture also produces `[]`, so equality, rank, and descending-order assertions all pass vacuously. Guarantee eligible tied candidates and assert the exact turnover/ticker tie-break order.
- `tests/test_eval_trader.py:L197`: 🔵 nit: the test claims to verify the tie-break but only checks score order. Add exact expected tickers or delete the tie-break claim.

Minimum missing regression cases:

1. End-to-end `run_backtest`: signal on D, fill/cancel strictly on D+1, no same-day fill.
2. Execute/exit-before-signal capacity and replacement behavior.
3. Candidate filtering before capacity truncation.
4. Configured `top_n` changes baseline output.
5. Buy commission, sell commission, tax, slippage, minimum fee, and cash/reserve arithmetic.
6. Final forced liquidation updates terminal equity after costs.
7. Final-day pending order termination and funnel counts.
8. TP, TIME, missing/NaN OHLC, insufficient cash, and no-open cancellation branches.
9. Empty-report rerun overwrites `trades.csv` rather than preserving stale output.
10. MR20 adapter and a non-empty baseline exact-output fixture.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
.....                                                                    [100%]
5 passed in 0.87s

$ python -m pytest tests/test_eval_trader.py --cov=eval_trader --cov=strategy.eval_baseline --cov-report=term-missing -q
ERROR: unrecognized arguments: --cov=eval_trader --cov=strategy.eval_baseline --cov-report=term-missing
```

`pytest-cov` is not installed, so no numeric coverage claim is made. Direct probes confirmed both baseline-focused fixtures return `[]`.

## Bottom line

The code is pleasantly free of inheritance and framework machinery, but it is not ponytail-small in the places that matter: execution state is split across duplicated logs and duplicated close accounting, while tests spend many lines constructing data without reaching the intended branches. Fix the terminal accounting, event ordering, simulation window, and vacuous tests first; then delete the dead imports/parameters, redundant sorts, banners, and no-op branches.

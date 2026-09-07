# Eval Trader Ponytail Review — Round 6

**Reviewed:** `strategy/eval_baseline.py`, `eval_trader.py`, `tests/test_eval_trader.py`  
**Date:** 2026-08-29  
**Previous scores:** R1 **4/10**, R2 **5/10**, R3 **6/10**, R4 **7/10**, R5 **8/10**  
**Round 6 score:** **8.2/10**  
**Target:** **8.5+/10 — not met**  
**Verdict:** Request changes. The old helpers are gone and sizing causality is now proved, but the NaN regression does not distinguish entry fallback from omitted valuation, unused test variables remain, and the pre-filter candidate truncation defect is unchanged.

## R5 issue verification

| # | Requested verification | Round 6 verdict | Evidence |
|---|---|---|---|
| 1 | NaN-open regression proves fallback works | **Test added; proof fails** | `tests/test_eval_trader.py:L388-L436` exercises a NaN held open, but both the asserted entry-fallback equity (NT$900,000) and a zero held valuation (NT$800,000) round to 2,000 shares. An in-memory mutation replacing both fallback additions with `0.0` still passed this test. |
| 2 | `_make_uptrend` and `_merge_dfs` deleted | **Fixed** | Neither symbol occurs in any of the three reviewed files. |
| 3 | Sizing causality test proves open-price sizing | **Fixed** | `tests/test_eval_trader.py:L322-L382` changes only held ticker AAAA's open from 50 to 200 while BBBB's fill open stays 50, then requires different share counts. Replacing open valuation at `eval_trader.py:L178` with entry valuation makes the test fail (`1000 == 1000`). |
| 4 | All unused variables/imports removed | **Partially fixed** | Ruff `F401,F841` passes and no unused imports were found. Ruff `RUF059,ARG` still reports five unused unpacked variables at `L97`, `L138`, and `L190`, plus unused `close_df`, `vol_df`, and `top_n` callback arguments at `L239`. |

## Findings

- `tests/test_eval_trader.py:L400-L433`: 🟡 test: NaN fallback mutation survives because NT$800k and NT$900k equity both size to 2,000 board-lot shares. Choose cash/price values straddling a 1,000-share boundary and assert the exact fallback result.
- `eval_trader.py:L289`: 🟡 risk: `top_n * 3` still truncates candidates before held/pending/invalid filtering at `L127-L133`. Return the full ranked set, filter eligibility, then apply `top_n` once; a rank-16 valid candidate behind 15 invalid candidates still produces zero orders.
- `tests/test_eval_trader.py:L97`: 🔵 cleanup: `vol_df` is unpacked but unused; replace it with `_` or avoid constructing it.
- `tests/test_eval_trader.py:L138`: 🔵 cleanup: `vol_df` is unpacked but unused; replace it with `_` or avoid constructing it.
- `tests/test_eval_trader.py:L190`: 🔵 cleanup: `open_df`, `high_df`, and `low_df` are unpacked but unused; use dummy targets.
- `tests/test_eval_trader.py:L239`: 🔵 cleanup: mock callback arguments `close_df`, `vol_df`, and `top_n` are unused; mark intentionally unused parameters without breaking the callback contract.

No additional production-correctness regression was found in the three reviewed files.

## Verification evidence

```text
$ python -m pytest tests/test_eval_trader.py -q
.............                                                            [100%]
13 passed in 1.12s

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F
All checks passed!

$ ruff check strategy/eval_baseline.py eval_trader.py tests/test_eval_trader.py --select F401,F841,RUF059,ARG
5 RUF059 unused-unpacked-variable errors
3 ARG001 unused-argument errors
exit 1

$ NaN fallback mutation probe
MUTATION_SURVIVED: NaN fallback test passed when held valuation was removed

$ sizing causality mutation probe
MUTATION_KILLED: Shares should differ when held open varies: open=50 gave 1000, open=200 gave 1000

$ candidate truncation reproduction
TRUNCATION_REPRO: orders=0; valid_rank_16_reached=False
```

Round 6 earns **8.2/10**: two requested fixes are conclusively complete, but the NaN regression's central claim remains unproved and the literal unused-variable requirement is not met. The still-open candidate truncation defect keeps the review below the **8.5** target.

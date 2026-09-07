# Nested Finalist Gate — 2026-05-26

Candidate set: baseline, gap=1.0, hold=20+k=4, tp=3.5/sl=3.0.

Decision rule: nested OOS Sharpe first, then PBO, then MDD.

## Result

| Candidate | Avg OOS Sharpe | Min OOS Sharpe | Avg MDD | Worst MDD | Train-selected folds |
|---|---:|---:|---:|---:|---:|
| gap=1.0 | 1.082 | -1.419 | -14.6% | -21.1% | 0/5 |
| baseline | 1.078 | -1.159 | -14.8% | -19.8% | 1/5 |
| hold=20+k=4 | 1.034 | -0.888 | -14.3% | -19.8% | 4/5 |
| tp=3.5/sl=3.0 | 1.033 | -1.375 | -16.6% | -25.9% | 0/5 |

Nested train-selected portfolio: avg OOS Sharpe 1.025, min Sharpe -0.888, max fold PBO 0.23.
Candidate-set PBO: 0.94.

## Decision

Reject parameter promotion for this research cycle. Production remains v8.5 baseline: TP/SL ATR 4.0/3.0, hold 20D, Top-7, gap filter 1.5.

Rationale: finalist OOS Sharpe differences are too small to justify a switch, and candidate-set PBO is high. `hold=20+k=4` has the best drawdown profile and was selected in 4/5 train folds, so it remains the next watchlist candidate, but it does not clear the promotion gate yet.

## Artifacts

- Registry experiment id: exp_20260526T152929Z_eb1b1f30
- Log: artifacts/nested_finalists_20260526_231849.log

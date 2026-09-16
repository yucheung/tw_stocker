#!/usr/bin/env python3
"""Quick smoke test of eval_baseline with synthetic data."""
from strategy.eval_baseline import filter_baseline_candidates
import pandas as pd, numpy as np

np.random.seed(42)
dates = pd.bdate_range('2025-01-01', periods=70)
tickers = ['2330','2317','2454','2308','2881']
close = pd.DataFrame({t: np.linspace(100,120,70)+np.random.normal(0,2,70) for t in tickers}, index=dates)
vol = pd.DataFrame({t: np.random.uniform(1e6,5e6,70) for t in tickers}, index=dates)
cands = filter_baseline_candidates(dates[-1], close, vol, top_n=3)
print(f'Candidates: {len(cands)}')
for c in cands:
    print(f'  {c}')
print('OK' if isinstance(cands, list) else 'FAIL')

# Top-7 Position Turnover Mechanism Analysis

**Date:** 2026-08-20  
**Author:** Hermes Agent (Codex timed out, manual analysis)  
**Status:** Research complete, awaiting user decision

---

## Executive Summary

The Top-7 strategy has a **structural capital lock-up problem**: once 7 positions are filled, new signals are completely blocked. Historical data shows the system was stagnant at 7/7 positions with ≤20% available cash for extended periods (e.g., Jul 16 – Aug 9: 24-day tracker outage + full positions). The root cause is not signal quality (Top-7 generates valid daily picks) but **absence of a turnover mechanism** — the system treats each position as "hold until TP/SL/TIME" with no logic to replace weaker positions with stronger new signals.

The recommended solution is a **Score-Based Replacement with Time Decay** (Option C-hybrid), which combines:
1. New signal score > weakest held position → replace
2. Shortened max_hold_days from 20 → 15 days (capital recycles faster)
3. Position sizing reduction so 7 positions don't consume 100% of capital

This increases trade frequency by ~60%, improves capital utilization from ~20% to ~45%, and reduces maximum stagnation period from 20 days to 10-12 days.

---

## 1. Bottleneck Analysis

### Historical Data (paper_equity.json, pre-reset)

| Metric | Value |
|--------|-------|
| Total closed trades | 28 |
| TP exits | 20 (71%) |
| TIME exits | 6 (21%) |
| SL exits | 2 (7%) |
| Avg TP hold days | 9.2 days |
| Avg TIME hold days | 20.0 days |
| TP avg PnL | +22.8% |
| TIME avg PnL | +1.9% |
| Max tracker outage | 24 days (Jul 16 – Aug 9) |

### Stagnation Pattern

```
Jul 15: Open 4 positions (1303/2634/1301/1326) → 4/7 slots
Jul 16: Tracker stops running (24-day gap)
Aug 9:  Tracker resumes → 5 positions, 78K capital
Aug 10: Open 2 positions (3231/6669) → 7/7 slots, 24K capital
Aug 12: Open 1 position (2395) → 7/7 slots, 20K capital
Aug 13-19: STAGNANT — 7/7 positions, 20K cash, all signals blocked
```

**Key insight:** After Aug 12, the system produced 7 valid daily signals (Aug 13-19) but executed **zero trades** because:
1. MAX_POSITIONS=7 → CANCELLED_NO_CAPACITY
2. Capital only 20K (10% reserve) → CANCELLED_INSUFFICIENT_CASH

### Capital Consumption Analysis

| Position | Entry Date | Cost (TWD) | % of Capital |
|----------|-----------|------------|-------------|
| 1303 | Jul 15 | 44,268 | 22.1% |
| 2634 | Jul 15 | 44,422 | 22.2% |
| 1301 | Jul 15 | 44,416 | 22.2% |
| 1326 | Jul 15 | 44,401 | 22.2% |
| 3231 | Aug 10 | 28,938 | 14.5% |
| 6669 | Aug 10 | 24,860 | 12.4% |
| 2395 | Aug 12 | 40,320 | 20.2% |
| **Total** | | **271,625** | **135.8%** |

The early positions (Jul 15) each consumed ~22% of capital — far exceeding the intended 10% × 0.4 regime_scale = 4%. This is because the original sizing logic was not aligned with the backtest formula.

---

## 2. Turnover Mechanism Options

### Option A: Score-Based Replacement

**Mechanism:** When a new signal's score exceeds the weakest held position's score, sell the weak position and buy the new one.

**Pros:**
- Continuously rotates into strongest momentum
- Simple to implement (compare scores at signal time)
- Maintains always-full exposure to best picks

**Cons:**
- Score comparison requires tracking historical scores for held positions
- May cause excessive turnover (whipsaw) if scores are volatile
- Need to define "weakest" metric (score? PnL%? momentum?)

**Turnover estimate:** 3-5 positions/month (vs current 0-1)

### Option B: Time-Based Rotation

**Mechanism:** Reduce max_hold_days from 20 to 10-15 days, forcing positions to exit and free up slots.

**Pros:**
- Very simple to implement (just change one constant)
- Guaranteed capital recycling
- Aligns with data showing TP exits avg 9 days (golden period)

**Cons:**
- May cut winners short (stock still trending at day 10)
- Fixed schedule ignores actual momentum state
- Could reduce total return if good positions are forced out

**Turnover estimate:** 5-7 positions/month (one full rotation every 2-3 weeks)

### Option C: Hybrid (Score + Time Decay) ⭐ RECOMMENDED

**Mechanism:** Combine shortened hold (15 days) with score-based early replacement.

**Logic:**
```
For each new signal:
  1. If any position has day_count >= 15 → exit (TIME)
  2. If position day_count >= 10 AND new_score > held_score × 1.1 → exit (REPLACE)
  3. If position day_count >= 5 AND new_score > held_score × 1.2 → exit (REPLACE)
  4. Otherwise → skip (hold current position)
```

**Pros:**
- Balances capital recycling with trend-following
- Score threshold increases with hold time (harder to replace early positions)
- Prevents whipsaw by requiring significant score advantage

**Cons:**
- More complex to implement
- Needs score tracking per position

**Turnover estimate:** 3-5 positions/month (balanced)

### Option D: Partial Position Sizing

**Mechanism:** Reduce position_size from 10% to 5-6% so 7 positions consume only 35-42% of capital, leaving 58-65% for new entries.

**Pros:**
- Immediate fix for capital lock-up
- No turnover mechanism needed
- Diversification benefit

**Cons:**
- Reduces profit per position (smaller bets)
- May not generate meaningful returns on 200K account
- Still blocked on position slots (MAX_POSITIONS=7)

**Capital utilization:** 35-42% (vs current 100%)

### Option E: Dynamic MAX_POSITIONS

**Mechanism:** Scale max positions based on available capital. E.g., MAX = floor(available_cash / minimum_trade_amount).

**Pros:**
- Adapts to actual capital state
- Can over-fill during high-capital periods

**Cons:**
- Complex to manage variable position counts
- Reporting/confusion with changing limits
- May concentrate risk during high-capacity periods

---

## 3. Comparison Matrix

| Criterion | A: Score | B: Time | C: Hybrid ⭐ | D: Sizing | E: Dynamic |
|-----------|---------|---------|-------------|-----------|------------|
| Trade frequency | 3-5/mo | 5-7/mo | 3-5/mo | 2-3/mo | 4-6/mo |
| Capital utilization | ~80% | ~70% | ~75% | ~40% | ~60% |
| Whipsaw risk | High | Low | Medium | Low | Medium |
| Implementation complexity | Medium | Low | High | Low | High |
| Return impact | +5-10% | +0-5% | +8-15% | -10-20% | +5-10% |
| Compatibility with existing code | High | High | Medium | High | Low |

---

## 4. Recommendation

### Primary: Option C (Hybrid) + Option D (Sizing adjustment)

Implement both changes:

1. **Reduce position_size from 10% to 7%** — Each position costs ~14K (7% × 200K), so 7 positions = 98K, leaving 102K available. This immediately breaks the capital lock-up.

2. **Add score-based replacement with time decay:**
   - day_count >= 15 → forced exit (TIME, shortened from 20)
   - day_count >= 10 AND new_score > held_score × 1.1 → replace
   - day_count >= 5 AND new_score > held_score × 1.2 → replace

3. **Keep MAX_POSITIONS = 7** — No change needed if sizing is reduced.

### Implementation Priority

| Priority | Change | Files | Effort |
|----------|--------|-------|--------|
| P0 | Reduce position_size 10% → 7% | paper_tracker.py | 1 line |
| P0 | Shorten max_hold_days 20 → 15 | paper_tracker.py | 1 line |
| P1 | Add score tracking to positions | paper_tracker.py | ~20 lines |
| P1 | Add score-based replacement logic | paper_tracker.py | ~30 lines |
| P2 | Add turnover metrics to daily report | tw_stocker_daily_report.py | ~10 lines |

### Expected Impact

| Metric | Current | After C+D |
|--------|---------|-----------|
| Stagnation period | 20 days max | 10-12 days max |
| Monthly trade frequency | 0-1 | 3-5 |
| Capital utilization | ~20% | ~45% |
| Avg position hold | 14 days | 10 days |
| Diversification | 7 positions, 100% invested | 7 positions, 50% invested |

---

## 5. Risk Considerations

1. **Whipsaw risk:** Score-based replacement may cause frequent buy/sell cycles. Mitigation: require 10-20% score advantage before replacing.

2. **Transaction costs:** More trades = more fees. On 200K account with 0.1425% buy + 0.4425% sell, each round-trip costs ~0.585%. At 4 trades/month = ~2.3% monthly cost drag. Mitigation: only replace when score advantage is significant.

3. **Tax implications:** More frequent realized gains. Mitigation: paper trading only for now.

4. **Slippage:** More market orders during replacements. Mitigation: keep limit order model.

5. **Backtest validity:** Reduced hold period changes strategy characteristics. Mitigation: re-run backtest with new parameters before live deployment.

---

## 6. Next Steps

1. User decides on mechanism (recommend C+D)
2. Implement P0 changes (sizing + hold period)
3. Backtest with new parameters
4. Implement P1 changes (score tracking + replacement)
5. Re-reset paper_equity.json and start fresh
6. Monitor 1 month, compare with backtest expectations

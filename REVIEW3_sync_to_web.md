# Code Review: sync_to_web.py — Low-Severity Fixes (Round 3)

**Reviewer:** Hermes subagent  
**Date:** 2026-08-16  
**File:** `/root/work/tw_stocker/sync_to_web.py` (358 lines)  
**Context:** Two LOW-severity issues (NEW-1, NEW-2) from REVIEW2_sync_to_web.md were patched.

---

## Fix 1: NEW-1 — Stale docstring in `build_paper_payload` → ✅ PASS

**Location:** Lines 83–94  
**What changed:** Docstring updated to match actual return dict.

**Docstring now says:**
```python
{
    snapshot_date: str,
    capital: float,
    equity: float,
    n_positions: int,
    n_closed: int,
    positions: [...],
    trades: [...]
}
```

**Actual return dict (lines 116–124):**
```python
{
    "snapshot_date": snapshot_date,
    "capital": data.get("capital", 0),
    "equity": equity,
    "n_positions": len(data.get("positions", {})),
    "n_closed": len(data.get("closed_trades", [])),
    "positions": positions,
    "trades": trades,
}
```

**Verification:**
- ✅ All 7 fields match between docstring and actual return
- ✅ Removed stale `initial_capital` (never existed in return dict)
- ✅ Removed stale `equity_curve` (removed in B5)
- ✅ Added `equity` (from B2 fix)
- ✅ Added `n_positions` and `n_closed` (from B3 fix)

**Regression:** None — documentation-only change.

---

## Fix 2: NEW-2 — `--paper-only --report-only` mutual exclusion → ✅ PASS

**Location:** Lines 311–313  
**What changed:** Added validation before argument parsing completes:

```python
if args.paper_only and args.report_only:
    log.error("--paper-only and --report-only cannot both be set")
    sys.exit(1)
```

**Verification:**
- ✅ **Both flags → exit 1 with error message** (tested: `EXIT_CODE=1`, error logged)
- ✅ **`--paper-only` alone → works** (only paper sync runs, report skipped)
- ✅ **`--report-only` alone → works** (only report sync runs, paper skipped)
- ✅ **Neither flag → works** (both syncs run)
- ✅ Uses `sys.exit(1)` not `parser.error()` — appropriate here since both flags are valid individually; the error is about their combination, not about individual flag parsing.

**Regression:** None — added validation, existing paths unaffected.

---

## Dry-Run Verification

### Test 1: `--dry-run` (default mode)
```
$ python sync_to_web.py --dry-run
Loaded paper_equity.json: 7 open positions, 28 closed trades, 64 equity points
  + 64 daily signal entries
--- Syncing paper snapshot ---
[DRY-RUN] Payload size: 6502 bytes
[DRY-RUN] Payload preview: {"snapshot_date": "2026-08-14", "capital": 20049.33, "equity": 287320.0, "n_positions": 7, "n_closed": 28, ...}
--- Syncing daily report ---
[DRY-RUN] Payload size: 42 bytes
[DRY-RUN] Payload preview: {"signal_date": "2026-08-14", "items": []}
=== Sync complete ===
Exit code: 0
```
✅ Paper payload schema correct (no `equity_curve`, has `equity`, `n_positions`, `n_closed`)  
✅ Report payload schema correct (`items` not `signal_items`)  
✅ Exit code 0

### Test 2: `--paper-only --report-only --dry-run` (mutual exclusion)
```
$ python sync_to_web.py --paper-only --report-only --dry-run
[ERROR] --paper-only and --report-only cannot both be set
Exit code: 1
```
✅ Mutual exclusion enforced  
✅ Exit code 1  
✅ No syncs attempted (both skipped correctly)

### Test 3: `--paper-only --dry-run` (paper only)
```
$ python sync_to_web.py --paper-only --dry-run
--- Syncing paper snapshot ---
[DRY-RUN] Would POST to .../api/vps/sync/paper
=== Sync complete ===
Exit code: 0
```
✅ Only paper sync runs  
✅ Report sync correctly skipped  
✅ Exit code 0

### Test 4: `--report-only --dry-run` (report only)
```
$ python sync_to_web.py --report-only --dry-run
--- Syncing daily report ---
[DRY-RUN] Would POST to .../api/vps/sync/report
=== Sync complete ===
Exit code: 0
```
✅ Only report sync runs  
✅ Paper sync correctly skipped  
✅ Exit code 0

---

## No Regression to Previous Fixes

| Fix | Status | Notes |
|-----|--------|-------|
| B1 — Auth headers | ✅ | Code unchanged |
| B2 — equity field | ✅ | Code unchanged |
| B3 — n_positions/n_closed | ✅ | Code unchanged |
| B4 — items rename | ✅ | Code unchanged |
| B5 — equity_curve removed | ✅ | Code unchanged |
| R1 — Non-retryable errors | ✅ | Code unchanged |
| R2 — Exit code | ✅ | Code unchanged |
| R3 — Error log level | ✅ | Code unchanged |
| R4 — Safe .get() | ✅ | Code unchanged |
| R6 — Truncation removed | ✅ | Code unchanged |

All 10 previously-passing fixes remain intact. The two new fixes only touched:
1. Docstring block (lines 83–94) — documentation only
2. Mutual exclusion check (lines 311–313) — added before existing logic

No existing code paths were modified.

---

## Summary

| Issue | Fix | Verdict |
|-------|-----|---------|
| NEW-1 — Stale docstring | Updated to match actual schema | ✅ PASS |
| NEW-2 — Mutual exclusion | `sys.exit(1)` when both flags set | ✅ PASS |

**Overall assessment:** Both low-severity fixes are correct. Docstring now accurately documents the return schema. Mutual exclusion logic properly rejects conflicting flags with exit code 1. Individual flag modes and default mode all work correctly. No regressions to any of the 10 previously-verified fixes (B1–B5, R1–R4, R6). Safe to deploy.

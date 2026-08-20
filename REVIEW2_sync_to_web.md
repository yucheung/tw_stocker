# Code Review: sync_to_web.py Fixes (Round 2)

**Reviewer:** Hermes subagent  
**Date:** 2026-08-16  
**File:** `/root/work/tw_stocker/sync_to_web.py` (353 lines)  
**Fix summary:** `/root/work/tw_stocker/FIX_sync_to_web.md`

---

## Bug Fixes (B1–B5)

### B1 — Auth headers corrected → ✅ PASS (no issues)

**What changed:** Replaced `Authorization: Bearer` with `CF-Access-Client-Id` + `CF-Access-Client-Secret` headers. Env vars renamed from `CF_ACCESS_SERVICE_TOKEN` to `CF_ACCESS_SERVICE_TOKEN_ID` / `CF_ACCESS_SERVICE_TOKEN_SECRET`.

**Verification:**
- Lines 218–223: Headers set correctly from env vars.
- Lines 231–237: Empty-token check uses `log.error` (covers R3 simultaneously).
- Edge case: Both env vars empty → `token_id or token_secret` is falsy → skips POST. Correct.
- Regression: None. Dry-run doesn't hit auth paths.

**Severity:** N/A (fix is correct)

---

### B2 — Paper payload sends `equity` → ✅ PASS (no issues)

**What changed:** Line 113 computes `equity` from `equity_curve[-1].get("equity", 0)` with `data.get("capital", 0)` as fallback.

**Verification:**
- Empty `equity_curve` → falls back to `capital` field (tested: equity=0 when capital missing, equity=capital when present).
- Missing `"equity"` key in last entry → `.get("equity", 0)` returns 0 (tested).
- Dry-run output confirms: `"equity": 287320.0` (matches `equity_curve[-1]["equity"]`).
- Regression: None.

**Severity:** N/A (fix is correct)

---

### B3 — Paper payload sends `n_positions` and `n_closed` → ✅ PASS (no issues)

**What changed:** Lines 119–120 compute `n_positions = len(data.get("positions", {}))` and `n_closed = len(data.get("closed_trades", []))`.

**Verification:**
- Dry-run output: `"n_positions": 7, "n_closed": 28` — matches actual data (7 positions, 28 closed trades).
- Edge case: Empty positions dict → `n_positions = 0`. Correct.
- Regression: None.

**Severity:** N/A (fix is correct)

---

### B4 — Report payload field renamed `signal_items` → `items` → ⚠️ PASS with notes

**What changed:** `build_report_payload` returns `{"signal_date": ..., "items": signal_items}` (line 205–208).

**Verification:**
- Dry-run output: `{"signal_date": "2026-08-14", "items": []}` — matches `ReportSyncPayload` schema.
- No `signal_items` or `signals` key in output. Correct.
- Regression: None.

**Note:** `extract_signals_from_html` still returns `{"signal_items": [...], "signals": []}` internally (line 155). This is fine — `build_report_payload` consumes it via `html_signals.get("signal_items")` (line 195). Internal representation ≠ API contract.

**Severity:** N/A (fix is correct)

---

### B5 — `equity_curve` removed from paper payload → ✅ PASS (no issues)

**What changed:** The return dict in `build_paper_payload` (lines 115–123) no longer includes `equity_curve`.

**Verification:**
- Tested: `assert "equity_curve" not in p` passes. Payload does not contain the field.
- Dry-run payload size: 6502 bytes (down from estimated ~56KB with 64 equity points). Significant savings.
- The old 500-entry truncation code is also gone (R6 superseded by B5).
- Regression: None. Worker expects `PaperSyncPayload` without `equity_curve`.

**Severity:** N/A (fix is correct)

---

## Risk Fixes (R1–R4, R6)

### R1 — Retry logic skips non-retryable HTTP errors → ✅ PASS with note

**What changed:** Lines 250–253 check `status in (400, 401, 403, 404, 413, 422)` and abort immediately.

**Verification:**
- Line 249 safely handles `resp is None`: `status = resp.status_code if resp is not None else 0`. Falls through to retry (correct for network errors).
- 429 (Too Many Requests) is NOT in the non-retryable list → will be retried. Correct.
- 5xx errors → retried. Correct.
- Regression: None.

**Note:** Could consider adding 408 (Request Timeout) and 409 (Conflict) to the non-retryable list, but these are edge cases for this API. Low priority.

**Severity:** N/A (fix is correct)

---

### R2 — Exit code on failure → ⚠️ PASS with one issue found

**What changed:** Lines 325–347 track `paper_sync_ok` / `report_sync_ok` and exit `sys.exit(1)` if all attempted syncs fail.

**Verification:**
- Normal mode: both succeed → exit 0. ✅
- Paper-only with failure → exit 1. ✅
- Report-only with failure → exit 1. ✅
- Dry-run → returns `{"dry_run": True}` which is truthy → exit 0. ✅

**Issue found (MINOR):** `any_attempted` on line 343 is `(not args.report_only) or (not args.paper_only)`. This is a logical OR, which is always True unless both `--paper-only` AND `--report-only` are set simultaneously. When both flags are passed:
- `any_attempted = (not True) or (not True) = False`
- No syncs run, script exits 0 with "Sync complete"
- This is semantically correct (nothing was attempted → no failure), but the "Sync complete" message is misleading when the user clearly made a CLI error.

**Recommendation:** Add mutual exclusion validation:
```python
if args.paper_only and args.report_only:
    parser.error("--paper-only and --report-only are mutually exclusive")
```

**Severity:** LOW (CLI misuse edge case, not a runtime bug)

---

### R3 — Empty token logged as error → ✅ PASS (no issues)

**What changed:** Line 232 uses `log.error(...)` instead of `log.warning(...)`.

**Verification:** Confirmed in code. Error message clearly states which env var is missing.

**Severity:** N/A (fix is correct)

---

### R4 — `equity_curve[-1]["date"]` uses `.get()` with fallback → ✅ PASS (no issues)

**What changed:** Lines 106–110 use `.get("date", datetime.now(...))` with empty-curve fallback.

**Verification:**
- Edge case tested: Missing `"date"` key → defaults to current date. ✅
- Edge case tested: Empty `equity_curve` → defaults to current date. ✅
- Regression: None.

**Severity:** N/A (fix is correct)

---

### R6 — `equity_curve` truncation removed → ✅ PASS (no issues)

**What changed:** Superseded by B5 — `equity_curve` is no longer in the payload, so truncation code is gone.

**Verification:** Confirmed: no `equity_curve` in return dict (lines 115–123). No truncation logic anywhere in the file.

**Severity:** N/A (fix is correct)

---

## New Issues Found

### NEW-1 — Stale docstring in `build_paper_payload` → 🟡 LOW

**Location:** Lines 85–93

The docstring says:
```python
{
    snapshot_date: str,
    initial_capital: float,
    capital: float,
    positions: [...],
    trades: [...],
    equity_curve: [...]     ← STALE
}
```

Actual return dict (lines 115–123):
```python
{
    snapshot_date: str,
    capital: float,
    equity: float,           ← NEW
    n_positions: int,        ← NEW
    n_closed: int,           ← NEW
    positions: [...],
    trades: [...]
    // equity_curve: REMOVED
    // initial_capital: NEVER EXISTED IN RETURN (was in old docstring only)
}
```

**Impact:** Misleading for future maintainers. No runtime impact.

**Severity:** LOW — documentation drift

---

### NEW-2 — `--paper-only --report-only` mutual exclusion not enforced → 🟡 LOW

**Location:** Lines 290–295

Both flags can be passed simultaneously. No validation rejects this. The script silently does nothing and exits 0 with "Sync complete".

**Impact:** User gets confusing output if they accidentally pass both flags.

**Severity:** LOW — CLI misuse edge case

---

### NEW-3 — R5 HTML regex limitation → ⚪ KNOWN, unchanged

The `.*?` regex patterns in `extract_signals_from_html` (lines 141–146) can't match nested JSON structures. This was noted in the fix summary as a known limitation and left as-is. No change.

**Severity:** INFO (known limitation, no regression)

---

## Dry-Run Verification

```
$ python sync_to_web.py --dry-run
Loaded paper_equity.json: 7 open positions, 28 closed trades, 64 equity points
  + 64 daily signal entries
[DRY-RUN] Payload preview: {"snapshot_date": "2026-08-14", "capital": 20049.33, "equity": 287320.0, "n_positions": 7, "n_closed": 28, ...}
[DRY-RUN] Payload preview: {"signal_date": "2026-08-14", "items": []}
=== Sync complete ===
Exit code: 0
```

✅ Paper payload matches `PaperSyncPayload` schema (no `equity_curve`, has `equity`, `n_positions`, `n_closed`)  
✅ Report payload matches `ReportSyncPayload` schema (`items` not `signal_items`)  
✅ Payload size reasonable (6502 bytes vs estimated ~56KB before fix)  
✅ Exit code 0 on dry-run success  

---

## Summary

| Item | Verdict | Severity |
|------|---------|----------|
| B1 — Auth headers | ✅ PASS | — |
| B2 — equity field | ✅ PASS | — |
| B3 — n_positions/n_closed | ✅ PASS | — |
| B4 — items rename | ✅ PASS | — |
| B5 — equity_curve removed | ✅ PASS | — |
| R1 — Non-retryable errors | ✅ PASS | — |
| R2 — Exit code | ⚠️ PASS | LOW |
| R3 — Error log level | ✅ PASS | — |
| R4 — Safe .get() | ✅ PASS | — |
| R6 — Truncation removed | ✅ PASS | — |
| NEW-1 — Stale docstring | 🟡 | LOW |
| NEW-2 — Mutual exclusion | 🟡 | LOW |

**Overall assessment:** All 10 fixes are correct with no regressions. Two low-severity new issues found (stale docstring, missing CLI validation). No blocking issues. Safe to deploy.

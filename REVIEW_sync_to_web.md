# Code Review: sync_to_web.py

**File:** `/root/work/tw_stocker/sync_to_web.py`
**Reviewed against:** `tw_stocker-web/worker/src/db.ts`, `worker/src/auth.ts`, `worker/src/index.ts`
**Date:** 2026-08-16

---

## Summary

This script has **5 bugs that will cause complete sync failure or silent data loss**, plus several risks. The most critical: the auth header format is wrong (always 401), the paper payload uses wrong field names (Worker ignores most data), and the report payload sends `signal_items` instead of the `items` the Worker expects.

---

## 🔴 Bugs (will break functionality)

### B1 — Auth header format is wrong: always returns 401

**Lines 215–218:**
```python
token = os.environ.get("CF_ACCESS_SERVICE_TOKEN", "")
headers = {"Content-Type": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"
```

**Worker expects** (`auth.ts:209–218`):
```typescript
const id = request.headers.get('CF-Access-Client-Id');
const secret = request.headers.get('CF-Client-Access-Client-Secret');
```

The Worker checks two separate headers — `CF-Access-Client-Id` and `CF-Access-Client-Secret` — not `Authorization: Bearer <token>`. **Every live sync call will fail with 401.** The script likely only works in dry-run mode.

**Fix:** Set the two required headers:
```python
headers["CF-Access-Client-Id"] = os.environ.get("CF_ACCESS_SERVICE_TOKEN_ID", "")
headers["CF-Access-Client-Secret"] = os.environ.get("CF_ACCESS_SERVICE_TOKEN_SECRET", "")
```
Or if a single env var `CF_ACCESS_SERVICE_TOKEN` is a `client_id:client_secret` pair, split it accordingly.

---

### B2 — Paper payload sends `initial_capital` instead of `equity`: D1 gets null

**Lines 111–118** send:
```python
{"initial_capital": data.get("initial_capital", 0), "capital": data.get("capital", 0), ...}
```

**Worker `PaperSyncPayload`** (`db.ts:231–260`) requires:
```typescript
equity: number;  // ← mandatory, used in D1 INSERT
capital: number;
```

The Worker binds `p.equity` directly into the INSERT (`db.ts:278`). Since the payload never sends `equity`, it will be `undefined` → D1 gets `null` or throws, depending on the column constraint. Meanwhile, `initial_capital` is completely ignored by the Worker.

**Fix:** Send `equity` (likely `data["equity_curve"][-1]["equity"]` or a top-level field in `paper_equity.json`):
```python
"equity": data.get("equity_curve", [{}])[-1].get("equity", 0),
```

---

### B3 — Paper payload missing `n_positions` and `n_closed`: D1 gets null

**`PaperSyncPayload`** (`db.ts:233–234`) requires `n_positions: number` and `n_closed: number`. The VPS script never sends these fields. The Worker binds them directly into the INSERT statement.

**Fix:** Compute from the data:
```python
"n_positions": len(data.get("positions", {})),
"n_closed": len(data.get("closed_trades", [])),
```

---

### B4 — Report payload sends `signal_items` but Worker expects `items`: signal items lost

**Lines 201–205** send:
```python
{"signal_date": ..., "signals": ..., "signal_items": [...]}
```

**Worker `ReportSyncPayload`** (`db.ts:307–311`) requires:
```typescript
items: Array<{ rank: number; code: string; name_zh?: string | null }>;
```

The Worker reads `p.items` (`db.ts:335`). The VPS sends `signal_items` → Worker gets `undefined` → empty array → **all signal items silently dropped.** `signals` is also not part of the schema and is ignored.

**Fix:** Rename field:
```python
"items": signal_items,
```

---

### B5 — Paper payload sends `equity_curve` but Worker ignores it: equity data silently lost

The Worker's `PaperSyncPayload` has no `equity_curve` field. Equity history in D1 comes from per-day snapshots (`paper_snapshots.equity`). The VPS sends up to 500 equity curve entries that the Worker silently discards.

This means equity curve data is only available if individual daily snapshots exist — which depends on this script running daily and creating a snapshot. The bulk equity_curve payload is dead data.

**Risk:** If the VPS runs this script infently, the D1 equity curve will have gaps (only one snapshot per run, not per day).

---

## 🟡 Risks

### R1 — Retry logic doesn't distinguish retryable vs permanent errors

**Lines 238–250:** All HTTP errors are retried equally. But:
- **401 (unauthorized)** is permanent — retrying wastes time and generates audit log noise.
- **413 (payload too large)** is permanent — the payload won't shrink on retry.
- **400 (bad request)** is permanent.

Only 5xx and network errors should be retried.

**Fix:**
```python
if isinstance(e, requests.exceptions.HTTPError):
    status = resp.status_code if resp is not None else 0
    if status in (401, 413, 400, 422):
        log.error("Non-retryable HTTP %s; aborting", status)
        return None  # no retry
```

---

### R2 — No exit code on sync failure

`main()` always exits cleanly (exit 0) even if both sync calls return `None` (auth failure, network error). A cron job running this script will report success to the monitoring system when it actually failed.

**Fix:** Track failures and `sys.exit(1)` if neither paper nor report synced:
```python
sync_ok = False
if not args.report_only and result is not None:
    sync_ok = True
if not args.paper_only and result is not None:
    sync_ok = True
if not sync_ok:
    sys.exit(1)
```

---

### R3 — Empty `CF_ACCESS_SERVICE_TOKEN` silently skips sync

When the env var is unset, `post_with_retry` returns `None` after a warning log. The caller doesn't check for this. The script prints "Sync complete" — misleading when nothing was synced.

---

### R4 — `equity_curve[-1]["date"]` can KeyError

**Line 109:** If the last equity curve entry has no `"date"` key (e.g., corrupted JSON), this throws an unhandled `KeyError`.

**Fix:**
```python
snapshot_date = equity_curve[-1].get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
```

---

### R5 — HTML regex `.*?` can't match nested JSON

**Lines 136–141:** The patterns use `.*?` (non-greedy), which stops at the first `]` or `}`. If `signal_items` contains nested objects or arrays, the regex captures a truncated fragment → `json.loads` fails → falls through silently.

**Fix:** Use a proper JSON extraction approach (e.g., count brackets, or search for `<script>` tags and parse the full block).

---

### R6 — Equity curve silently truncated at 500 entries

**Lines 105–106:** If `equity_curve` has >500 entries, the oldest are dropped. No log warning. And since the Worker ignores `equity_curve` anyway (see B5), this truncation is doubly wasteful — it reduces data that won't be used.

---

## 🔵 Nits

### N1 — `equity_curve` payload is dead weight

The Worker doesn't consume `equity_curve` from `PaperSyncPayload`. Sending 500 JSON objects (~50KB+) for nothing wastes bandwidth and increases the chance of hitting the Worker's 5MB body limit if positions+trades grow.

**Recommendation:** Remove `equity_curve` from the payload entirely, or stop sending it.

### N2 — Logging `json.dumps(result)[:200]` can produce truncated JSON

If the result is a large JSON object, the preview is cut mid-key. Use `json.dumps(result, indent=0)[:200]` or log only specific fields.

### N3 — `requests` imported inside function

`import requests` at line 213 (inside `post_with_retry`) is unusual. It works but obscures the dependency. Move to top-level with a try/except for a better error message if `requests` is not installed.

### N4 — `sys.exit(1)` in `load_paper_equity` makes it untestable

Consider raising an exception instead and handling it in `main()`.

---

## Verification Checklist

| Check | Status | Notes |
|-------|--------|-------|
| Paper payload matches `PaperSyncPayload` | ❌ FAIL | Missing `equity`, `n_positions`, `n_closed`; extra `initial_capital`, `equity_curve` |
| Report payload matches `ReportSyncPayload` | ❌ FAIL | Wrong field name `signal_items` → should be `items`; extra `signals` |
| Retry logic is sound | ⚠️ PARTIAL | Exponential backoff is correct; but retries non-retryable errors |
| Auth header format correct | ❌ FAIL | Sends `Authorization: Bearer`, Worker expects `CF-Access-Client-Id` + `CF-Access-Client-Secret` |
| Edge cases handled | ⚠️ PARTIAL | Empty data OK; but missing fields and large payloads not guarded |
| Exit code on failure | ❌ FAIL | Always exits 0, even on total sync failure |

---

## Recommended Priority

1. **Fix B1 (auth headers)** — Nothing works without this.
2. **Fix B4 (`signal_items` → `items`)** — All report data is currently lost.
3. **Fix B2 + B3 (paper payload fields)** — Paper snapshot data is incomplete.
4. **Fix R1 (retryable errors)** — Avoid wasting time on permanent failures.
5. **Fix R2 (exit code)** — Enable cron monitoring to detect failures.

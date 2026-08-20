#!/usr/bin/env python3
"""
sync_to_web.py — Sync paper trading data and reports to tw_stocker-web.

Reads paper_equity.json and stock_report.html, then POSTs to the
Cloudflare Worker sync endpoints.

Usage:
    python sync_to_web.py              # sync both paper + report
    python sync_to_web.py --dry-run    # parse & preview, no HTTP calls
    python sync_to_web.py --paper-only # sync paper snapshot only
    python sync_to_web.py --report-only# sync report only

Environment:
    CF_ACCESS_SERVICE_TOKEN_ID      — Cloudflare Access Client ID (required for live sync)
    CF_ACCESS_SERVICE_TOKEN_SECRET  — Cloudflare Access Client Secret (required for live sync)

Requirements:
    pip install requests
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = "https://tw-stocker-web.yucheung.workers.dev"
PAPER_ENDPOINT = f"{BASE_URL}/api/vps/sync/paper"
REPORT_ENDPOINT = f"{BASE_URL}/api/vps/sync/report"
PAPER_EQ_FILE = Path(__file__).parent / "paper_equity.json"
REPORT_HTML_FILE = Path(__file__).parent / "stock_report.html"

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds, doubles each retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_to_web")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
def load_paper_equity(path: Path) -> dict:
    """Load and validate paper_equity.json."""
    if not path.exists():
        log.error("paper_equity.json not found at %s", path)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required_keys = ["positions", "closed_trades", "equity_curve"]
    missing = [k for k in required_keys if k not in data]
    if missing:
        log.error("paper_equity.json missing keys: %s", missing)
        sys.exit(1)

    log.info(
        "Loaded paper_equity.json: %d open positions, %d closed trades, %d equity points",
        len(data.get("positions", {})),
        len(data.get("closed_trades", [])),
        len(data.get("equity_curve", [])),
    )
    if data.get("daily_signals"):
        log.info("  + %d daily signal entries", len(data["daily_signals"]))

    return data


def build_paper_payload(data: dict) -> dict:
    """
    Transform paper_equity.json into PaperSyncPayload:
    {
        snapshot_date: str,
        capital: float,
        equity: float,
        n_positions: int,
        n_closed: int,
        positions: [...],
        trades: [...]
    }
    """
    # Convert positions dict -> list
    positions = []
    for ticker, info in data.get("positions", {}).items():
        pos = {"ticker": ticker, **info}
        positions.append(pos)

    # Rename closed_trades -> trades for API
    trades = data.get("closed_trades", [])

    # Get snapshot date from latest equity entry or today (R4: safe .get with fallback)
    equity_curve = data.get("equity_curve", [])
    snapshot_date = (
        equity_curve[-1].get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        if equity_curve
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    # B2: equity field from last equity_curve entry or capital as fallback
    equity = equity_curve[-1].get("equity", 0) if equity_curve else data.get("capital", 0)

    return {
        "snapshot_date": snapshot_date,
        "capital": data.get("capital", 0),
        "equity": equity,
        "n_positions": len(data.get("positions", {})),
        "n_closed": len(data.get("closed_trades", [])),
        "positions": positions,
        "trades": trades,
    }


def extract_signals_from_html(html_path: Path) -> dict:
    """
    Extract signal data from stock_report.html.
    The HTML contains an embedded JSON block with signal_items.
    Falls back to daily_signals from paper_equity.json if no HTML.
    """
    if not html_path.exists():
        log.warning("stock_report.html not found at %s", html_path)
        return {}

    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Try to extract embedded JSON from script tags (common pattern in ai_report.py output)
    # Look for: <script>const SIGNAL_DATA = {...}</script> or similar
    json_patterns = [
        r"const\s+SIGNAL_DATA\s*=\s*({.*?});",
        r"var\s+signalData\s*=\s*({.*?});",
        r'"signal_items"\s*:\s*(\[.*?\])',
        r'"signals"\s*:\s*(\[.*?\])',
    ]

    for pattern in json_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                log.info("Extracted signal data from HTML via pattern: %s", pattern[:30])
                if isinstance(parsed, list):
                    return {"signal_items": parsed, "signals": []}
                return parsed
            except json.JSONDecodeError:
                continue

    # Fallback: extract the title/date from HTML
    title_match = re.search(r"<title>(.*?)</title>", content)
    signal_date = "unknown"
    if title_match:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", title_match.group(1))
        if date_match:
            signal_date = date_match.group(1)

    log.info("Could not extract structured signals from HTML; using date=%s", signal_date)
    return {"signal_date": signal_date, "signals": [], "signal_items": []}


def build_report_payload(
    paper_data: dict, html_signals: Optional[dict] = None
) -> dict:
    """
    Transform data into ReportSyncPayload:
    {
        signal_date: str,
        items: [...]
    }
    """
    # Prefer daily_signals from paper_equity.json
    daily_signals = paper_data.get("daily_signals", [])
    if daily_signals:
        # Use the most recent signal entry
        latest = daily_signals[-1]
        signal_date = latest.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        signals = daily_signals  # include all
    else:
        signal_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        signals = []

    # Merge HTML signal_items if available
    signal_items = []
    if html_signals and html_signals.get("signal_items"):
        signal_items = html_signals["signal_items"]
    elif html_signals and html_signals.get("signal_date"):
        signal_date = html_signals["signal_date"]

    # Try to extract signal_items from paper_equity.json if not in HTML
    # The paper_equity.json sometimes has detailed signal items at the end
    if not signal_items and paper_data.get("signal_items"):
        signal_items = paper_data["signal_items"]

    return {
        "signal_date": signal_date,
        "items": signal_items,
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def post_with_retry(url: str, payload: dict, dry_run: bool = False) -> Optional[dict]:
    """POST JSON payload with retry logic. Returns response JSON or None."""
    import requests  # noqa: E401

    token_id = os.environ.get("CF_ACCESS_SERVICE_TOKEN_ID", "")
    token_secret = os.environ.get("CF_ACCESS_SERVICE_TOKEN_SECRET", "")
    headers = {"Content-Type": "application/json"}
    if token_id and token_secret:
        headers["CF-Access-Client-Id"] = token_id
        headers["CF-Access-Client-Secret"] = token_secret

    if dry_run:
        log.info("[DRY-RUN] Would POST to %s", url)
        log.info("[DRY-RUN] Payload size: %d bytes", len(json.dumps(payload, ensure_ascii=False)))
        log.info("[DRY-RUN] Payload preview: %s", json.dumps(payload, ensure_ascii=False)[:500])
        return {"dry_run": True, "status": "simulated"}

    if not token_id or not token_secret:
        log.error(
            "CF_ACCESS_SERVICE_TOKEN_ID or CF_ACCESS_SERVICE_TOKEN_SECRET not set; "
            "skipping POST to %s",
            url,
        )
        return None

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        resp = None
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            log.info("POST %s → %d (attempt %d/%d)", url, resp.status_code, attempt, MAX_RETRIES)
            return resp.json() if resp.content else {"status": "ok"}
        except requests.exceptions.HTTPError as e:
            last_error = e
            status = resp.status_code if resp is not None else 0
            # R1: Don't retry non-retryable client errors (4xx except 429)
            if status in (400, 401, 403, 404, 413, 422):
                log.error("Non-retryable HTTP %s; aborting: %s", status, e)
                return None
            log.warning(
                "HTTP %s on attempt %d/%d: %s",
                status,
                attempt,
                MAX_RETRIES,
                e,
            )
        except requests.exceptions.RequestException as e:
            last_error = e
            log.warning("Request error on attempt %d/%d: %s", attempt, MAX_RETRIES, e)

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAY * (2 ** (attempt - 1))
            log.info("Retrying in %ds…", delay)
            time.sleep(delay)

    log.error("All %d attempts failed for %s: %s", MAX_RETRIES, url, last_error)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Sync paper trading data to tw_stocker-web Worker",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse data and show what would be sent (no HTTP calls)",
    )
    parser.add_argument(
        "--paper-only",
        action="store_true",
        help="Sync paper snapshot only (skip report)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Sync report only (skip paper snapshot)",
    )
    parser.add_argument(
        "--paper-file",
        type=Path,
        default=PAPER_EQ_FILE,
        help="Path to paper_equity.json",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=REPORT_HTML_FILE,
        help="Path to stock_report.html",
    )
    args = parser.parse_args()

    if args.paper_only and args.report_only:
        log.error("--paper-only and --report-only cannot both be set")
        sys.exit(1)

    log.info("=== tw_stocker sync_to_web ===")
    if args.dry_run:
        log.info("Mode: DRY-RUN (no HTTP requests will be made)")

    # 1) Load paper equity data
    paper_data = load_paper_equity(args.paper_file)

    # 2) Load report HTML signals
    html_signals = extract_signals_from_html(args.report_file)

    # 3) Build payloads
    paper_payload = build_paper_payload(paper_data)
    report_payload = build_report_payload(paper_data, html_signals)

    # 4) Sync paper snapshot
    paper_sync_ok = False
    if not args.report_only:
        log.info("--- Syncing paper snapshot ---")
        result = post_with_retry(PAPER_ENDPOINT, paper_payload, dry_run=args.dry_run)
        if result:
            paper_sync_ok = True
            log.info("Paper sync result: %s", json.dumps(result, ensure_ascii=False)[:200])

    # 5) Sync report
    report_sync_ok = False
    if not args.paper_only:
        log.info("--- Syncing daily report ---")
        result = post_with_retry(REPORT_ENDPOINT, report_payload, dry_run=args.dry_run)
        if result:
            report_sync_ok = True
            log.info("Report sync result: %s", json.dumps(result, ensure_ascii=False)[:200])

    # R2: Exit with non-zero code if all attempted syncs failed
    any_attempted = (not args.report_only) or (not args.paper_only)
    all_ok = paper_sync_ok or report_sync_ok
    if any_attempted and not all_ok:
        log.error("=== Sync FAILED (all syncs returned None) ===")
        sys.exit(1)

    log.info("=== Sync complete ===")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
backtest_worker.py — VPS side backtest execution worker for tw_stocker-web.

Pulls queued backtest jobs from Cloudflare Worker, executes param_sweep.py with
mapped arguments, and backfills the results (metrics, equity curve summary, error)
via POST /api/vps/sync/backtest.

Concurrency Safety:
    Uses VPS-side file locking (fcntl.flock) to ensure only one worker runs at a time.
    If another instance is active, it exits immediately.

CLI Mapping:
    param_sweep.py CLI arguments:
      --tp-grid, --sl-grid, --days, --start-date, --end-date, --top-k, --output
    Transmitted from web UI:
      tp_atr / tpAtr -> --tp-grid
      sl_atr / slAtr -> --sl-grid
      days           -> --days
      top_k / topK   -> --top-k
      start_date     -> --start-date
      end_date       -> --end-date
    Untransmitted parameters (unsupported by param_sweep.py CLI):
      capital, hold_days, strategy, pool, tp_sl_mode, gap_filter, slippage, regime_filter
      (truthfully recorded in job summary and logs without hacky forcing)

Usage:
    python3 backtest_worker.py             # run one cycle, process 1 job if queued
    python3 backtest_worker.py --dry-run   # poll and log, don't execute
    python3 backtest_worker.py --job-id ID # process specific job
"""

import argparse
import fcntl
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Setup Paths & Environment
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PARAM_SWEEP_PATH = BASE_DIR / "param_sweep.py"
DEFAULT_LOCK_PATH = Path("/tmp/tw_stocker_backtest_worker.lock")
DEFAULT_ARTIFACTS_DIR = BASE_DIR / "artifacts"

# Load local .env and /root/.tw-stocker-web-sync.env if present
for env_candidate in [BASE_DIR / ".env", Path("/root/.tw-stocker-web-sync.env")]:
    if env_candidate.exists():
        for line in env_candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v

DEFAULT_BASE_URL = os.environ.get(
    "TW_STOCKER_WEB_URL",
    os.environ.get("WORKER_URL", "https://tw-stocker-web.yucheung.workers.dev"),
).rstrip("/")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backtest_worker")


# ---------------------------------------------------------------------------
# Concurrency Safety (VPS-side flock)
# ---------------------------------------------------------------------------
def acquire_flock(lock_path: Path) -> Optional[Any]:
    """Acquire a non-blocking exclusive file lock. Returns file object or None."""
    f = None
    try:
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (BlockingIOError, OSError):
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        return None


def release_flock(lock_file: Any) -> None:
    """Release flock and close file."""
    if lock_file is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception as e:
            log.warning("Failed to release flock: %s", e)


# ---------------------------------------------------------------------------
# HTTP Helpers & Headers (copied from sync_strategy_to_web.py pattern)
# ---------------------------------------------------------------------------
def get_headers() -> Dict[str, str]:
    """Build request headers with browser UA and service tokens."""
    token_id = (
        os.environ.get("CF_ACCESS_SERVICE_TOKEN_ID")
        or os.environ.get("CF_ACCESS_CLIENT_ID")
        or ""
    )
    token_secret = (
        os.environ.get("CF_ACCESS_SERVICE_TOKEN_SECRET")
        or os.environ.get("CF_ACCESS_CLIENT_SECRET")
        or ""
    )
    sync_token = os.environ.get("SYNC_PUSH_TOKEN") or ""

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }
    if token_id and token_secret:
        headers["CF-Access-Client-Id"] = token_id
        headers["CF-Access-Client-Secret"] = token_secret
    if sync_token:
        headers["X-Twstocker-Sync-Token"] = sync_token

    return headers


# ---------------------------------------------------------------------------
# Parameter Mapping to param_sweep.py CLI
# ---------------------------------------------------------------------------
def map_params_to_cli(params: Dict[str, Any], output_csv: str) -> Tuple[List[str], List[str]]:
    """
    Map web backtest parameters to param_sweep.py CLI arguments.

    Returns:
        (cli_cmd, untransmitted_parameters_list)
    """
    cmd = [
        sys.executable,
        str(PARAM_SWEEP_PATH),
        "--output",
        output_csv,
    ]

    # 1. tp_atr / sl_atr
    tp_atr = params.get("tp_atr")
    if tp_atr is None:
        tp_atr = params.get("tpAtr", 4.0)
    sl_atr = params.get("sl_atr")
    if sl_atr is None:
        sl_atr = params.get("slAtr", 3.0)
    cmd.extend(["--tp-grid", str(tp_atr), "--sl-grid", str(sl_atr)])

    # 2. days
    if "days" in params and params["days"]:
        cmd.extend(["--days", str(int(params["days"]))])

    # 3. top_k / topK
    top_k = params.get("top_k")
    if top_k is None:
        top_k = params.get("topK", 7)
    cmd.extend(["--top-k", str(int(top_k))])

    # 4. start_date / end_date
    if params.get("start_date"):
        cmd.extend(["--start-date", str(params["start_date"])])
    if params.get("end_date"):
        cmd.extend(["--end-date", str(params["end_date"])])

    # Optional: skip_data_gate for short runs or explicit request
    if params.get("skip_data_gate") or (params.get("days") and int(params["days"]) < 100):
        cmd.append("--skip-data-gate")

    # 5. Untransmitted parameters (unsupported by param_sweep.py CLI)
    untransmitted = []

    hold_days = params.get("hold_days")
    if hold_days is None:
        hold_days = params.get("holdDays")

    tp_sl_mode = params.get("tp_sl_mode")
    if tp_sl_mode is None:
        tp_sl_mode = params.get("tpSlMode")

    gap_filter = params.get("gap_filter")
    if gap_filter is None:
        gap_filter = params.get("gapFilter")

    regime_filter = params.get("regime_filter")
    if regime_filter is None:
        regime_filter = params.get("regimeFilter")

    advanced_json = params.get("advanced_json")
    if advanced_json is None:
        advanced_json = params.get("advancedJson")

    notify = params.get("notify")
    strategy = params.get("strategy")

    unsupported_keys = [
        ("capital", params.get("capital")),
        ("hold_days", hold_days),
        ("strategy", strategy),
        ("pool", params.get("pool")),
        ("tp_sl_mode", tp_sl_mode),
        ("gap_filter", gap_filter),
        ("slippage", params.get("slippage")),
        ("regime_filter", regime_filter),
        ("advanced_json", advanced_json),
        ("notify", notify),
    ]
    for key, val in unsupported_keys:
        if val is not None:
            untransmitted.append(f"{key}={val}")

    return cmd, untransmitted


def sanitize_error(error: Optional[str]) -> Optional[str]:
    """
    Sanitize error message before backfilling (P2-2):
    1. Mask known sensitive environment variable values.
    2. Mask token-like patterns (JWT, Bearer, token=..., 32+ hex).
    3. Mask VPS absolute paths to keep only filename.
    """
    if not error:
        return error
    msg = str(error)

    # 1. Mask known sensitive env values
    sensitive_env_keys = [
        "SYNC_PUSH_TOKEN",
        "CF_ACCESS_SERVICE_TOKEN_SECRET",
        "CF_ACCESS_CLIENT_SECRET",
        "CF_ACCESS_SERVICE_TOKEN_ID",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_AUD",
    ]
    for key in sensitive_env_keys:
        val = os.environ.get(key)
        if val and len(val) >= 4:
            msg = msg.replace(val, "***MASKED***")

    # 2. Token-like pattern masking
    # JWT tokens
    msg = re.sub(r'eyJ[a-zA-Z0-9_-]{4,}\.[a-zA-Z0-9_-]{2,}(?:\.[a-zA-Z0-9_-]+)?', '***MASKED_TOKEN***', msg)
    # JSON quoted fields & key-value tokens (e.g. {"api_key":"sk_live_..."}, token=..., secret: ...)
    msg = re.sub(
        r'(?i)(["\']?(?:[a-zA-Z0-9_-]*(?:token|secret|password|api_?key|auth|credential)[a-zA-Z0-9_-]*|key)["\']?\s*[:=]\s*["\']?(?:bearer\s+)?)([^"\'\s,}{]{4,})(["\']?)',
        r'\g<1>***MASKED***\g<3>',
        msg,
    )
    # Bare bearer token
    msg = re.sub(r'(?i)(bearer\s+)(["\']?)([^"\'\s,}{]{4,})\2', r'\g<1>\g<2>***MASKED***\g<2>', msg)
    # 32+ char hex tokens
    msg = re.sub(r'\b[a-fA-F0-9]{32,}\b', '***MASKED_HEX***', msg)

    # 3. VPS absolute path masking: keep only filename (supports paths with spaces)
    msg = re.sub(
        r'/(?:[^\s"\'`:\r\n]+(?:[ \t]+[^\s"\'`:\r\n]+)*\/)+(?:[^"\'`:\r\n]+?(?=["\'`])|[^\s"\'`:\r\n]+)',
        lambda m: os.path.basename(m.group(0)),
        msg,
    )

    return msg


# ---------------------------------------------------------------------------
# Worker Task Execution
# ---------------------------------------------------------------------------
def run_worker_once(
    base_url: str = DEFAULT_BASE_URL,
    lock_path: Path = DEFAULT_LOCK_PATH,
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR,
    target_job_id: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """
    Execute one cycle of the worker:
    1. Acquire VPS flock.
    2. Poll for queued jobs (or target_job_id).
    3. Claim job (POST status=running).
    4. Execute param_sweep.py CLI.
    5. Backfill result (POST status=done/failed).
    6. Release flock.

    Returns True if a job was processed, False otherwise.
    """
    lock_file = acquire_flock(lock_path)
    if lock_file is None:
        log.info("Another backtest_worker process is running (flock locked). Exiting.")
        return False

    try:
        headers = get_headers()

        # Step 2: Fetch queued jobs
        runs_url = f"{base_url}/api/backtest/runs"
        log.info("Polling for queued backtest runs at %s...", runs_url)

        try:
            resp = requests.get(
                runs_url,
                params={"status": "queued", "limit": 10},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            runs = resp.json().get("runs", [])
        except Exception as e:
            log.error("Failed to query backtest runs from %s: %s", runs_url, e)
            return False

        if target_job_id:
            runs = [r for r in runs if r.get("job_id") == target_job_id]

        if not runs:
            log.info("No queued backtest runs found. Exiting.")
            return False

        # Pick highest priority, oldest job
        job = runs[0]
        job_id = job.get("job_id")
        log.info("Claimed job %s (priority=%s, strategy=%s)", job_id, job.get("priority"), job.get("strategy"))

        if dry_run:
            log.info("[DRY-RUN] Would process job %s: params_json=%s", job_id, job.get("params_json"))
            return True

        sync_url = f"{base_url}/api/vps/sync/backtest"

        # Step 3: Mark job running in Worker
        try:
            claim_resp = requests.post(
                sync_url,
                json={"job_id": job_id, "status": "running", "progress_pct": 0.05},
                headers=headers,
                timeout=15,
            )
            if claim_resp.status_code == 409:
                log.info("Job %s was already claimed by another worker (conflict 409). Giving up and exiting.", job_id)
                return False
            claim_resp.raise_for_status()
            claim_data = claim_resp.json()
            if not claim_data.get("ok", True):
                log.info("Job %s claim rejected (%s). Giving up and exiting.", job_id, claim_data.get("error"))
                return False
            log.info("Marked job %s as running", job_id)
        except Exception as e:
            log.error("Failed to mark job %s as running: %s", job_id, e)
            return False

        # Step 4: Parse params and build CLI command
        raw_params = job.get("params_json") or "{}"
        try:
            params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
        except Exception as e:
            params = {}
            log.warning("Could not parse params_json for job %s: %s", job_id, e)

        # Ensure strategy from top-level job is in params if missing (P2-1)
        if "strategy" not in params and job.get("strategy"):
            params["strategy"] = job.get("strategy")

        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_csv = artifacts_dir / f"backtest_{job_id}.csv"

        cmd, untransmitted = map_params_to_cli(params, str(output_csv))
        if untransmitted:
            log.info(
                "Job %s: Untransmitted parameters (unsupported by param_sweep.py CLI): %s",
                job_id,
                ", ".join(untransmitted),
            )

        log.info("Executing CLI command for %s: %s", job_id, " ".join(cmd))

        # Step 5: Execute param_sweep.py
        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=1800,  # 30 min max
            )
            elapsed = time.time() - start_time
        except subprocess.TimeoutExpired:
            log.error("Job %s timed out after 1800s", job_id)
            requests.post(
                sync_url,
                json={
                    "job_id": job_id,
                    "status": "failed",
                    "error": sanitize_error("Execution timed out (1800s limit)"),
                    "progress_pct": 0,
                },
                headers=headers,
                timeout=15,
            )
            return True
        except Exception as e:
            log.error("Subprocess execution error for %s: %s", job_id, e)
            requests.post(
                sync_url,
                json={
                    "job_id": job_id,
                    "status": "failed",
                    "error": sanitize_error(f"Subprocess execution error: {str(e)}"),
                    "progress_pct": 0,
                },
                headers=headers,
                timeout=15,
            )
            return True

        # Step 6: Handle subprocess completion
        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or f"Process exited with code {proc.returncode}").strip()
            sanitized_err = sanitize_error(err_msg) or ""
            log.error("Job %s failed with code %d:\n%s", job_id, proc.returncode, sanitized_err[-500:])
            try:
                requests.post(
                    sync_url,
                    json={
                        "job_id": job_id,
                        "status": "failed",
                        "error": sanitized_err[-1500:],
                        "progress_pct": 0,
                    },
                    headers=headers,
                    timeout=15,
                )
            except Exception as e:
                log.error("Failed to backfill failure status for %s: %s", job_id, e)
            return True

        # Subprocess succeeded: read CSV
        if not output_csv.exists() or output_csv.stat().st_size == 0:
            log.error("Job %s output CSV %s not found or empty", job_id, output_csv)
            requests.post(
                sync_url,
                json={
                    "job_id": job_id,
                    "status": "failed",
                    "error": sanitize_error(f"Output CSV {output_csv.name} not found or empty"),
                    "progress_pct": 0,
                },
                headers=headers,
                timeout=15,
            )
            return True

        try:
            df = pd.read_csv(output_csv)
            if df.empty:
                raise ValueError("output CSV contains no rows")
            row = df.iloc[0]

            def _clean_float(val: Any, ndigits: int = 4, default: Optional[float] = None) -> Optional[float]:
                try:
                    f = float(val)
                    if math.isnan(f) or math.isinf(f):
                        return default
                    return round(f, ndigits)
                except Exception:
                    return default

            summary = {
                "total_return": _clean_float(float(row["total_return_pct"]) / 100.0, 4, 0.0),
                "annual_return": _clean_float(float(row["ann_return_pct"]) / 100.0, 4, 0.0),
                "cagr": _clean_float(float(row["ann_return_pct"]) / 100.0, 4, 0.0),
                "sharpe": _clean_float(row["sharpe"], 3, 0.0),
                "sharpe_ratio": _clean_float(row["sharpe"], 3, 0.0),
                "mdd": _clean_float(abs(float(row["mdd_pct"])) / 100.0, 4, 0.0),
                "max_drawdown": _clean_float(abs(float(row["mdd_pct"])) / 100.0, 4, 0.0),
                "win_rate": _clean_float(float(row["win_rate_pct"]) / 100.0, 3, 0.0),
                "profit_factor": _clean_float(row["profit_factor"], 2, None),
                "total_trades": int(row["total_trades"]) if pd.notna(row["total_trades"]) else 0,
                "sortino": _clean_float(row.get("sortino"), 3, 0.0),
                "calmar": _clean_float(row.get("calmar"), 3, 0.0),
                "tp_atr": _clean_float(row["tp_mult"], 2, 4.0),
                "sl_atr": _clean_float(row["sl_mult"], 2, 3.0),
                "elapsed_sec": round(elapsed, 1),
                "unmapped_params": untransmitted,
            }
        except Exception as e:
            sanitized_err = sanitize_error(f"Failed to parse output CSV metrics: {str(e)}") or ""
            log.error("Failed to parse CSV for job %s: %s", job_id, sanitize_error(str(e)))
            requests.post(
                sync_url,
                json={
                    "job_id": job_id,
                    "status": "failed",
                    "error": sanitized_err,
                    "progress_pct": 0,
                },
                headers=headers,
                timeout=15,
            )
            return True

        # Backfill done status
        backfill_payload = {
            "job_id": job_id,
            "status": "done",
            "progress_pct": 1.0,
            "summary_json": summary,
            "equity_json": [],
            "artifacts_path": output_csv.name,
            "error": None,
        }

        try:
            done_resp = requests.post(
                sync_url,
                json=backfill_payload,
                headers=headers,
                timeout=15,
            )
            done_resp.raise_for_status()
            log.info("Job %s backfilled successfully (done in %.1fs, Sharpe=%.2f, Return=%.1f%%)",
                     job_id, elapsed, summary["sharpe"], summary["total_return"] * 100)
        except Exception as e:
            log.error("Failed to backfill done status for %s: %s", job_id, e)

        return True

    finally:
        release_flock(lock_file)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="tw_stocker backtest worker — polls and executes queued jobs"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Worker URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--job-id",
        default=None,
        help="Specify single job_id to claim and run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll queued jobs and preview without running",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run once and exit (default)",
    )
    args = parser.parse_args()

    processed = run_worker_once(
        base_url=args.base_url,
        target_job_id=args.job_id,
        dry_run=args.dry_run,
    )
    sys.exit(0 if processed or not args.job_id else 1)


if __name__ == "__main__":
    main()

"""
Tests for backtest_worker.py:
- Service token headers & endpoint interaction
- VPS-side flock concurrency safety
- Parameter mapping & untransmitted parameters listing
- Backfill of done status with summary metrics
- Backfill of failed status with error message
- Empty queue no-op
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure tw_stocker root is in path
sys.path.insert(0, str(Path(__file__).parent))
import backtest_worker


class TestBacktestWorker(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_flock_concurrency(self):
        """Test VPS flock ensures only one instance can run."""
        lock_file = self.tmp_path / "test.lock"
        f1 = backtest_worker.acquire_flock(lock_file)
        self.assertIsNotNone(f1)

        # Attempting second lock should return None
        f2 = backtest_worker.acquire_flock(lock_file)
        self.assertIsNone(f2)

        # Releasing first lock allows acquiring again
        backtest_worker.release_flock(f1)
        f3 = backtest_worker.acquire_flock(lock_file)
        self.assertIsNotNone(f3)
        backtest_worker.release_flock(f3)

    def test_get_headers(self):
        """Test headers contain browser UA and service tokens."""
        with patch.dict(os.environ, {
            "SYNC_PUSH_TOKEN": "my-push-token",
            "CF_ACCESS_SERVICE_TOKEN_ID": "cid-123",
            "CF_ACCESS_SERVICE_TOKEN_SECRET": "csec-456",
            "CF_ACCESS_CLIENT_ID": "cid-123",
            "CF_ACCESS_CLIENT_SECRET": "csec-456",
        }):
            headers = backtest_worker.get_headers()
            self.assertEqual(headers.get("X-Twstocker-Sync-Token"), "my-push-token")
            self.assertEqual(headers.get("CF-Access-Client-Id"), "cid-123")
            self.assertEqual(headers.get("CF-Access-Client-Secret"), "csec-456")
            self.assertIn("Mozilla/5.0", headers.get("User-Agent", ""))

    def test_param_mapping_and_untransmitted(self):
        """Test params correctly map to param_sweep.py CLI and untransmitted are listed."""
        params = {
            "strategy": "momentum_v85",
            "pool": "full",
            "days": 60,
            "top_k": 5,
            "capital": 100000,
            "tp_atr": 2.5,
            "sl_atr": 3.5,
            "hold_days": 10,
            "start_date": "2026-01-01",
            "end_date": "2026-03-01",
        }
        output_csv = str(self.tmp_path / "out.csv")
        cmd, untransmitted = backtest_worker.map_params_to_cli(params, output_csv)

        # Verify CLI args
        self.assertIn("--tp-grid", cmd)
        idx_tp = cmd.index("--tp-grid")
        self.assertEqual(cmd[idx_tp + 1], "2.5")

        self.assertIn("--sl-grid", cmd)
        idx_sl = cmd.index("--sl-grid")
        self.assertEqual(cmd[idx_sl + 1], "3.5")

        self.assertIn("--days", cmd)
        idx_days = cmd.index("--days")
        self.assertEqual(cmd[idx_days + 1], "60")

        self.assertIn("--top-k", cmd)
        idx_k = cmd.index("--top-k")
        self.assertEqual(cmd[idx_k + 1], "5")

        self.assertIn("--start-date", cmd)
        self.assertIn("--end-date", cmd)
        self.assertIn("--output", cmd)
        self.assertIn("--skip-data-gate", cmd)

        # Verify untransmitted parameters are truthfully listed
        untransmitted_str = " ".join(untransmitted)
        self.assertIn("capital=100000", untransmitted_str)
        self.assertIn("hold_days=10", untransmitted_str)
        self.assertIn("strategy=momentum_v85", untransmitted_str)
        self.assertIn("pool=full", untransmitted_str)

    @patch("backtest_worker.requests.get")
    @patch("backtest_worker.requests.post")
    @patch("backtest_worker.subprocess.run")
    def test_process_one_job_success(self, mock_run, mock_post, mock_get):
        """Test full successful cycle: poll -> running -> param_sweep -> done."""
        job_id = "job_test_123"
        job_data = {
            "job_id": job_id,
            "status": "queued",
            "priority": 1,
            "params_json": json.dumps({
                "days": 60,
                "top_k": 7,
                "tp_atr": 4.0,
                "sl_atr": 3.0,
                "capital": 200000,
            }),
        }

        # Mock GET queued runs
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"runs": [job_data]}
        mock_get.return_value = mock_get_resp

        # Mock POST sync
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_post_resp

        # Mock subprocess creating output CSV
        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("--output")
            out_path = Path(cmd[out_idx + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                "tp_mult,sl_mult,total_return_pct,ann_return_pct,sharpe,sortino,calmar,mdd_pct,win_rate_pct,profit_factor,total_trades\n"
                "4.0,3.0,15.2,22.4,1.35,1.8,1.2,-10.5,58.5,1.6,42\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="Success", stderr="")

        mock_run.side_effect = fake_run

        processed = backtest_worker.run_worker_once(
            base_url="http://mock-worker",
            lock_path=self.tmp_path / "worker.lock",
            artifacts_dir=self.tmp_path / "artifacts",
        )

        self.assertTrue(processed)
        # Should have called POST twice: 1) running, 2) done
        self.assertEqual(mock_post.call_count, 2)

        # Check call 1: running
        call1_args, call1_kwargs = mock_post.call_args_list[0]
        self.assertEqual(call1_kwargs["json"]["status"], "running")
        self.assertEqual(call1_kwargs["json"]["job_id"], job_id)

        # Check call 2: done
        call2_args, call2_kwargs = mock_post.call_args_list[1]
        payload = call2_kwargs["json"]
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["progress_pct"], 1.0)
        self.assertAlmostEqual(payload["summary_json"]["total_return"], 0.152)
        self.assertAlmostEqual(payload["summary_json"]["sharpe"], 1.35)
        self.assertAlmostEqual(payload["summary_json"]["mdd"], 0.105)
        self.assertAlmostEqual(payload["summary_json"]["win_rate"], 0.585)
        self.assertEqual(payload["summary_json"]["total_trades"], 42)

    @patch("backtest_worker.requests.get")
    @patch("backtest_worker.requests.post")
    @patch("backtest_worker.subprocess.run")
    def test_process_one_job_failure(self, mock_run, mock_post, mock_get):
        """Test failure cycle: poll -> running -> param_sweep error -> failed."""
        job_id = "job_fail_123"
        job_data = {
            "job_id": job_id,
            "status": "queued",
            "priority": 1,
            "params_json": json.dumps({"days": 60}),
        }

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"runs": [job_data]}
        mock_get.return_value = mock_get_resp

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_post_resp

        # Mock subprocess failing
        mock_run.return_value = subprocess.CompletedProcess(
            ["python3"], 1, stdout="", stderr="RuntimeError: yfinance rate limit"
        )

        processed = backtest_worker.run_worker_once(
            base_url="http://mock-worker",
            lock_path=self.tmp_path / "worker.lock",
            artifacts_dir=self.tmp_path / "artifacts",
        )

        self.assertTrue(processed)
        self.assertEqual(mock_post.call_count, 2)

        # Check call 2: failed
        call2_args, call2_kwargs = mock_post.call_args_list[1]
        payload = call2_kwargs["json"]
        self.assertEqual(payload["status"], "failed")
        self.assertIn("yfinance rate limit", payload["error"])

    @patch("backtest_worker.requests.get")
    def test_empty_queue_exits_cleanly(self, mock_get):
        """Test empty queue returns False and does nothing."""
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"runs": []}
        mock_get.return_value = mock_get_resp

        processed = backtest_worker.run_worker_once(
            base_url="http://mock-worker",
            lock_path=self.tmp_path / "worker.lock",
            artifacts_dir=self.tmp_path / "artifacts",
        )
        self.assertFalse(processed)


if __name__ == "__main__":
    unittest.main()

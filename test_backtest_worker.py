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
        """Test params correctly map to param_sweep.py CLI and untransmitted are listed (including 0/False, strategy, advanced_json, notify)."""
        params = {
            "strategy": "momentum_v85",
            "pool": "full",
            "days": 60,
            "top_k": 5,
            "capital": 100000,
            "tp_atr": 2.5,
            "sl_atr": 3.5,
            "hold_days": 10,
            "gap_filter": False,
            "regime_filter": 0,
            "advanced_json": {"grid_opt": True},
            "notify": False,
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

        # Verify untransmitted parameters are truthfully listed (P2-1)
        untransmitted_str = " ".join(untransmitted)
        self.assertIn("capital=100000", untransmitted_str)
        self.assertIn("hold_days=10", untransmitted_str)
        self.assertIn("strategy=momentum_v85", untransmitted_str)
        self.assertIn("pool=full", untransmitted_str)
        self.assertIn("gap_filter=False", untransmitted_str, "gap_filter=False (boolean) 必須被記錄")
        self.assertIn("regime_filter=0", untransmitted_str, "regime_filter=0 (numeric 0) 必須被記錄")
        self.assertIn("advanced_json=", untransmitted_str)
        self.assertIn("notify=False", untransmitted_str, "notify=False 必須被記錄")

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
        self.assertNotIn("/", payload["artifacts_path"], "artifacts_path 不得包含絕對路徑斜線")
        self.assertTrue(payload["artifacts_path"].endswith(".csv"), "artifacts_path 應為 CSV 檔名")
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

        # Mock subprocess failing with VPS absolute path and secret tokens
        mock_run.return_value = subprocess.CompletedProcess(
            ["python3"],
            1,
            stdout="",
            stderr=(
                "Traceback (most recent call last):\n"
                "  File \"/root/work/tw_stocker/strategy/custom_backtest.py\", line 42, in run\n"
                "RuntimeError: invalid token=ghp_fake1234567890abcdef1234567890abcdef and /var/log/secret.log"
            ),
        )

        processed = backtest_worker.run_worker_once(
            base_url="http://mock-worker",
            lock_path=self.tmp_path / "worker.lock",
            artifacts_dir=self.tmp_path / "artifacts",
        )

        self.assertTrue(processed)
        self.assertEqual(mock_post.call_count, 2)

        # Check call 2: failed + error sanitized (P2-2)
        call2_args, call2_kwargs = mock_post.call_args_list[1]
        payload = call2_kwargs["json"]
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("/root/work/tw_stocker/strategy/custom_backtest.py", payload["error"], "絕對路徑不得出現在原文")
        self.assertIn("custom_backtest.py", payload["error"], "檔名應保留")
        self.assertNotIn("/var/log/secret.log", payload["error"])
        self.assertIn("secret.log", payload["error"])
        self.assertNotIn("ghp_fake1234567890abcdef1234567890abcdef", payload["error"], "假 token 樣式字串不得出現在原文")

    def test_sanitize_error_direct(self):
        """Test sanitize_error directly: masks VPS paths, tokens, and sensitive envs (P2-2)."""
        with patch.dict(os.environ, {"SYNC_PUSH_TOKEN": "super_secret_sync_key"}):
            raw = (
                "Error at /root/work/tw_stocker/backtest_worker.py: push token=super_secret_sync_key, "
                "auth key=my_api_key_12345678, JWT eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M, "
                "hash=0123456789abcdef0123456789abcdef, log in /home/vps/error.txt"
            )
            sanitized = backtest_worker.sanitize_error(raw)
            self.assertNotIn("/root/work/tw_stocker/backtest_worker.py", sanitized)
            self.assertIn("backtest_worker.py", sanitized)
            self.assertNotIn("/home/vps/error.txt", sanitized)
            self.assertIn("error.txt", sanitized)
            self.assertNotIn("super_secret_sync_key", sanitized)
            self.assertNotIn("my_api_key_12345678", sanitized)
            self.assertNotIn("0123456789abcdef0123456789abcdef", sanitized)
            self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30", sanitized)

        # JSON 引號欄位測試（例如 {"api_key":"sk_live_..."}）
        json_raw = 'Request failed: {"api_key":"sk_live_1234567890abcdef", "client_secret":"sec_val_99887766"}'
        json_sanitized = backtest_worker.sanitize_error(json_raw)
        self.assertNotIn("sk_live_1234567890abcdef", json_sanitized)
        self.assertNotIn("sec_val_99887766", json_sanitized)
        self.assertIn('{"api_key":"***MASKED***"', json_sanitized)

        # 含空格路徑只剩檔名測試
        spaced_raw = "Failed at /root/my project/run/log.txt"
        spaced_sanitized = backtest_worker.sanitize_error(spaced_raw)
        self.assertNotIn("/root/my project", spaced_sanitized)
        self.assertEqual(spaced_sanitized, "Failed at log.txt")

        spaced_alone = backtest_worker.sanitize_error("/root/my project/run/log.txt")
        self.assertEqual(spaced_alone, "log.txt")

    @patch("backtest_worker.requests.get")
    @patch("backtest_worker.requests.post")
    def test_claim_conflict_409_gives_up_and_exits(self, mock_post, mock_get):
        """Test atomic claim conflict (409): worker gives up and exits cleanly (P1-2)."""
        job_id = "job_conflict_123"
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

        # Mock claim returning 409 Conflict (another worker took it)
        mock_claim_resp = MagicMock()
        mock_claim_resp.status_code = 409
        mock_claim_resp.json.return_value = {"error": "conflict: job not queued", "ok": False}
        mock_post.return_value = mock_claim_resp

        processed = backtest_worker.run_worker_once(
            base_url="http://mock-worker",
            lock_path=self.tmp_path / "worker.lock",
            artifacts_dir=self.tmp_path / "artifacts",
        )

        # Worker should abandon and exit
        self.assertFalse(processed)
        # Should have called claim once and never called done/failed
        self.assertEqual(mock_post.call_count, 1)

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

    @patch("backtest_worker.pd.read_csv")
    @patch("backtest_worker.requests.get")
    @patch("backtest_worker.requests.post")
    @patch("backtest_worker.subprocess.run")
    def test_csv_parse_exception_sanitizes_token_and_path(self, mock_run, mock_post, mock_get, mock_read_csv):
        """Test CSV parse exception sanitizes tokens and paths before logging and sending to sync API."""
        job_id = "job_csv_fail_456"
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

        def fake_run(cmd, **kwargs):
            out_idx = cmd.index("--output")
            out_path = Path(cmd[out_idx + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("dummy", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="Success", stderr="")

        mock_run.side_effect = fake_run
        mock_read_csv.side_effect = ValueError(
            "ParserError: invalid token=secret_csv_token_9876543210 at /root/my project/corrupt_data.csv: corrupted"
        )

        with self.assertLogs(backtest_worker.log, level="ERROR") as log_cm:
            processed = backtest_worker.run_worker_once(
                base_url="http://mock-worker",
                lock_path=self.tmp_path / "worker.lock",
                artifacts_dir=self.tmp_path / "artifacts",
            )

        self.assertTrue(processed)
        self.assertEqual(mock_post.call_count, 2)

        # Check call 2: failed + error sanitized
        call2_args, call2_kwargs = mock_post.call_args_list[1]
        payload = call2_kwargs["json"]
        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("secret_csv_token_9876543210", payload["error"], "假 token 不得存在於回填 error")
        self.assertNotIn("/root/my project", payload["error"], "絕對路徑不得存在於回填 error")
        self.assertIn("corrupt_data.csv", payload["error"], "檔名應保留")

        # Verify VPS log does not contain raw token or absolute path
        log_output = "\n".join(log_cm.output)
        self.assertNotIn("secret_csv_token_9876543210", log_output, "VPS log 不得含假 token 原文")
        self.assertNotIn("/root/my project", log_output, "VPS log 不得含絕對路徑")
        self.assertIn("corrupt_data.csv", log_output, "VPS log 應保留檔名")


if __name__ == "__main__":
    unittest.main()

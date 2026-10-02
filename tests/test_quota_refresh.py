"""Unit tests for quota warmup, resilient token refresh, and failure backoff."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from antigravity_cli_switcher import manager as m
from antigravity_cli_switcher.manager.quota import (
    _ensure_fresh_access_token,
    _persist_refresh_failure,
    _run_agy_warmup,
)
from antigravity_cli_switcher.manager.state import parse_timestamp, utc_now


class TestQuotaWarmupAndRefresh(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="acs-quota-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = m.build_paths(self.base / "manager")
        m.ensure_layout(self.paths)

    def test_warmup_runs_agy_models_command(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "gemini-3.8-flash-high"
        mock_proc.stderr = ""
        with mock.patch("subprocess.run", return_value=mock_proc) as mock_run:
            _run_agy_warmup(self.base, agy_binary="agy", timeout_seconds=15)
            mock_run.assert_called_once()
            cmd_args: list[str] = mock_run.call_args[0][0]
            self.assertEqual(cmd_args, ["agy", "models"])

    def test_warmup_ignores_non_zero_exit_if_token_is_fresh(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: Individual quota reached. Resets in 8m45s."
        with (
            mock.patch("subprocess.run", return_value=mock_proc),
            mock.patch("antigravity_cli_switcher.manager.quota._token_expiry_due", return_value=False),
        ):
            # Does not raise because token is already fresh on disk
            _run_agy_warmup(self.base, agy_binary="agy", timeout_seconds=15)

    def test_warmup_raises_if_ineligible_error_regardless_of_token(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: Your current account is not eligible for Antigravity."
        with (
            mock.patch("subprocess.run", return_value=mock_proc),
            mock.patch("antigravity_cli_switcher.manager.quota._token_expiry_due", return_value=False),
        ):
            with self.assertRaises(ValueError) as ctx:
                _run_agy_warmup(self.base, agy_binary="agy", timeout_seconds=15)
            self.assertIn("Eligibility check failed", str(ctx.exception))

    def test_ensure_fresh_access_token_recovers_if_token_refreshed_despite_warmup_error(self) -> None:
        expiry_call_count = 0

        def fake_expiry_due(home: Path) -> bool:
            nonlocal expiry_call_count
            expiry_call_count += 1
            # Due initially, fresh after warmup attempt
            return expiry_call_count == 1

        with (
            mock.patch("antigravity_cli_switcher.manager.quota._token_expiry_due", side_effect=fake_expiry_due),
            mock.patch(
                "antigravity_cli_switcher.manager.quota._run_agy_warmup",
                side_effect=ValueError("agy warmup failed: error: Individual quota reached."),
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.quota._extract_access_token",
                return_value="ya29.c.b0_synthetic_token",
            ),
            mock.patch("antigravity_cli_switcher.manager.quota._isolated_keyring_warmup"),
        ):
            token = _ensure_fresh_access_token(self.base)
            self.assertEqual(token, "ya29.c.b0_synthetic_token")

    def test_refresh_failure_exponential_backoff(self) -> None:
        acc_dir = self.paths.accounts_dir / "synthetic_acc"
        acc_dir.mkdir(parents=True, exist_ok=True)
        state = m.load_state(self.paths)
        state["accounts"]["synthetic_acc"] = {
            "refresh_policy_seconds": 1800,
            "refresh_fail_count": 0,
        }
        m.save_state(self.paths, state)

        # First failure: delay is 60s (rather than full 1800s lockout)
        before_1 = utc_now()
        _persist_refresh_failure(self.paths, "synthetic_acc", "first fail")
        meta_1 = m.load_state(self.paths)["accounts"]["synthetic_acc"]
        next_1 = parse_timestamp(meta_1["next_live_check_at"])
        self.assertIsNotNone(next_1)
        assert next_1 is not None
        delta_1 = int((next_1 - before_1).total_seconds())
        self.assertGreaterEqual(delta_1, 55)
        self.assertLessEqual(delta_1, 70)
        self.assertEqual(meta_1["refresh_fail_count"], 1)

        # Second failure: delay is 120s
        before_2 = utc_now()
        _persist_refresh_failure(self.paths, "synthetic_acc", "second fail")
        meta_2 = m.load_state(self.paths)["accounts"]["synthetic_acc"]
        next_2 = parse_timestamp(meta_2["next_live_check_at"])
        self.assertIsNotNone(next_2)
        assert next_2 is not None
        delta_2 = int((next_2 - before_2).total_seconds())
        self.assertGreaterEqual(delta_2, 115)
        self.assertLessEqual(delta_2, 130)
        self.assertEqual(meta_2["refresh_fail_count"], 2)


if __name__ == "__main__":
    unittest.main()

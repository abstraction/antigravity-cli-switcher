"""Unit tests for native quota polling via agy /usage, backend configuration, and fallback."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from antigravity_cli_switcher import manager as m
from antigravity_cli_switcher.manager.cloudcode import _parse_quota_families_from_summary
from antigravity_cli_switcher.manager.native_quota import _fetch_native_quota
from antigravity_cli_switcher.manager.policy import (
    DEFAULT_QUOTA_BACKEND,
    VALID_QUOTA_BACKENDS,
    get_quota_backend,
    set_quota_backend,
)
from antigravity_cli_switcher.manager.quota import refresh_account_usage

SAMPLE_NATIVE_AGY_USAGE = {
    "conversation_id": "",
    "status": "SUCCESS",
    "response": "Gemini Models\tWeekly Limit Remaining\t64%\t2026-10-08T20:01:27Z\n",
    "duration_seconds": 0,
    "num_turns": 0,
    "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    },
    "command": {
        "name": "usage",
        "data": {
            "description": "Quota limits description",
            "groups": [
                {
                    "name": "Gemini Models",
                    "description": "Models within this group: Gemini Flash, Gemini Pro",
                    "buckets": [
                        {
                            "id": "gemini-weekly",
                            "name": "Weekly Limit Remaining",
                            "description": "Weekly refresh",
                            "window": "weekly",
                            "remaining_fraction": 0.6439,
                            "reset_time": "2026-10-08T20:01:27Z",
                        },
                        {
                            "id": "gemini-5h",
                            "name": "Five Hour Limit Remaining",
                            "description": "5h refresh",
                            "window": "5h",
                            "remaining_fraction": 0.785,
                            "reset_time": "2026-10-04T00:02:14Z",
                        },
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "description": "Models within this group: Claude Opus, Claude Sonnet",
                    "buckets": [
                        {
                            "id": "3p-weekly",
                            "name": "Weekly Limit Remaining",
                            "window": "weekly",
                            "remaining_fraction": 1.0,
                            "reset_time": "2026-10-10T19:31:49Z",
                        },
                        {
                            "id": "3p-5h",
                            "name": "Five Hour Limit Remaining",
                            "window": "5h",
                            "remaining_fraction": 0.95,
                            "reset_time": "2026-10-04T00:31:49Z",
                        },
                    ],
                },
            ],
        },
    },
}


class TestNativeQuotaParsing(unittest.TestCase):
    def test_parse_native_quota_summary(self) -> None:
        families, bucket_count = _parse_quota_families_from_summary(SAMPLE_NATIVE_AGY_USAGE)
        self.assertEqual(bucket_count, 4)
        self.assertEqual(families["gemini"]["weekly"]["value"], 64.39)
        self.assertEqual(families["gemini"]["weekly"]["status"], "known")
        self.assertEqual(families["gemini"]["short"]["value"], 78.5)
        self.assertEqual(families["gemini"]["short"]["status"], "known")
        self.assertEqual(families["other"]["weekly"]["value"], 100.0)
        self.assertEqual(families["other"]["short"]["value"], 95.0)

    def test_parse_nested_and_direct_groups(self) -> None:
        cmd_dict = SAMPLE_NATIVE_AGY_USAGE.get("command")
        assert isinstance(cmd_dict, dict)
        data_dict = cmd_dict.get("data")
        assert isinstance(data_dict, dict)
        groups = data_dict.get("groups")
        assert isinstance(groups, list)

        # Direct groups key
        direct_payload = {"groups": groups}
        families_dir, count_dir = _parse_quota_families_from_summary(direct_payload)
        self.assertEqual(count_dir, 4)
        self.assertEqual(families_dir["gemini"]["short"]["value"], 78.5)

        # Under data key
        data_payload = {"data": {"groups": groups}}
        families_data, count_data = _parse_quota_families_from_summary(data_payload)
        self.assertEqual(count_data, 4)
        self.assertEqual(families_data["gemini"]["short"]["value"], 78.5)


class TestNativeQuotaExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="acs-test-native-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = m.build_paths(self.base / "manager")
        m.ensure_layout(self.paths)

    def test_fetch_native_quota_invokes_agy_usage(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(SAMPLE_NATIVE_AGY_USAGE)
        mock_proc.stderr = ""

        with (
            mock.patch("subprocess.run", return_value=mock_proc) as mock_run,
            mock.patch("antigravity_cli_switcher.manager.native_quota._isolated_keyring_warmup"),
        ):
            payload = _fetch_native_quota(self.base, agy_binary="agy", timeout_seconds=15)
            self.assertEqual(payload["status"], "SUCCESS")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd, ["agy", "-p", "/usage", "--output-format", "json"])

    def test_fetch_native_quota_raises_on_timeout(self) -> None:
        with (
            mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=15)),
            mock.patch("antigravity_cli_switcher.manager.native_quota._isolated_keyring_warmup"),
        ):
            with self.assertRaises(ValueError) as ctx:
                _fetch_native_quota(self.base, agy_binary="agy", timeout_seconds=15)
            self.assertIn("timed out", str(ctx.exception))

    def test_fetch_native_quota_raises_ineligible_error(self) -> None:
        mock_proc = mock.Mock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "Your current account is not eligible for Antigravity."

        with (
            mock.patch("subprocess.run", return_value=mock_proc),
            mock.patch("antigravity_cli_switcher.manager.native_quota._isolated_keyring_warmup"),
        ):
            with self.assertRaises(ValueError) as ctx:
                _fetch_native_quota(self.base, agy_binary="agy", timeout_seconds=15)
            self.assertIn("Eligibility check failed", str(ctx.exception))


class TestQuotaBackendPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="acs-test-policy-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = m.build_paths(self.base / "manager")
        m.ensure_layout(self.paths)

    def test_default_quota_backend_is_native(self) -> None:
        state = m.load_state(self.paths)
        self.assertEqual(get_quota_backend(state), "native")
        self.assertEqual(DEFAULT_QUOTA_BACKEND, "native")
        self.assertEqual(VALID_QUOTA_BACKENDS, ("native", "http", "auto"))

    def test_set_quota_backend_persists_in_state(self) -> None:
        set_quota_backend(self.paths, "http")
        state = m.load_state(self.paths)
        self.assertEqual(get_quota_backend(state), "http")
        self.assertEqual(state["quota_backend"], "http")

        set_quota_backend(self.paths, "auto")
        state = m.load_state(self.paths)
        self.assertEqual(get_quota_backend(state), "auto")

    def test_set_invalid_quota_backend_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            set_quota_backend(self.paths, "invalid_backend")
        self.assertIn("Unsupported quota backend", str(ctx.exception))


class TestRefreshAccountUsageBackends(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="acs-test-refresh-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = m.build_paths(self.base / "manager")
        m.ensure_layout(self.paths)

        # Setup synthetic account
        self.account_name = "test_account_a"
        acc_dir = self.paths.accounts_dir / self.account_name
        acc_dir.mkdir(parents=True, exist_ok=True)
        token_payload = {
            "token": {
                "access_token": "ya29.c.b0_synthetic_token",
                "refresh_token": "1//synthetic_refresh_token",
                "expiry": "2099-01-01T00:00:00Z",
            }
        }
        (acc_dir / ".gemini").mkdir(parents=True, exist_ok=True)
        (acc_dir / ".gemini" / "antigravity_oauth_token.json").write_text(json.dumps(token_payload), encoding="utf-8")
        state = m.load_state(self.paths)
        state["accounts"][self.account_name] = {
            "enabled": True,
            "status": "standby",
            "refresh_policy_seconds": 300,
            "health_status": "ready",
        }
        m.save_state(self.paths, state)

    def test_refresh_account_usage_native_default(self) -> None:
        with (
            mock.patch(
                "antigravity_cli_switcher.manager.quota._fetch_native_quota",
                return_value=SAMPLE_NATIVE_AGY_USAGE,
            ) as mock_fetch,
            mock.patch(
                "antigravity_cli_switcher.manager.quota.check_token_account_match",
                return_value=mock.Mock(status="ok"),
            ),
        ):
            res = refresh_account_usage(self.paths, self.account_name)
            self.assertEqual(res.backend, "native")
            self.assertEqual(res.short_usage_value, 78.5)
            self.assertEqual(res.weekly_usage_value, 64.39)
            self.assertEqual(res.bucket_count, 4)
            mock_fetch.assert_called_once()

        state = m.load_state(self.paths)
        meta = state["accounts"][self.account_name]
        self.assertEqual(meta["usage_families"]["gemini"]["short"]["value"], 78.5)
        self.assertEqual(meta["health_status"], "ready")
        self.assertIsNone(meta["last_live_check_error"])

    def test_refresh_account_usage_explicit_http_backend(self) -> None:
        load_resp = {"cloudaicompanionProject": "project-synth", "planType": "GEMINI_CODE_ASSIST"}
        summary_resp = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "window": "5h",
                            "remainingFraction": 0.5,
                            "resetTime": "2026-10-04T00:02:14Z",
                        }
                    ],
                }
            ]
        }

        def fake_http(token: str, path: str, payload: dict) -> dict:
            if "loadCodeAssist" in path:
                return load_resp
            return summary_resp

        with (
            mock.patch("antigravity_cli_switcher.manager.quota._cloudcode_request", side_effect=fake_http) as mock_http,
            mock.patch(
                "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
                return_value="ya29.c.b0_synthetic_token",
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.quota.check_token_account_match",
                return_value=mock.Mock(status="ok"),
            ),
        ):
            res = refresh_account_usage(self.paths, self.account_name, backend="http")
            self.assertEqual(res.backend, "http")
            self.assertEqual(res.short_usage_value, 50.0)
            self.assertEqual(mock_http.call_count, 2)

    def test_refresh_account_usage_auto_backend_fallback_on_native_error(self) -> None:
        load_resp = {"cloudaicompanionProject": "project-synth", "planType": "GEMINI_CODE_ASSIST"}
        summary_resp = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "window": "5h",
                            "remainingFraction": 0.8,
                            "resetTime": "2026-10-04T00:02:14Z",
                        }
                    ],
                }
            ]
        }

        def fake_http(token: str, path: str, payload: dict) -> dict:
            if "loadCodeAssist" in path:
                return load_resp
            return summary_resp

        with (
            mock.patch(
                "antigravity_cli_switcher.manager.quota._fetch_native_quota",
                side_effect=ValueError("agy command failed: connection refused"),
            ),
            mock.patch("antigravity_cli_switcher.manager.quota._cloudcode_request", side_effect=fake_http) as mock_http,
            mock.patch(
                "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
                return_value="ya29.c.b0_synthetic_token",
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.quota.check_token_account_match",
                return_value=mock.Mock(status="ok"),
            ),
        ):
            res = refresh_account_usage(self.paths, self.account_name, backend="auto")
            self.assertEqual(res.backend, "http")
            self.assertEqual(res.short_usage_value, 80.0)
            self.assertEqual(mock_http.call_count, 2)

    def test_refresh_account_usage_auto_backend_does_not_fallback_on_ineligible(self) -> None:
        with (
            mock.patch(
                "antigravity_cli_switcher.manager.quota._fetch_native_quota",
                side_effect=ValueError("Eligibility check failed: Account ineligible"),
            ),
            mock.patch("antigravity_cli_switcher.manager.quota._cloudcode_request") as mock_http,
            mock.patch(
                "antigravity_cli_switcher.manager.quota.check_token_account_match",
                return_value=mock.Mock(status="ok"),
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                refresh_account_usage(self.paths, self.account_name, backend="auto")
            self.assertIn("Eligibility check failed", str(ctx.exception))
            mock_http.assert_not_called()

        state = m.load_state(self.paths)
        meta = state["accounts"][self.account_name]
        self.assertEqual(meta["health_status"], "ineligible")


if __name__ == "__main__":
    unittest.main()

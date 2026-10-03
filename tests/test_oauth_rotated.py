from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from antigravity_cli_switcher.manager.candidates import _candidate_health_priority
from antigravity_cli_switcher.manager.paths import build_paths, ensure_layout
from antigravity_cli_switcher.manager.quota import _ensure_fresh_access_token, _refresh_http_quota
from antigravity_cli_switcher.manager.state import load_state, save_state
from antigravity_cli_switcher.manager.verification import _derive_health_status, verify_account
from antigravity_cli_switcher.models import (
    AccountMeta,
    FreshToken,
    HealthStatus,
    ProblemStatus,
)
from antigravity_cli_switcher.tui.theme import format_health_badge


class TestOAuthRotatedStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.paths = build_paths(self.base / "manager")
        ensure_layout(self.paths)
        self.account_name = "test_standby_account"
        self.account_home = self.paths.accounts_dir / self.account_name
        self.account_home.mkdir(parents=True, exist_ok=True)

        state = load_state(self.paths)
        state["accounts"][self.account_name] = {
            "name": self.account_name,
            "enabled": True,
            "status": "ready",
            "health_status": "ready",
        }
        save_state(self.paths, state)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fresh_token_subclass_behavior(self) -> None:
        tok_default = FreshToken("ya29.synthetic_token")
        self.assertEqual(tok_default, "ya29.synthetic_token")
        self.assertFalse(tok_default.fallback_used)
        self.assertIsInstance(tok_default, str)

        tok_fallback = FreshToken("ya29.synthetic_token_b", fallback_used=True)
        self.assertEqual(tok_fallback, "ya29.synthetic_token_b")
        self.assertTrue(tok_fallback.fallback_used)
        self.assertEqual(f"Bearer {tok_fallback}", "Bearer ya29.synthetic_token_b")

    def test_ensure_fresh_access_token_marks_fallback_when_http_refresh_fails(self) -> None:
        expiry_calls = 0

        def fake_expiry_due(home: Path) -> bool:
            nonlocal expiry_calls
            expiry_calls += 1
            return expiry_calls == 1

        with (
            mock.patch("antigravity_cli_switcher.manager.quota._token_expiry_due", side_effect=fake_expiry_due),
            mock.patch("antigravity_cli_switcher.manager.quota._refresh_token_http", return_value=False),
            mock.patch("antigravity_cli_switcher.manager.quota._isolated_keyring_warmup"),
            mock.patch("antigravity_cli_switcher.manager.quota._run_agy_warmup"),
            mock.patch(
                "antigravity_cli_switcher.manager.quota._extract_access_token",
                return_value="ya29.synthetic_fallback_token",
            ),
        ):
            token = _ensure_fresh_access_token(self.account_home)
            self.assertEqual(token, "ya29.synthetic_fallback_token")
            self.assertTrue(token.fallback_used)

    def test_refresh_http_quota_updates_health_to_oauth_rotated_on_fallback(self) -> None:
        load_resp: dict[str, object] = {"cloudaicompanionProject": "project-synth", "planType": "GEMINI_CODE_ASSIST"}
        summary_resp: dict[str, object] = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "window": "5h",
                            "remainingFraction": 0.9,
                            "resetTime": "2026-10-04T00:02:14Z",
                        }
                    ],
                }
            ]
        }

        def fake_cloudcode(token: str, path: str, payload: dict[str, object]) -> dict[str, object]:
            if "loadCodeAssist" in path:
                return load_resp
            return summary_resp

        fallback_token = FreshToken("ya29.synthetic_fallback", fallback_used=True)

        with (
            mock.patch(
                "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
                return_value=fallback_token,
            ),
            mock.patch("antigravity_cli_switcher.manager.quota._cloudcode_request", side_effect=fake_cloudcode),
        ):
            res = _refresh_http_quota(self.paths, self.account_name, self.account_home)
            self.assertEqual(res.backend, "http")

            state = load_state(self.paths)
            meta = state["accounts"][self.account_name]
            self.assertEqual(meta["health_status"], "oauth_rotated")

    def test_verification_preserves_oauth_rotated(self) -> None:
        meta = AccountMeta(
            enabled=True,
            health_status=HealthStatus.OAUTH_ROTATED,
            last_live_check_at="2026-10-04T00:00:00Z",
        )

        with (
            mock.patch(
                "antigravity_cli_switcher.manager.verification._resolve_profile_source", return_value=self.account_home
            ),
            mock.patch("antigravity_cli_switcher.manager.verification.profile_has_login_artifacts", return_value=True),
            mock.patch(
                "antigravity_cli_switcher.manager.verification._extract_access_token", return_value="ya29.valid_token"
            ),
            mock.patch("antigravity_cli_switcher.manager.verification._token_expiry_due", return_value=False),
            mock.patch(
                "antigravity_cli_switcher.manager.verification.check_token_account_match",
                return_value=mock.Mock(status="ok", token_email="user1@example.com", colliding_account=None),
            ),
        ):
            derived = _derive_health_status(self.paths, self.account_name, meta)
            self.assertEqual(derived, HealthStatus.OAUTH_ROTATED)

            ver = verify_account(self.paths, self.account_name, meta)
            self.assertEqual(ver.problem_status, ProblemStatus.OAUTH_ROTATED)
            self.assertEqual(ver.health_status, HealthStatus.OAUTH_ROTATED)
            self.assertEqual(ver.recommended_action, "update_credentials")
            self.assertIn("Google OAuth desktop credentials may have rotated", ver.summary)

    def test_format_health_badge_oauth_rotated(self) -> None:
        badge = format_health_badge("oauth_rotated")
        self.assertEqual(badge.plain, "OAUTH_ROTATED")
        self.assertEqual(badge.style, "bold red reverse")

    def test_candidate_health_priority_oauth_rotated(self) -> None:
        pri = _candidate_health_priority(HealthStatus.OAUTH_ROTATED)
        self.assertEqual(pri, 1)

    def test_list_models_interoperability_with_fresh_token(self) -> None:
        from antigravity_cli_switcher.manager.models_catalog import list_models

        fallback_token = FreshToken("ya29.synthetic_token", fallback_used=True)
        load_resp = {
            "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
        }
        with (
            mock.patch(
                "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
                return_value=fallback_token,
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.models_catalog.check_token_account_match",
                return_value=mock.Mock(status="ok"),
            ),
            mock.patch(
                "antigravity_cli_switcher.manager.models_catalog._cloudcode_request",
                return_value=load_resp,
            ) as mock_req,
        ):
            res = list_models(self.paths, name=self.account_name)
            self.assertEqual(res["count"], 2)
            mock_req.assert_called_once_with(fallback_token, mock.ANY, {})


if __name__ == "__main__":
    unittest.main()

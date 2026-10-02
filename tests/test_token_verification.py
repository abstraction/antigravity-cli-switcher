from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from antigravity_cli_switcher.cli import (
    DEFAULT_SORT_MODE,
    SORT_MODES,
    _family_usage_windows,
    _format_countdown,
    _format_natural_duration,
    _format_reset_compact,
    _format_window_summary,
)
from antigravity_cli_switcher.manager import (
    _identity_from_antigravity_token,
    account_dir,
    apply_active,
    build_paths,
    check_token_account_match,
    clear_bad,
    ensure_layout,
    get_status_snapshot,
    load_state,
    login_account,
    refresh_account_usage,
    resolve_account_name,
    save_state,
    set_expected_email,
    switch_account,
    verify_account,
)


def _make_jwt(email: str, sub: str = "123456789", name: str = "Test User") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "email": email,
                    "sub": sub,
                    "name": name,
                    "email_verified": True,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


def _make_account_with_token(paths, name: str, email: str, expected_email: str | None = None) -> None:
    acct = account_dir(paths, name)
    token_path = acct / ".gemini" / "antigravity-cli"
    token_path.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": {
            "access_token": "ya29.fake",
            "token_type": "Bearer",
            "refresh_token": "1//fake",
            "expiry": "2030-01-01T00:00:00Z",
        },
        "auth_method": "oauth",
        "id_token": _make_jwt(email),
    }
    (token_path / "antigravity-oauth-token").write_text(json.dumps(token_data), encoding="utf-8")

    state = load_state(paths)
    meta = {
        "enabled": True,
        "status": "standby",
        "expected_email": expected_email or email,
        "identity": {
            "account_name": email,
            "email": email,
            "source": "antigravity-oauth-token.id_token",
        },
    }
    state["accounts"][name] = meta
    save_state(paths, state)


class TokenIdentityExtractionTests(unittest.TestCase):
    def test_extracts_from_top_level_id_token(self) -> None:
        jwt_token = _make_jwt("user@example.com", name="Jane Doe", sub="sub-999")
        token_state = {
            "token": {
                "access_token": "ya29.fake",
                "refresh_token": "1//fake",
            },
            "auth_method": "oauth",
            "id_token": jwt_token,
        }
        identity = _identity_from_antigravity_token(token_state)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity["email"], "user@example.com")
        self.assertEqual(identity["account_name"], "user@example.com")
        self.assertEqual(identity["display_name"], "Jane Doe")
        self.assertEqual(identity["subject"], "sub-999")
        self.assertEqual(identity["source"], "antigravity-oauth-token.id_token")

    def test_extracts_from_nested_token_id_token(self) -> None:
        jwt_token = _make_jwt("nested@example.com")
        token_state = {
            "token": {
                "access_token": "ya29.fake",
                "id_token": jwt_token,
            },
        }
        identity = _identity_from_antigravity_token(token_state)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity["email"], "nested@example.com")


class TokenAccountMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.paths = build_paths(Path(self._tmp))
        ensure_layout(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_matching_token_returns_ok(self) -> None:
        _make_account_with_token(self.paths, "acc1", "acc1@gmail.com")
        source = account_dir(self.paths, "acc1")
        result = check_token_account_match(self.paths, "acc1", source)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.token_email, "acc1@gmail.com")

    def test_mismatched_token_returns_mismatch(self) -> None:
        _make_account_with_token(self.paths, "acc1", "wrong@gmail.com", expected_email="acc1@gmail.com")
        source = account_dir(self.paths, "acc1")
        result = check_token_account_match(self.paths, "acc1", source)
        self.assertEqual(result.status, "mismatch")
        self.assertEqual(result.token_email, "wrong@gmail.com")
        self.assertEqual(result.expected_email, "acc1@gmail.com")
        self.assertIn("Token mismatch", result.message)

    def test_duplicate_token_across_accounts_detected(self) -> None:
        _make_account_with_token(self.paths, "acc1", "shared@gmail.com")
        _make_account_with_token(self.paths, "acc2", "shared@gmail.com", expected_email="acc2@gmail.com")
        source = account_dir(self.paths, "acc2")
        result = check_token_account_match(self.paths, "acc2", source)
        # acc2 has expected acc2@gmail.com but token is shared@gmail.com -> mismatch or duplicate
        self.assertIn(result.status, {"mismatch", "duplicate"})

    def test_duplicate_token_when_no_expected_email(self) -> None:
        _make_account_with_token(self.paths, "acc1", "shared@gmail.com")
        # acc2 has token shared@gmail.com with no distinct expected email set
        acct2 = account_dir(self.paths, "acc2")
        token_path = acct2 / ".gemini" / "antigravity-cli"
        token_path.mkdir(parents=True, exist_ok=True)
        (token_path / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": "fake"}, "id_token": _make_jwt("shared@gmail.com")}),
            encoding="utf-8",
        )
        state = load_state(self.paths)
        state["accounts"]["acc2"] = {"enabled": True, "status": "standby"}
        save_state(self.paths, state)

        result = check_token_account_match(self.paths, "acc2", acct2)
        self.assertEqual(result.status, "duplicate")
        self.assertEqual(result.colliding_account, "acc1")


class RefreshUsageAbortOnMismatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.paths = build_paths(Path(self._tmp))
        ensure_layout(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("antigravity_cli_switcher.manager._cloudcode_request")
    def test_aborts_before_api_calls_on_mismatch(self, mock_cloudcode) -> None:
        _make_account_with_token(self.paths, "acc1", "wrong@gmail.com", expected_email="acc1@gmail.com")
        with self.assertRaises(ValueError) as ctx:
            refresh_account_usage(self.paths, "acc1")
        self.assertIn("Token mismatch", str(ctx.exception))
        mock_cloudcode.assert_not_called()

        state = load_state(self.paths)
        self.assertEqual(state["accounts"]["acc1"]["health_status"], "token_mismatch")
        self.assertIn("Token mismatch", state["accounts"]["acc1"]["last_live_check_error"])


class VerifyAccountTokenChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.paths = build_paths(Path(self._tmp))
        ensure_layout(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_verify_flags_token_mismatch(self) -> None:
        _make_account_with_token(self.paths, "acc1", "wrong@gmail.com", expected_email="acc1@gmail.com")
        state = load_state(self.paths)
        from antigravity_cli_switcher.models import AccountMeta

        meta = AccountMeta(**state["accounts"]["acc1"])
        result = verify_account(self.paths, "acc1", meta)
        self.assertEqual(result.problem_status.value, "token_mismatch")
        self.assertEqual(result.recommended_action, "relogin")
        self.assertIn("Token mismatch", result.summary)

    def test_verify_flags_ineligible_from_stored_health_status(self) -> None:
        account_name = "test_acct_ineligible"
        account_email = "ineligible_user@corp.internal"
        _make_account_with_token(self.paths, account_name, account_email, expected_email=account_email)
        state = load_state(self.paths)
        state["accounts"][account_name]["health_status"] = "ineligible"
        state["accounts"][account_name]["last_live_check_at"] = "2026-10-02T17:54:53+00:00"
        state["accounts"][account_name]["last_live_check_error"] = None
        save_state(self.paths, state)

        from antigravity_cli_switcher.models import AccountMeta, HealthStatus, ProblemStatus

        meta = AccountMeta(**state["accounts"][account_name])
        result = verify_account(self.paths, account_name, meta)
        self.assertEqual(result.problem_status, ProblemStatus.INELIGIBLE)
        self.assertEqual(result.problem_status.value, "ineligible")
        self.assertEqual(result.recommended_action, "human_intervention")
        self.assertEqual(result.health_status, HealthStatus.INELIGIBLE)
        self.assertIn("human intervention", result.summary)

    def test_verify_flags_ineligible_from_live_check_error(self) -> None:
        account_name = "test_acct_ineligible"
        account_email = "ineligible_user@corp.internal"
        _make_account_with_token(self.paths, account_name, account_email, expected_email=account_email)
        state = load_state(self.paths)
        state["accounts"][account_name]["last_live_check_error"] = (
            "Eligibility check failed: Your current account is not eligible for Antigravity. "
            "Verify your account to continue."
        )
        save_state(self.paths, state)

        from antigravity_cli_switcher.models import AccountMeta, HealthStatus, ProblemStatus

        meta = AccountMeta(**state["accounts"][account_name])
        result = verify_account(self.paths, account_name, meta)
        self.assertEqual(result.problem_status, ProblemStatus.INELIGIBLE)
        self.assertEqual(result.recommended_action, "human_intervention")
        self.assertEqual(result.health_status, HealthStatus.INELIGIBLE)


class LoginAccountConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.paths = build_paths(Path(self._tmp))
        ensure_layout(self.paths)
        self.live_dir = self.paths.root / ".gemini"
        state = load_state(self.paths)
        state["live_dir"] = str(self.live_dir.resolve())
        save_state(self.paths, state)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("os.isatty", return_value=True)
    @patch("subprocess.Popen")
    @patch("builtins.input", return_value="n")
    def test_login_declined_aborts_without_saving(self, mock_input, mock_popen, mock_isatty) -> None:
        def popen_side_effect(*args, **kwargs):
            cwd_path = kwargs.get("cwd")

            def write_token():
                token_dir = cwd_path / ".gemini" / "antigravity-cli"
                token_dir.mkdir(parents=True, exist_ok=True)
                (token_dir / "antigravity-oauth-token").write_text(
                    json.dumps({"token": {"access_token": "ya29.test"}, "id_token": _make_jwt("test@gmail.com")}),
                    encoding="utf-8",
                )
                return 0

            proc = MagicMock()
            proc.poll.side_effect = write_token
            proc.returncode = 0
            return proc

        mock_popen.side_effect = popen_side_effect

        saved = login_account(self.paths, "new_acc", agy_binary="/fake/agy")
        self.assertIsNone(saved)
        self.assertFalse(account_dir(self.paths, "new_acc").exists())

    @patch("os.isatty", return_value=True)
    @patch("subprocess.Popen")
    @patch("builtins.input", side_effect=["y", "y"])
    def test_login_confirmed_saves_account_and_expected_email(self, mock_input, mock_popen, mock_isatty) -> None:
        def popen_side_effect(*args, **kwargs):
            cwd_path = kwargs.get("cwd")

            def write_token():
                token_dir = cwd_path / ".gemini" / "antigravity-cli"
                token_dir.mkdir(parents=True, exist_ok=True)
                (token_dir / "antigravity-oauth-token").write_text(
                    json.dumps({"token": {"access_token": "ya29.test"}, "id_token": _make_jwt("confirmed@gmail.com")}),
                    encoding="utf-8",
                )
                return 0

            proc = MagicMock()
            proc.poll.side_effect = write_token
            proc.returncode = 0
            return proc

        mock_popen.side_effect = popen_side_effect

        saved = login_account(self.paths, "new_acc", agy_binary="/fake/agy")
        self.assertEqual(saved, "new_acc")
        self.assertTrue(account_dir(self.paths, "new_acc").exists())
        state = load_state(self.paths)
        self.assertEqual(state["accounts"]["new_acc"]["expected_email"], "confirmed@gmail.com")

    @patch("os.isatty", return_value=True)
    @patch("subprocess.Popen")
    @patch("builtins.input", return_value="y")
    def test_relogin_overwrites_existing_and_clears_ineligible_errors(
        self, mock_input, mock_popen, mock_isatty
    ) -> None:
        # Create an existing account with ineligibility error and fail count
        target_dir = account_dir(self.paths, "[3] gamma")
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": "ya29.old"}, "id_token": _make_jwt("old@gmail.com")}),
            encoding="utf-8",
        )
        state = load_state(self.paths)
        state["accounts"]["[3] gamma"] = {
            "enabled": True,
            "status": "standby",
            "health_status": "ineligible",
            "last_live_check_error": "Eligibility check failed | error 403",
            "last_error": "warmup failed",
            "fail_count": 5,
            "refresh_fail_count": 2,
        }
        save_state(self.paths, state)

        def popen_side_effect(*args, **kwargs):
            cwd_path = kwargs.get("cwd")

            def write_token():
                token_dir = cwd_path / ".gemini" / "antigravity-cli"
                token_dir.mkdir(parents=True, exist_ok=True)
                (token_dir / "antigravity-oauth-token").write_text(
                    json.dumps({"token": {"access_token": "ya29.fresh"}, "id_token": _make_jwt("gamma@example.com")}),
                    encoding="utf-8",
                )
                return 0

            proc = MagicMock()
            proc.poll.side_effect = write_token
            proc.returncode = 0
            return proc

        mock_popen.side_effect = popen_side_effect

        # Relogin with overwrite_existing=True
        saved = login_account(self.paths, "[3] gamma", agy_binary="/fake/agy", overwrite_existing=True)
        self.assertEqual(saved, "[3] gamma")

        updated_state = load_state(self.paths)
        account_meta = updated_state["accounts"]["[3] gamma"]
        self.assertIsNone(account_meta["last_live_check_error"])
        self.assertEqual(account_meta["health_status"], "ready")
        self.assertEqual(account_meta["fail_count"], 0)
        self.assertEqual(account_meta["refresh_fail_count"], 0)
        self.assertIsNone(account_meta["last_error"])
        self.assertEqual(account_meta["expected_email"], "gamma@example.com")

    @patch("os.isatty", return_value=True)
    @patch("subprocess.Popen")
    @patch("builtins.input", return_value="y")
    def test_login_resolves_bracketed_alias_and_numeric_index(self, mock_input, mock_popen, mock_isatty) -> None:
        target_dir = account_dir(self.paths, "[3] gamma")
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": "ya29.old"}, "id_token": _make_jwt("old@gmail.com")}),
            encoding="utf-8",
        )
        state = load_state(self.paths)
        state["accounts"]["[3] gamma"] = {
            "enabled": True,
            "status": "standby",
            "health_status": "ineligible",
            "last_live_check_error": "Eligibility check failed",
        }
        save_state(self.paths, state)

        def popen_side_effect(*args, **kwargs):
            cwd_path = kwargs.get("cwd")

            def write_token():
                token_dir = cwd_path / ".gemini" / "antigravity-cli"
                token_dir.mkdir(parents=True, exist_ok=True)
                (token_dir / "antigravity-oauth-token").write_text(
                    json.dumps(
                        {"token": {"access_token": "ya29.resolved"}, "id_token": _make_jwt("gamma@example.com")}
                    ),
                    encoding="utf-8",
                )
                return 0

            proc = MagicMock()
            proc.poll.side_effect = write_token
            proc.returncode = 0
            return proc

        mock_popen.side_effect = popen_side_effect

        # Call with numeric index "3"
        saved = login_account(self.paths, "3", agy_binary="/fake/agy", overwrite_existing=True)
        self.assertEqual(saved, "[3] gamma")

    def test_clear_bad_clears_live_check_error(self) -> None:
        account_dir(self.paths, "acc_err").mkdir(parents=True, exist_ok=True)
        state = load_state(self.paths)
        state["accounts"]["acc_err"] = {
            "enabled": True,
            "status": "standby",
            "health_status": "ineligible",
            "last_live_check_error": "Eligibility check failed",
            "last_error": "fail",
            "fail_count": 3,
            "refresh_fail_count": 2,
        }
        save_state(self.paths, state)

        clear_bad(self.paths, "acc_err")

        updated = load_state(self.paths)
        meta = updated["accounts"]["acc_err"]
        self.assertIsNone(meta["last_live_check_error"])
        self.assertIsNone(meta["last_error"])
        self.assertEqual(meta["fail_count"], 0)
        self.assertEqual(meta["refresh_fail_count"], 0)


class DashboardFormattingAndSortTests(unittest.TestCase):
    def test_default_sort_is_usage_low(self) -> None:
        self.assertEqual(DEFAULT_SORT_MODE, "usage-low")
        mode_keys = [mode[0] for mode in SORT_MODES]
        self.assertIn("usage-low", mode_keys)

    def test_natural_duration_formatting(self) -> None:
        # Multi-day
        self.assertEqual(_format_natural_duration(86400 * 6 + 3600 * 0 + 60 * 50), "6d 0h 50m")
        self.assertEqual(_format_natural_duration(86400 * 1 + 3600 * 10 + 60 * 20), "1d 10h 20m")
        # Hours
        self.assertEqual(_format_natural_duration(3600 * 4 + 60 * 15), "4h 15m")
        # Minutes and seconds
        self.assertEqual(_format_natural_duration(60 * 23 + 56), "23m 56s")
        # Sub-minute
        self.assertEqual(_format_natural_duration(45), "45s")
        self.assertEqual(_format_natural_duration(0), "due")

    def test_compact_reset_formatting(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 6 days away
        win_weekly = {"reset_at": "2026-01-07T12:00:00+00:00"}
        self.assertEqual(_format_reset_compact(win_weekly, now), "6d")
        # 23 minutes away
        win_short = {"reset_at": "2026-01-01T12:23:00+00:00"}
        self.assertEqual(_format_reset_compact(win_short, now), "23m")
        # 4 hours away
        win_hours = {"reset_at": "2026-01-01T16:00:00+00:00"}
        self.assertEqual(_format_reset_compact(win_hours, now), "4h")

    def test_format_countdown_shows_sw_for_both_models(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta = {
            "usage_windows": {
                "gemini_short": {"value": 10.0, "status": "ok", "reset_at": "2026-01-01T12:23:00+00:00"},
                "gemini_weekly": {"value": 50.0, "status": "ok", "reset_at": "2026-01-07T12:00:00+00:00"},
                "claude_short": {"value": 20.0, "status": "ok", "reset_at": "2026-01-01T12:12:00+00:00"},
                "claude_weekly": {"value": 60.0, "status": "ok", "reset_at": "2026-01-06T12:00:00+00:00"},
            }
        }
        res = _format_countdown(meta, now)
        self.assertEqual(res, "G:23m/6d C:12m/5d")

    def test_format_countdown_with_usage_families(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta = {
            "usage_families": {
                "gemini": {
                    "short": {"value": 10.0, "status": "ok", "reset_at": "2026-01-01T12:23:00+00:00"},
                    "weekly": {"value": 50.0, "status": "ok", "reset_at": "2026-01-07T12:00:00+00:00"},
                },
                "other": {
                    "short": {"value": 20.0, "status": "ok", "reset_at": "2026-01-01T12:12:00+00:00"},
                    "weekly": {"value": 60.0, "status": "ok", "reset_at": "2026-01-06T12:00:00+00:00"},
                },
            }
        }
        res = _format_countdown(meta, now)
        self.assertEqual(res, "G:23m/6d C:12m/5d")

    def test_window_summary_uses_natural_duration(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta = {
            "usage_windows": {
                "gemini_weekly": {"value": 100.0, "status": "ok", "reset_at": "2026-01-07T12:50:00+00:00"}
            }
        }
        summary = _format_window_summary(meta, "gemini_weekly", now)
        self.assertEqual(summary, "100% (in 6d 0h 50m)")

    def test_family_usage_windows(self) -> None:
        meta_families = {
            "usage_families": {
                "gemini": {
                    "short": {"value": 80.0, "status": "ok"},
                    "weekly": {"value": 90.0, "status": "ok"},
                },
                "other": {
                    "short": {"value": 15.0, "status": "ok"},
                    "weekly": {"value": 50.0, "status": "ok"},
                },
            }
        }
        self.assertEqual(_family_usage_windows(meta_families, "gemini")["short"]["value"], 80.0)
        self.assertEqual(_family_usage_windows(meta_families, "claude")["short"]["value"], 15.0)
        self.assertEqual(_family_usage_windows(meta_families, "other")["short"]["value"], 15.0)

        meta_legacy = {
            "usage_windows": {
                "short": {"value": 5.0, "status": "ok"},
                "weekly": {"value": 10.0, "status": "ok"},
                "claude_short": {"value": 25.0, "status": "ok"},
                "claude_weekly": {"value": 75.0, "status": "ok"},
            }
        }
        self.assertEqual(_family_usage_windows(meta_legacy, "gemini")["short"]["value"], 5.0)
        self.assertEqual(_family_usage_windows(meta_legacy, "claude")["short"]["value"], 25.0)


class ExpectedEmailAndMismatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.paths = build_paths(self.tmp_dir)
        ensure_layout(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_set_expected_email_sets_and_clears(self) -> None:
        _make_account_with_token(self.paths, "test-acc", "token@example.com")
        set_expected_email(self.paths, "test-acc", "expected@example.com")

        state = load_state(self.paths)
        self.assertEqual(state["accounts"]["test-acc"]["expected_email"], "expected@example.com")

        # Clear email
        set_expected_email(self.paths, "test-acc", "   ")
        state = load_state(self.paths)
        self.assertNotIn("expected_email", state["accounts"]["test-acc"])

        # Non-existent account raises ValueError
        with self.assertRaises(ValueError) as ctx:
            set_expected_email(self.paths, "non-existent", "foo@example.com")
        self.assertIn("Account 'non-existent' not found", str(ctx.exception))

    def test_snapshot_includes_plan_type_and_expected_email(self) -> None:
        _make_account_with_token(self.paths, "acc1", "user1@example.com", expected_email="user1@example.com")
        state = load_state(self.paths)
        state["accounts"]["acc1"]["plan_type"] = "pro"
        save_state(self.paths, state)

        snapshot = get_status_snapshot(self.paths)
        self.assertIn("acc1", snapshot["accounts"])
        self.assertEqual(snapshot["accounts"]["acc1"]["plan_type"], "pro")
        self.assertEqual(snapshot["accounts"]["acc1"]["expected_email"], "user1@example.com")

    def test_switch_account_mismatch_hard_gate(self) -> None:
        # Create standby account with mismatched token vs expected_email
        _make_account_with_token(self.paths, "active-acc", "active@example.com")
        _make_account_with_token(
            self.paths,
            "mismatch-acc",
            "token_email@example.com",
            expected_email="different_expected@example.com",
        )

        state = load_state(self.paths)
        state["active"] = "active-acc"
        save_state(self.paths, state)

        with self.assertRaises(ValueError) as ctx:
            switch_account(self.paths, "mismatch-acc")

        self.assertIn("Token mismatch for 'mismatch-acc'", str(ctx.exception))
        self.assertIn("expected different_expected@example.com", str(ctx.exception))
        self.assertIn("got token_email@example.com", str(ctx.exception))

        # Active account should not have changed
        current_state = load_state(self.paths)
        self.assertEqual(current_state["active"], "active-acc")

    @patch("antigravity_cli_switcher.manager.accounts._ensure_safe_account_switch")
    def test_apply_active_mismatch_hard_gate(self, mock_safe_switch: MagicMock) -> None:
        _make_account_with_token(
            self.paths,
            "mismatch-active",
            "actual_token@example.com",
            expected_email="target_expected@example.com",
        )

        state = load_state(self.paths)
        state["active"] = "mismatch-active"
        save_state(self.paths, state)

        with self.assertRaises(ValueError) as ctx:
            apply_active(self.paths)

        self.assertIn("Token mismatch for 'mismatch-active'", str(ctx.exception))
        self.assertIn("expected target_expected@example.com", str(ctx.exception))

    def test_resolve_account_name(self) -> None:
        state: dict[str, dict[str, dict[str, str]]] = {
            "accounts": {
                "[1] omega": {},
                "[2] alpha": {},
                "gamma": {},
            }
        }
        # Exact match
        self.assertEqual(resolve_account_name(state, "[1] omega"), "[1] omega")
        self.assertEqual(resolve_account_name(state, "gamma"), "gamma")

        # Case-insensitive
        self.assertEqual(resolve_account_name(state, "GAMMA"), "gamma")

        # By number
        self.assertEqual(resolve_account_name(state, "1"), "[1] omega")
        self.assertEqual(resolve_account_name(state, "2"), "[2] alpha")

        # Stripping bracket prefix
        self.assertEqual(resolve_account_name(state, "omega"), "[1] omega")
        self.assertEqual(resolve_account_name(state, "alpha"), "[2] alpha")

        # Fallback
        self.assertEqual(resolve_account_name(state, "unknown"), "unknown")

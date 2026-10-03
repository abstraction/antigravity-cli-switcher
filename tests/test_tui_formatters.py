import inspect
import tempfile
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from antigravity_cli_switcher.manager.paths import build_paths
from antigravity_cli_switcher.manager.quota import _persist_refresh_failure
from antigravity_cli_switcher.manager.state import load_state, parse_timestamp, save_state, utc_now
from antigravity_cli_switcher.models import AccountMeta, ProblemStatus, UsageWindow
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.formatters import (
    format_countdown,
    format_model_usage,
    format_natural_duration,
    format_next_refresh,
    format_problem_summary,
)
from tests.conftest import ORIGINAL_START_DUE_WATCHER


class TUIFormattersTests(unittest.TestCase):
    def test_natural_duration(self) -> None:
        self.assertEqual(format_natural_duration(0), "due")
        self.assertEqual(format_natural_duration(-10), "due")
        self.assertEqual(format_natural_duration(45), "45s")
        self.assertEqual(format_natural_duration(120), "2m")
        self.assertEqual(format_natural_duration(150), "2m 30s")
        self.assertEqual(format_natural_duration(3600), "1h")
        self.assertEqual(format_natural_duration(3660), "1h 1m")
        self.assertEqual(format_natural_duration(86400), "1d")
        self.assertEqual(format_natural_duration(90000), "1d 1h")

    def test_claude_model_usage_from_usage_families(self) -> None:
        meta = AccountMeta(
            usage_families={
                "other": {
                    "short": UsageWindow(status="ok", value=95.0),
                    "weekly": UsageWindow(status="ok", value=80.0),
                },
                "gemini": {
                    "short": UsageWindow(status="ok", value=100.0),
                    "weekly": UsageWindow(status="ok", value=90.0),
                },
            }
        )
        claude_str = format_model_usage(meta, "claude")
        self.assertEqual(claude_str.strip(), "95%/80%")

        gemini_str = format_model_usage(meta, "gemini")
        self.assertEqual(gemini_str.strip(), "100%/90%")

    def test_claude_countdown_from_usage_families(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta = AccountMeta(
            usage_families={
                "other": {
                    "short": UsageWindow(status="ok", reset_at="2026-01-01T14:00:00+00:00"),
                    "weekly": UsageWindow(status="ok", reset_at="2026-01-07T12:00:00+00:00"),
                },
                "gemini": {
                    "short": UsageWindow(status="ok", reset_at="2026-01-01T12:30:00+00:00"),
                    "weekly": UsageWindow(status="ok", reset_at="2026-01-08T12:00:00+00:00"),
                },
            }
        )
        res = format_countdown(meta, now)
        self.assertEqual(res, "G:30m/7d C:2h/6d")

    def test_next_refresh_with_scheduled_timestamp(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # In the future
        meta_future = AccountMeta(
            next_live_check_at="2026-01-01T12:05:00+00:00",
            refresh_policy_seconds=300,
        )
        self.assertEqual(format_next_refresh(meta_future, now), "5m")

        # In the past
        meta_past = AccountMeta(
            next_live_check_at="2026-01-01T11:59:00+00:00",
            refresh_policy_seconds=300,
        )
        self.assertEqual(format_next_refresh(meta_past, now), "due")

    def test_next_refresh_fallback_to_last_check_plus_policy(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Last check was 100 seconds ago, policy is 300s -> 200s left (3m 20s)
        meta_recent = AccountMeta(
            last_live_check_at="2026-01-01T11:58:20+00:00",
            refresh_policy_seconds=300,
        )
        self.assertEqual(format_next_refresh(meta_recent, now), "3m 20s")

        # Last check was 400 seconds ago, policy is 300s -> due
        meta_overdue = AccountMeta(
            last_live_check_at="2026-01-01T11:50:00+00:00",
            refresh_policy_seconds=300,
        )
        self.assertEqual(format_next_refresh(meta_overdue, now), "due")

        # Disabled account returns "-"
        meta_disabled = AccountMeta(
            enabled=False,
            last_live_check_at="2026-01-01T11:50:00+00:00",
            refresh_policy_seconds=300,
        )
        self.assertEqual(format_next_refresh(meta_disabled, now), "-")


class TUIBackgroundRefreshTests(unittest.TestCase):
    def test_check_due_refresh_triggers_worker(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)

        with patch(
            "antigravity_cli_switcher.tui.app.pick_due_refresh_account", return_value="due-account"
        ) as mock_pick:
            with patch.object(app, "_refresh_account_worker") as mock_worker:
                result = app.check_due_refresh()
                mock_pick.assert_called_once_with(paths, exclude=set())
                mock_worker.assert_called_once_with("due-account")
                self.assertEqual(result, "due-account")

    def test_check_due_refresh_none_when_no_account_due(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)

        with patch("antigravity_cli_switcher.tui.app.pick_due_refresh_account", return_value=None) as mock_pick:
            with patch.object(app, "_refresh_account_worker") as mock_worker:
                result = app.check_due_refresh()
                mock_pick.assert_called_once_with(paths, exclude=set())
                mock_worker.assert_not_called()
                self.assertIsNone(result)

    def test_persist_refresh_failure_schedules_next_check(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            paths = build_paths(Path(td))
            (paths.accounts_dir / "acc1").mkdir(parents=True, exist_ok=True)
            save_state(paths, {"accounts": {"acc1": {"refresh_policy_seconds": 120}}})

            _persist_refresh_failure(paths, "acc1", "mock error")
            state = load_state(paths)
            meta = state["accounts"]["acc1"]
            self.assertEqual(meta["last_live_check_error"], "mock error")
            self.assertIsNotNone(meta.get("next_live_check_at"))

    def test_parse_timestamp_utc_awareness_and_formats(self) -> None:
        self.assertIsNone(parse_timestamp(None))
        self.assertIsNone(parse_timestamp(""))
        self.assertIsNone(parse_timestamp("invalid-date"))

        # Z suffix
        dt_z = parse_timestamp("2026-10-02T12:00:00Z")
        self.assertIsNotNone(dt_z)
        assert dt_z is not None
        self.assertEqual(dt_z.tzinfo, timezone.utc)
        self.assertEqual(dt_z.hour, 12)

        # Naive string normalized to UTC aware
        dt_naive = parse_timestamp("2026-10-02T12:00:00")
        self.assertIsNotNone(dt_naive)
        assert dt_naive is not None
        self.assertEqual(dt_naive.tzinfo, timezone.utc)
        self.assertTrue(dt_naive <= utc_now())

        # Offset-aware string converted to UTC
        dt_offset = parse_timestamp("2026-10-02T17:30:00+05:30")
        self.assertIsNotNone(dt_offset)
        assert dt_offset is not None
        self.assertEqual(dt_offset.tzinfo, timezone.utc)
        self.assertEqual(dt_offset.hour, 12)

    def test_due_check_interval_seconds_default(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)
        self.assertEqual(app.due_check_interval_seconds, 5.0)

    def test_refresh_account_worker_chains_next_due_account(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)

        with patch("antigravity_cli_switcher.tui.app.refresh_quota_data") as mock_refresh:
            mock_res = type(
                "MockRes",
                (),
                {"short_usage_status": "ok", "weekly_usage_status": "ok"},
            )()
            mock_refresh.return_value = mock_res
            with patch.object(app, "refresh_snapshot"):
                with patch.object(app, "check_due_refresh") as mock_check_due:
                    # Run worker body (the wrapped unmanaged function)
                    worker_fn: Callable[[ACSApp, str], None] = inspect.unwrap(ACSApp._refresh_account_worker)
                    worker_fn(app, "acc1")
                    self.assertNotIn("acc1", app._refreshing_accounts)
                    mock_check_due.assert_not_called()

    def test_refresh_account_worker_honors_exit_event(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)
        app._exit_event.set()

        with patch("antigravity_cli_switcher.tui.app.refresh_quota_data") as mock_refresh:
            mock_res = type(
                "MockRes",
                (),
                {"short_usage_status": "ok", "weekly_usage_status": "ok"},
            )()
            mock_refresh.return_value = mock_res
            with patch.object(app, "refresh_snapshot"):
                with patch.object(app, "check_due_refresh") as mock_check_due:
                    worker_fn: Callable[[ACSApp, str], None] = inspect.unwrap(ACSApp._refresh_account_worker)
                    worker_fn(app, "acc1")
                    mock_check_due.assert_not_called()

    def test_start_due_watcher_checks_immediately(self) -> None:
        paths = build_paths("/tmp/mock_root")
        app = ACSApp(paths)

        def mock_check() -> None:
            app._exit_event.set()

        with patch.object(app, "check_due_refresh", side_effect=mock_check) as mock_check_due:
            watcher_fn: Callable[[ACSApp], None] = inspect.unwrap(ORIGINAL_START_DUE_WATCHER)
            watcher_fn(app)
            mock_check_due.assert_called_once()

    def test_format_problem_summary(self) -> None:
        self.assertEqual(format_problem_summary(ProblemStatus.OK), "None")
        self.assertEqual(format_problem_summary(ProblemStatus.OK, "none"), "None")
        self.assertEqual(format_problem_summary(None), "None")
        self.assertEqual(format_problem_summary("ok"), "None")
        self.assertEqual(
            format_problem_summary(ProblemStatus.MISSING_AUTH, "relogin"),
            "Missing credentials (relogin required)",
        )
        self.assertEqual(
            format_problem_summary(ProblemStatus.INELIGIBLE, "human_intervention"),
            "Ineligible (human intervention needed)",
        )
        self.assertEqual(
            format_problem_summary(ProblemStatus.COOLDOWN, "wait"),
            "Active cooldown (waiting cooldown)",
        )
        self.assertEqual(
            format_problem_summary(ProblemStatus.DISABLED, "enable"),
            "Disabled (enable required)",
        )
        self.assertEqual(
            format_problem_summary(ProblemStatus.REFRESH_FAILED, "refresh"),
            "Live check failed (refresh due)",
        )
        self.assertEqual(
            format_problem_summary(ProblemStatus.SYNTHETIC_TOKEN, "fix"),
            "Synthetic token (fix needed)",
        )

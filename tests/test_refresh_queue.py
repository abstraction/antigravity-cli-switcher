from __future__ import annotations

import unittest
from datetime import datetime, timezone

from antigravity_cli_switcher.models import AccountMeta, HealthStatus, UsageWindow
from antigravity_cli_switcher.tui.formatters import (
    compute_refresh_queue,
    format_next_refresh,
    get_next_refresh_time,
    is_account_due_for_refresh,
)


class RefreshQueueTests(unittest.TestCase):
    def test_compute_refresh_queue_future_clock_skew_clamped(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta = AccountMeta(next_live_check_at="2026-01-01T11:00:00+00:00")
        # last refresh set 10 minutes into the future
        queue = compute_refresh_queue(
            {"acc": meta},
            None,
            now,
            last_background_refresh_at="2026-01-01T12:10:00+00:00",
        )
        # Cooldown ETA must be clamped to pacing_seconds (35s), NOT 635s
        self.assertEqual(queue["acc"].eta_seconds, 35)

    def test_compute_refresh_queue_naive_datetime(self) -> None:
        now_naive = datetime(2026, 1, 1, 12, 0, 0)
        meta = AccountMeta(next_live_check_at="2026-01-01T11:00:00+00:00")
        queue = compute_refresh_queue(
            {"acc": meta},
            None,
            now_naive,
            last_background_refresh_at="2026-01-01T11:59:40+00:00",
        )
        self.assertIn("acc", queue)
        self.assertEqual(queue["acc"].position, 1)
        self.assertEqual(queue["acc"].eta_seconds, 15)

    def test_is_account_due_for_refresh_exhausted_sleeping(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta_exhausted = AccountMeta(
            last_live_check_at="2026-01-01T11:50:00+00:00",
            refresh_policy_seconds=300,
            usage_windows={"short": UsageWindow(value=0.0, reset_at="2026-01-01T13:00:00+00:00")},
        )
        # Account is sleeping until reset at 13:00, not due now
        self.assertFalse(is_account_due_for_refresh(meta_exhausted, now))

    def test_format_next_refresh_exhausted_sleeping_shows_reset_countdown(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta_exhausted = AccountMeta(
            last_live_check_at="2026-01-01T11:50:00+00:00",
            refresh_policy_seconds=300,
            usage_windows={"short": UsageWindow(value=0.0, reset_at="2026-01-01T13:00:00+00:00")},
        )
        # Must show time until reset ("1h"), NOT falsely show "due"
        self.assertEqual(format_next_refresh(meta_exhausted, now), "1h")

    def test_format_next_refresh_ineligible_error_fallback(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        meta_ineligible_error = AccountMeta(
            health_status=HealthStatus.UNKNOWN,
            last_live_check_at="2026-01-01T11:00:00+00:00",
            last_live_check_error="403 User is ineligible for Cloud Code PA",
            refresh_policy_seconds=300,
        )
        self.assertFalse(is_account_due_for_refresh(meta_ineligible_error, now))
        # 24h backoff: checked 1 hour ago -> 23h remaining
        self.assertEqual(format_next_refresh(meta_ineligible_error, now), "23h")

    def test_get_next_refresh_time_all_branches(self) -> None:
        # Disabled account
        disabled = AccountMeta(enabled=False)
        self.assertIsNone(get_next_refresh_time(disabled))

        # Explicit next_live_check_at
        explicit = AccountMeta(next_live_check_at="2026-01-01T12:30:00+00:00")
        target = get_next_refresh_time(explicit)
        assert target is not None
        self.assertEqual(target.isoformat(), "2026-01-01T12:30:00+00:00")


if __name__ == "__main__":
    unittest.main()

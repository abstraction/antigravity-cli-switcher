"""Unit tests for TUI usage scoring and account sort key calculation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    HealthStatus,
    ProblemStatus,
    UsageWindow,
)
from antigravity_cli_switcher.tui.usage_scoring import (
    calculate_account_usage_score,
    usage_sort_key,
)


def test_usage_scoring_bottleneck_principles() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    # Short 100%, Weekly 20% -> Effective quota should be 20% (weekly is bottleneck)
    meta_weekly = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(status="known", value=100.0),
                "weekly": UsageWindow(status="known", value=20.0),
            }
        }
    )
    score1 = calculate_account_usage_score(meta_weekly, now)
    assert score1.has_known_quota is True
    assert score1.gemini_effective == 20.0
    assert score1.effective_quota == 20.0
    assert score1.max_headroom == 20.0

    # Short 15%, Weekly 90% -> Effective quota should be 15% (short is bottleneck)
    meta_short = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(status="known", value=15.0),
                "weekly": UsageWindow(status="known", value=90.0),
            }
        }
    )
    score2 = calculate_account_usage_score(meta_short, now)
    assert score2.gemini_effective == 15.0
    assert score2.effective_quota == 15.0


def test_usage_scoring_multi_family_combining() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    meta_both = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(status="known", value=100.0),
                "weekly": UsageWindow(status="known", value=100.0),
            },
            "other": {
                "short": UsageWindow(status="known", value=100.0),
                "weekly": UsageWindow(status="known", value=100.0),
            },
        }
    )
    score_both = calculate_account_usage_score(meta_both, now)
    assert score_both.effective_quota == 100.0
    assert score_both.max_headroom == 100.0
    assert score_both.min_headroom == 100.0

    meta_mixed = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(status="known", value=100.0),
                "weekly": UsageWindow(status="known", value=100.0),
            },
            "other": {
                "short": UsageWindow(status="known", value=0.0),
                "weekly": UsageWindow(status="known", value=0.0),
            },
        }
    )
    score_mixed = calculate_account_usage_score(meta_mixed, now)
    assert score_mixed.effective_quota == 50.0
    assert score_mixed.max_headroom == 100.0
    assert score_mixed.min_headroom == 0.0


def test_usage_scoring_legacy_window_fallback() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta_legacy = AccountMeta(
        usage_windows={
            "short": UsageWindow(status="known", value=75.0),
            "weekly": UsageWindow(status="known", value=85.0),
        }
    )
    score = calculate_account_usage_score(meta_legacy, now)
    assert score.has_known_quota is True
    assert score.gemini_effective == 75.0
    assert score.effective_quota == 75.0


def test_usage_scoring_reset_urgency() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_soon = (now + timedelta(minutes=15)).isoformat()
    t_late = (now + timedelta(hours=4)).isoformat()

    meta_soon = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(status="known", value=10.0, reset_at=t_soon),
                "weekly": UsageWindow(status="known", value=80.0, reset_at=t_late),
            }
        }
    )
    score_soon = calculate_account_usage_score(meta_soon, now)
    assert score_soon.nearest_reset_seconds == 15 * 60


def test_usage_high_sort_key_order() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    acc_100 = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=100.0), "weekly": UsageWindow(value=100.0)},
            "other": {"short": UsageWindow(value=100.0), "weekly": UsageWindow(value=100.0)},
        }
    )
    acc_50 = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=50.0), "weekly": UsageWindow(value=50.0)},
        }
    )
    acc_0 = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=0.0), "weekly": UsageWindow(value=0.0)},
        }
    )
    acc_unknown = AccountMeta()
    acc_cooldown = AccountMeta(
        status="cooldown",
        usage_families={"gemini": {"short": UsageWindow(value=100.0)}},
    )
    acc_disabled = AccountMeta(enabled=False)
    acc_broken = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=100.0), "weekly": UsageWindow(value=100.0)},
        }
    )
    ver_broken = AccountVerification(problem_status=ProblemStatus.MISSING_AUTH)

    items = [
        ("acc_0", acc_0, None),
        ("acc_disabled", acc_disabled, None),
        ("acc_cooldown", acc_cooldown, None),
        ("acc_50", acc_50, None),
        ("acc_unknown", acc_unknown, None),
        ("acc_broken", acc_broken, ver_broken),
        ("acc_100", acc_100, None),
    ]

    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=True))
    order = [name for name, _, _ in items]

    # In usage-high:
    # 1. 100% capacity account
    # 2. 50% account
    # 3. 0% depleted account
    # 4. Unknown quota account
    # 5. Cooldown account
    # 6. Broken auth account
    # 7. Disabled account
    assert order == [
        "acc_100",
        "acc_50",
        "acc_0",
        "acc_unknown",
        "acc_cooldown",
        "acc_broken",
        "acc_disabled",
    ]


def test_usage_low_squeeze_urgency_order() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_10m = (now + timedelta(minutes=10)).isoformat()
    t_3h = (now + timedelta(hours=3)).isoformat()

    acc_squeeze_soon = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(value=15.0, reset_at=t_10m),
                "weekly": UsageWindow(value=80.0),
            }
        }
    )
    acc_squeeze_late = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(value=15.0, reset_at=t_3h),
                "weekly": UsageWindow(value=80.0),
            }
        }
    )
    acc_depleted = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(value=0.0),
                "weekly": UsageWindow(value=0.0),
            }
        }
    )
    acc_full = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(value=100.0),
                "weekly": UsageWindow(value=100.0),
            }
        }
    )

    items = [
        ("acc_full", acc_full, None),
        ("acc_squeeze_late", acc_squeeze_late, None),
        ("acc_depleted", acc_depleted, None),
        ("acc_squeeze_soon", acc_squeeze_soon, None),
    ]

    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=False))
    order = [name for name, _, _ in items]

    # In usage-low:
    # 1. acc_squeeze_soon (15%, resets in 10m - imminent reset) must be squeezed FIRST
    # 2. acc_squeeze_late (15%, resets in 3h) next
    # 3. acc_full (100%) next
    # 4. acc_depleted (0% exhausted) cannot be squeezed right now -> placed after usable
    assert order == ["acc_squeeze_soon", "acc_squeeze_late", "acc_full", "acc_depleted"]


def test_usage_scoring_edge_cases_empty_or_zero() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    meta_empty = AccountMeta()
    score_empty = calculate_account_usage_score(meta_empty, now)
    assert score_empty.has_known_quota is False
    assert score_empty.effective_quota == 0.0

    meta_zero = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=0.0), "weekly": UsageWindow(value=0.0)},
        }
    )
    score_zero = calculate_account_usage_score(meta_zero, now)
    assert score_zero.has_known_quota is True
    assert score_zero.effective_quota == 0.0
    assert score_zero.max_headroom == 0.0


def test_usage_scoring_expired_or_invalid_reset_timestamps() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_past = (now - timedelta(hours=2)).isoformat()

    # Past reset is due (delta = 0)
    meta_past = AccountMeta(
        usage_families={
            "gemini": {
                "short": UsageWindow(value=10.0, reset_at=t_past),
                "weekly": UsageWindow(value=20.0, reset_at="invalid-date-format"),
            }
        }
    )
    score = calculate_account_usage_score(meta_past, now)
    assert score.nearest_reset_seconds == 0


def test_usage_sorting_identical_usage_name_tie_breaker() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta = AccountMeta(
        usage_families={
            "gemini": {"short": UsageWindow(value=50.0), "weekly": UsageWindow(value=50.0)},
        }
    )

    items = [("zeta", meta, None), ("alpha", meta, None)]

    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=True))
    assert [name for name, _, _ in items] == ["alpha", "zeta"]

    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=False))
    assert [name for name, _, _ in items] == ["alpha", "zeta"]


def test_usage_sorting_synthetic_and_broken_detection() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta_ok = AccountMeta(usage_families={"gemini": {"short": UsageWindow(value=70.0)}})
    meta_synth = AccountMeta(
        health_status=HealthStatus.SYNTHETIC_TOKEN,
        usage_families={"gemini": {"short": UsageWindow(value=100.0)}},
    )
    ver_synth = AccountVerification(problem_status=ProblemStatus.SYNTHETIC_TOKEN)

    items = [("acc_synth", meta_synth, ver_synth), ("acc_ok", meta_ok, None)]
    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=True))
    assert [name for name, _, _ in items] == ["acc_ok", "acc_synth"]


def test_usage_sorting_multiple_exhausted_ranked_by_reset() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_20m = (now + timedelta(minutes=20)).isoformat()
    t_2d = (now + timedelta(days=2)).isoformat()

    acc_depleted_soon = AccountMeta(usage_families={"gemini": {"short": UsageWindow(value=0.0, reset_at=t_20m)}})
    acc_depleted_late = AccountMeta(usage_families={"gemini": {"short": UsageWindow(value=0.0, reset_at=t_2d)}})

    items = [("late", acc_depleted_late, None), ("soon", acc_depleted_soon, None)]
    # In usage-low, among exhausted accounts, the one that resets sooner comes first
    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=False))
    assert [name for name, _, _ in items] == ["soon", "late"]


def test_usage_scoring_top_level_usage_value_and_reset_at() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_soon = (now + timedelta(minutes=25)).isoformat()
    meta_top_level = AccountMeta(
        usage_value=65.0,
        usage_status="known",
        reset_at=t_soon,
    )
    score = calculate_account_usage_score(meta_top_level, now)
    assert score.has_known_quota is True
    assert score.gemini_effective == 65.0
    assert score.effective_quota == 65.0
    assert score.nearest_reset_seconds == 25 * 60


def test_usage_scoring_direct_claude_family_key() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta_claude = AccountMeta(
        usage_families={
            "claude": {
                "short": UsageWindow(value=45.0),
                "weekly": UsageWindow(value=70.0),
            }
        }
    )
    score = calculate_account_usage_score(meta_claude, now)
    assert score.has_known_quota is True
    assert score.claude_effective == 45.0
    assert score.effective_quota == 45.0


def test_usage_sorting_broken_detection_without_verification() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta_healthy = AccountMeta(
        usage_families={"gemini": {"short": UsageWindow(value=80.0), "weekly": UsageWindow(value=80.0)}}
    )
    meta_broken_no_ver = AccountMeta(
        health_status=HealthStatus.AUTH_MISSING,
        usage_families={"gemini": {"short": UsageWindow(value=100.0), "weekly": UsageWindow(value=100.0)}},
    )

    items = [("broken_acc", meta_broken_no_ver, None), ("healthy_acc", meta_healthy, None)]
    # In usage-high, healthy (80%) must rank before broken (even if broken reports 100%)
    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=True))
    assert [name for name, _, _ in items] == ["healthy_acc", "broken_acc"]


def test_usage_sorting_token_mismatch_and_duplicate_ranked_as_broken() -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    meta = AccountMeta(usage_families={"gemini": {"short": UsageWindow(value=90.0), "weekly": UsageWindow(value=90.0)}})
    ver_mismatch = AccountVerification(problem_status=ProblemStatus.TOKEN_MISMATCH)
    ver_duplicate = AccountVerification(problem_status=ProblemStatus.TOKEN_DUPLICATE)
    ver_ok = AccountVerification(problem_status=ProblemStatus.OK)

    items = [
        ("acc_mismatch", meta, ver_mismatch),
        ("acc_ok", meta, ver_ok),
        ("acc_duplicate", meta, ver_duplicate),
    ]
    items.sort(key=lambda item: usage_sort_key(item[0], item[1], item[2], now, descending=True))
    assert items[0][0] == "acc_ok"
    assert {items[1][0], items[2][0]} == {"acc_mismatch", "acc_duplicate"}

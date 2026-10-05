"""Unit tests for passive fleet utilization and financial rightsizing engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from antigravity_cli_switcher.manager.paths import build_paths
from antigravity_cli_switcher.manager.state import load_state, save_state
from antigravity_cli_switcher.manager.utilization import (
    compute_fleet_insight,
    prune_and_rollover_buckets,
    reconcile_active_duty,
    record_quota_refresh,
    set_account_cost,
)
from antigravity_cli_switcher.models import FleetArchetype


def _make_families(
    gw_val: float = 100.0,
    gs_val: float = 100.0,
    ow_val: float = 100.0,
    os_val: float = 100.0,
) -> dict[str, object]:
    return {
        "gemini": {
            "weekly": {"status": "known", "value": gw_val, "reset_at": None},
            "short": {"status": "known", "value": gs_val, "reset_at": None},
        },
        "other": {
            "weekly": {"status": "known", "value": ow_val, "reset_at": None},
            "short": {"status": "known", "value": os_val, "reset_at": None},
        },
    }


def test_record_quota_refresh_normal_delta() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {"accounts": {"test_account_a": {}}}

    # First observation (100% weekly, 100% short)
    record_quota_refresh(state, "test_account_a", _make_families(100.0, 100.0), now=now_dt)
    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    acc = fleet_util["accounts"]["test_account_a"]
    bucket = acc["daily_buckets"][0]
    assert bucket["gemini_weekly_consumed"] == 0.0
    assert bucket["gemini_short_consumed"] == 0.0
    assert bucket["min_gemini_headroom"] == 100.0

    # Second observation (85% weekly, 80% short) -> 15% weekly consumed, 20% short consumed
    now_dt += timedelta(minutes=30)
    record_quota_refresh(state, "test_account_a", _make_families(85.0, 80.0), now=now_dt)
    bucket = acc["daily_buckets"][0]
    assert bucket["gemini_weekly_consumed"] == 15.0
    assert bucket["gemini_short_consumed"] == 20.0
    assert bucket["min_gemini_headroom"] == 80.0

    # Third observation (70% weekly, 60% short) -> 30% weekly consumed, 40% short consumed
    now_dt += timedelta(minutes=30)
    record_quota_refresh(state, "test_account_a", _make_families(70.0, 60.0), now=now_dt)
    bucket = acc["daily_buckets"][0]
    assert bucket["gemini_weekly_consumed"] == 30.0
    assert bucket["gemini_short_consumed"] == 40.0
    assert bucket["min_gemini_headroom"] == 60.0
    assert acc["rolling_7d_gemini_consumed"] == 30.0
    assert acc["rolling_7d_min_gemini_headroom"] == 60.0


def test_record_quota_refresh_weekly_reset() -> None:
    now_dt = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {"accounts": {"test_account_a": {}}}

    record_quota_refresh(state, "test_account_a", _make_families(100.0, 100.0), now=now_dt)
    now_dt += timedelta(hours=2)
    # Drained to 15% weekly
    record_quota_refresh(state, "test_account_a", _make_families(15.0, 15.0), now=now_dt)
    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    acc = fleet_util["accounts"]["test_account_a"]
    bucket = acc["daily_buckets"][0]
    assert bucket["gemini_weekly_consumed"] == 85.0

    # Reset occurs: weekly quota jumps back up from 15% to 100%
    now_dt += timedelta(hours=1)
    record_quota_refresh(state, "test_account_a", _make_families(100.0, 100.0), now=now_dt)
    bucket = acc["daily_buckets"][0]
    # Discontinuity does not erase accumulated consumption
    assert bucket["gemini_weekly_consumed"] == 85.0

    # Subsequent usage from 100% to 90% adds 10%
    now_dt += timedelta(hours=1)
    record_quota_refresh(state, "test_account_a", _make_families(90.0, 90.0), now=now_dt)
    bucket = acc["daily_buckets"][0]
    assert bucket["gemini_weekly_consumed"] == 95.0


def test_reconcile_active_duty_sleep_gap() -> None:
    t0 = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {
        "active": "test_account_a",
        "accounts": {"test_account_a": {}},
        "fleet_utilization": {
            "last_active_account": "test_account_a",
            "last_active_switched_at": t0.isoformat(),
        },
    }

    # 8-hour sleep/suspension gap
    t1 = t0 + timedelta(hours=8)
    reconcile_active_duty(state, now=t1)

    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    acc = fleet_util["accounts"]["test_account_a"]
    bucket = acc["daily_buckets"][0]
    # Capped at 900 seconds (15 minutes)
    assert bucket["active_seconds"] == 900

    # Normal 60-second execution adds exactly 60 seconds
    t2 = t1 + timedelta(seconds=60)
    reconcile_active_duty(state, now=t2)
    assert bucket["active_seconds"] == 960


def test_zombie_detection() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {
        "accounts": {"test_account_zombie": {}, "test_account_active": {}},
    }

    # Record refresh with zero consumption
    record_quota_refresh(state, "test_account_zombie", _make_families(100.0, 100.0), now=now_dt)
    record_quota_refresh(state, "test_account_active", _make_families(100.0, 100.0), now=now_dt)
    now_dt += timedelta(hours=1)
    record_quota_refresh(state, "test_account_active", _make_families(80.0, 80.0), now=now_dt)

    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    zombie_rec = fleet_util["accounts"]["test_account_zombie"]
    active_rec = fleet_util["accounts"]["test_account_active"]

    assert zombie_rec["is_zombie"] is True
    assert active_rec["is_zombie"] is False

    insight = compute_fleet_insight(state, now=now_dt)
    assert "test_account_zombie" in insight.zombie_accounts
    assert "test_account_active" not in insight.zombie_accounts


def test_burst_concurrency_calculation() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {
        "accounts": {
            "test_account_a": {
                "usage_families": _make_families(100.0, 100.0),
            },
            "test_account_b": {
                "usage_families": _make_families(100.0, 100.0),
            },
            "test_account_c": {
                "usage_families": _make_families(100.0, 100.0),
            },
        },
    }

    # Exhaust account A (5% short headroom)
    record_quota_refresh(state, "test_account_a", _make_families(50.0, 5.0), now=now_dt)
    accounts = state["accounts"]
    assert isinstance(accounts, dict)
    acc_a = accounts["test_account_a"]
    assert isinstance(acc_a, dict)
    acc_a["usage_families"] = _make_families(50.0, 5.0)

    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    today_str = now_dt.strftime("%Y-%m-%d")
    burst_map = fleet_util["daily_peak_burst"]
    assert isinstance(burst_map, dict)
    assert burst_map[today_str] == 1

    # Concurrently exhaust account B (8% short headroom)
    now_dt += timedelta(minutes=5)
    record_quota_refresh(state, "test_account_b", _make_families(60.0, 8.0), now=now_dt)
    acc_b = accounts["test_account_b"]
    assert isinstance(acc_b, dict)
    acc_b["usage_families"] = _make_families(60.0, 8.0)
    assert burst_map[today_str] == 2

    # Account C is healthy (80% short headroom)
    now_dt += timedelta(minutes=5)
    record_quota_refresh(state, "test_account_c", _make_families(90.0, 80.0), now=now_dt)
    assert burst_map[today_str] == 2

    insight = compute_fleet_insight(state, now=now_dt)
    assert insight.peak_burst_depth == 2


def test_fleet_archetype_classification() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Ghost Fleet: all accounts are zombies (0% quota used)
    ghost_state: dict[str, object] = {
        "accounts": {"acc1": {}, "acc2": {}},
    }
    record_quota_refresh(ghost_state, "acc1", _make_families(100.0, 100.0), now=now_dt)
    record_quota_refresh(ghost_state, "acc2", _make_families(100.0, 100.0), now=now_dt)
    insight = compute_fleet_insight(ghost_state, now=now_dt)
    assert insight.archetype == FleetArchetype.GHOST_FLEET
    assert insight.recommended_fleet_size == 1

    # 2. Weekend Warrior: active on <= 2 days with no severe exhaustions
    ww_state: dict[str, object] = {
        "accounts": {"acc1": {}},
        "fleet_utilization": {
            "accounts": {
                "acc1": {
                    "daily_buckets": [
                        {
                            "date": "2026-10-04",
                            "gemini_weekly_consumed": 50.0,
                            "active_seconds": 1200,
                            "exhaustion_count": 0,
                            "min_gemini_headroom": 50.0,
                            "min_other_headroom": 100.0,
                        },
                        {
                            "date": "2026-10-05",
                            "gemini_weekly_consumed": 40.0,
                            "active_seconds": 800,
                            "exhaustion_count": 0,
                            "min_gemini_headroom": 60.0,
                            "min_other_headroom": 100.0,
                        },
                    ],
                }
            }
        },
    }
    insight = compute_fleet_insight(ww_state, now=now_dt)
    assert insight.archetype == FleetArchetype.WEEKEND_WARRIOR

    # 3. Quota Grinder: 3+ exhaustions
    qg_state: dict[str, object] = {
        "accounts": {"acc1": {}},
        "fleet_utilization": {
            "accounts": {
                "acc1": {
                    "daily_buckets": [
                        {
                            "date": "2026-10-01",
                            "gemini_weekly_consumed": 20.0,
                            "exhaustion_count": 1,
                            "min_gemini_headroom": 5.0,
                            "min_other_headroom": 100.0,
                        },
                        {
                            "date": "2026-10-02",
                            "gemini_weekly_consumed": 20.0,
                            "exhaustion_count": 1,
                            "min_gemini_headroom": 5.0,
                            "min_other_headroom": 100.0,
                        },
                        {
                            "date": "2026-10-03",
                            "gemini_weekly_consumed": 20.0,
                            "exhaustion_count": 1,
                            "min_gemini_headroom": 5.0,
                            "min_other_headroom": 100.0,
                        },
                    ],
                }
            }
        },
    }
    insight = compute_fleet_insight(qg_state, now=now_dt)
    assert insight.archetype == FleetArchetype.QUOTA_GRINDER

    # 4. Balanced: distributed across > 2 days, moderate headroom, 0 exhaustions
    bal_state: dict[str, object] = {
        "accounts": {"acc1": {}},
        "fleet_utilization": {
            "accounts": {
                "acc1": {
                    "daily_buckets": [
                        {
                            "date": "2026-10-01",
                            "gemini_weekly_consumed": 15.0,
                            "active_seconds": 500,
                            "exhaustion_count": 0,
                            "min_gemini_headroom": 70.0,
                            "min_other_headroom": 100.0,
                        },
                        {
                            "date": "2026-10-02",
                            "gemini_weekly_consumed": 15.0,
                            "active_seconds": 500,
                            "exhaustion_count": 0,
                            "min_gemini_headroom": 70.0,
                            "min_other_headroom": 100.0,
                        },
                        {
                            "date": "2026-10-03",
                            "gemini_weekly_consumed": 15.0,
                            "active_seconds": 500,
                            "exhaustion_count": 0,
                            "min_gemini_headroom": 70.0,
                            "min_other_headroom": 100.0,
                        },
                    ],
                }
            }
        },
    }
    insight = compute_fleet_insight(bal_state, now=now_dt)
    assert insight.archetype == FleetArchetype.BALANCED

    # 5. Starved Standby: 1 account does >= 90% of total volume while standbys are idle zombies
    ss_state: dict[str, object] = {
        "accounts": {"acc1": {}, "acc2": {}},
        "fleet_utilization": {
            "accounts": {
                "acc1": {
                    "rolling_7d_gemini_consumed": 95.0,
                    "rolling_7d_other_consumed": 0.0,
                    "rolling_7d_exhaustions": 0,
                    "rolling_7d_active_seconds": 3600,
                    "is_zombie": False,
                },
                "acc2": {
                    "rolling_7d_gemini_consumed": 0.0,
                    "rolling_7d_other_consumed": 0.0,
                    "rolling_7d_exhaustions": 0,
                    "rolling_7d_active_seconds": 0,
                    "is_zombie": True,
                },
            }
        },
    }
    insight = compute_fleet_insight(ss_state, now=now_dt)
    assert insight.archetype == FleetArchetype.STARVED_STANDBY


def test_prune_and_rollover_buckets() -> None:
    now_dt = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {
        "fleet_utilization": {
            "accounts": {
                "test_account_a": {
                    "daily_buckets": [
                        {"date": "2026-10-01", "gemini_weekly_consumed": 50.0, "active_seconds": 100},  # Older than 7d
                        {"date": "2026-10-05", "gemini_weekly_consumed": 20.0, "active_seconds": 200},  # Retained
                        {"date": "2026-10-10", "gemini_weekly_consumed": 10.0, "active_seconds": 300},  # Retained
                    ]
                }
            },
            "daily_peak_burst": {
                "2026-10-01": 2,  # Older than 7d
                "2026-10-08": 1,  # Retained
            },
        }
    }

    prune_and_rollover_buckets(state, now=now_dt)

    fleet_util = state["fleet_utilization"]
    assert isinstance(fleet_util, dict)
    acc = fleet_util["accounts"]["test_account_a"]
    bucket_dates = [b["date"] for b in acc["daily_buckets"]]
    assert "2026-10-01" not in bucket_dates
    assert bucket_dates == ["2026-10-05", "2026-10-10"]
    assert acc["rolling_7d_gemini_consumed"] == 30.0
    assert acc["rolling_7d_active_seconds"] == 500

    assert "2026-10-01" not in fleet_util["daily_peak_burst"]
    assert fleet_util["daily_peak_burst"]["2026-10-08"] == 1


def test_set_account_cost(tmp_path: object) -> None:
    from pathlib import Path

    paths = build_paths(Path(str(tmp_path)))
    (paths.accounts_dir / "test_account_a").mkdir(parents=True, exist_ok=True)
    state = load_state(paths)
    state["accounts"]["test_account_a"] = {"enabled": True}
    save_state(paths, state)

    updated = set_account_cost(paths, "test_account_a", 25.50)
    assert updated == 25.50

    reloaded = load_state(paths)
    fleet_util = reloaded.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    acc = fleet_util["accounts"]["test_account_a"]
    assert acc["monthly_cost_usd"] == 25.50


def test_set_account_cost_rejects_negative(tmp_path: object) -> None:
    from pathlib import Path

    import pytest

    paths = build_paths(Path(str(tmp_path)))
    with pytest.raises(ValueError, match="non-negative"):
        set_account_cost(paths, "test_account_a", -15.0)


def test_burst_concurrency_ignores_stale_exhaustion() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    past_reset = (now_dt - timedelta(hours=2)).isoformat()
    state: dict[str, object] = {
        "accounts": {
            "test_account_a": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 5.0, "reset_at": past_reset},
                        "weekly": {"value": 50.0},
                    }
                },
                "last_live_check_at": (now_dt - timedelta(days=2)).isoformat(),
            },
            "test_account_b": {
                "usage_families": _make_families(100.0, 100.0),
            },
        },
    }

    # Only test_account_b exhausts right now; test_account_a was exhausted 2 days ago with past reset
    record_quota_refresh(state, "test_account_b", _make_families(50.0, 5.0), now=now_dt)
    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    burst_map = fleet_util["daily_peak_burst"]
    assert isinstance(burst_map, dict)
    today_str = now_dt.strftime("%Y-%m-%d")
    # Peak burst must be 1, NOT 2!
    assert burst_map[today_str] == 1


def test_short_consumption_prevents_false_zombie() -> None:
    now_dt = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {"accounts": {"test_account_short_only": {}}}

    # First observation (100% short, no weekly bucket)
    families_t0: dict[str, object] = {
        "gemini": {"short": {"value": 100.0}},
        "other": {"short": {"value": 100.0}},
    }
    record_quota_refresh(state, "test_account_short_only", families_t0, now=now_dt)

    # Second observation (50% short consumed, weekly still missing)
    now_dt += timedelta(hours=1)
    families_t1: dict[str, object] = {
        "gemini": {"short": {"value": 50.0}},
        "other": {"short": {"value": 100.0}},
    }
    record_quota_refresh(state, "test_account_short_only", families_t1, now=now_dt)

    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    rec = fleet_util["accounts"]["test_account_short_only"]
    # Account actively consumed 50% short quota; must NOT be a zombie!
    assert rec["is_zombie"] is False


def test_compute_fleet_insight_custom_days() -> None:
    now_dt = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    state: dict[str, object] = {
        "accounts": {"test_account_a": {}},
        "fleet_utilization": {
            "accounts": {
                "test_account_a": {
                    "daily_buckets": [
                        {"date": "2026-10-04", "gemini_weekly_consumed": 50.0, "active_seconds": 3600},
                        {"date": "2026-10-09", "gemini_weekly_consumed": 20.0, "active_seconds": 1800},
                    ],
                }
            },
        },
    }

    # Evaluate over 3 days (cutoff 2026-10-07) -> only 2026-10-09 bucket is considered
    insight_3d = compute_fleet_insight(state, days=3, now=now_dt)
    assert insight_3d.total_accounts == 1
    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    rec = fleet_util["accounts"]["test_account_a"]
    assert rec["rolling_7d_gemini_consumed"] == 20.0
    assert rec["rolling_7d_active_seconds"] == 1800


def test_profile_rename_and_delete_cleans_fleet_utilization(tmp_path: object) -> None:
    from pathlib import Path

    from antigravity_cli_switcher.manager.profiles import add_account, delete_account, rename_account

    paths = build_paths(Path(str(tmp_path)))
    src_dir = Path(str(tmp_path)) / "src_profile"
    token_dir = src_dir / ".gemini" / "antigravity-cli"
    token_dir.mkdir(parents=True, exist_ok=True)
    import json

    (token_dir / "antigravity-oauth-token").write_text(
        json.dumps({"token": {"access_token": "ya29.test", "expiry": "2030-01-01T00:00:00Z"}, "auth_method": "oauth"}),
        encoding="utf-8",
    )

    add_account(paths, "orig_name", src_dir)
    set_account_cost(paths, "orig_name", 30.0)

    # Verify initial fleet state
    state = load_state(paths)
    assert "orig_name" in state["fleet_utilization"]["accounts"]

    # Test rename
    rename_account(paths, "orig_name", "renamed_name")
    state = load_state(paths)
    assert "orig_name" not in state["fleet_utilization"]["accounts"]
    assert "renamed_name" in state["fleet_utilization"]["accounts"]
    assert state["fleet_utilization"]["accounts"]["renamed_name"]["monthly_cost_usd"] == 30.0

    # Test delete
    delete_account(paths, "renamed_name")
    state = load_state(paths)
    assert "renamed_name" not in state["fleet_utilization"]["accounts"]

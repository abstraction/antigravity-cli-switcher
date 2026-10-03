"""Tests for the squeeze candidate strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from antigravity_cli_switcher.manager.candidates import _best_switch_candidate
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.models import HealthStatus


@pytest.fixture
def manager_paths(tmp_path) -> ManagerPaths:
    return ManagerPaths(
        root=tmp_path,
        accounts_dir=tmp_path / "accounts",
        state_file=tmp_path / "state.json",
        runtime_dir=tmp_path / "runtime",
        lock_file=tmp_path / "lock",
    )


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Need to mock the utc_now in candidates.py to be stable for tests
    monkeypatch.setattr(
        "antigravity_cli_switcher.manager.candidates.utc_now",
        lambda: datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Mock health derivation so it doesn't check the filesystem
    def mock_health(paths, name, meta):
        return meta.health_status

    monkeypatch.setattr("antigravity_cli_switcher.manager.verification._derive_health_status", mock_health)


def test_squeeze_strategy_picks_lowest_headroom(manager_paths: ManagerPaths) -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    # far future resets so urgency falls back to tier 2 (normal headroom)
    t_far = (now + timedelta(days=5)).isoformat()

    state = {
        "switch_policy": {
            "candidate_strategy": "squeeze",
        },
        "accounts": {
            "depleted": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 20.0, "reset_at": t_far},
                        "weekly": {"value": 50.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            "fresh": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 100.0, "reset_at": t_far},
                        "weekly": {"value": 100.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            "half": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 50.0, "reset_at": t_far},
                        "weekly": {"value": 50.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
        },
    }

    best = _best_switch_candidate(manager_paths, state)
    assert best == "depleted"


def test_squeeze_strategy_picks_imminent_reset(manager_paths: ManagerPaths) -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_imminent = (now + timedelta(minutes=30)).isoformat()
    t_far = (now + timedelta(days=5)).isoformat()

    state = {
        "switch_policy": {
            "candidate_strategy": "squeeze",
        },
        "accounts": {
            "depleted_no_reset": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 10.0, "reset_at": t_far},
                        "weekly": {"value": 50.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            "half_imminent_reset": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 50.0, "reset_at": t_imminent},
                        "weekly": {"value": 50.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
        },
    }

    best = _best_switch_candidate(manager_paths, state)
    # The half account has an imminent reset, so it should jump to urgency tier 0
    assert best == "half_imminent_reset"


def test_squeeze_strategy_picks_weekly_waste(manager_paths: ManagerPaths) -> None:
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_weekly_waste = (now + timedelta(hours=20)).isoformat()
    t_far = (now + timedelta(days=5)).isoformat()

    state = {
        "switch_policy": {
            "candidate_strategy": "squeeze",
            "weekly_waste_deadline_hours": 48.0,
            "weekly_waste_threshold_percent": 70.0,
        },
        "accounts": {
            "depleted_no_reset": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 20.0, "reset_at": t_far},
                        "weekly": {"value": 20.0, "reset_at": t_far},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            "fresh_weekly_waste": {
                "usage_families": {
                    "gemini": {
                        "short": {"value": 100.0, "reset_at": t_far},
                        "weekly": {"value": 100.0, "reset_at": t_weekly_waste},
                    }
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
        },
    }

    best = _best_switch_candidate(manager_paths, state)
    # fresh_weekly_waste has 100% weekly resetting in 20 hours (tier 1), beating depleted_no_reset (tier 2)
    assert best == "fresh_weekly_waste"

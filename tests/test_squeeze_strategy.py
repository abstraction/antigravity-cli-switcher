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


def test_squeeze_does_not_bounce_between_exhausted_accounts(manager_paths: ManagerPaths) -> None:
    """Regression: squeeze must skip fully-exhausted accounts (0% short, status=known).

    Before the fix, exhausted accounts scored primary=0.0 and always beat healthy
    accounts (primary=33%+), causing r to bounce between the two 0% accounts
    instead of switching to a healthy one.
    """
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_far = (now + timedelta(days=5)).isoformat()
    t_short_reset = (now + timedelta(hours=1)).isoformat()

    def _known_window(value: float, reset_at: str) -> dict:
        return {"value": value, "status": "known", "reset_at": reset_at}

    state = {
        "switch_policy": {"candidate_strategy": "squeeze"},
        "accounts": {
            # Two exhausted accounts (0% short remaining, status=known) — the bounce pair.
            "exhausted_a": {
                "usage_families": {
                    "gemini": {
                        "short": _known_window(0.0, t_short_reset),
                        "weekly": _known_window(51.0, t_far),
                    },
                    "claude": {
                        "short": _known_window(0.0, t_short_reset),
                        "weekly": _known_window(50.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            "exhausted_b": {
                "usage_families": {
                    "gemini": {
                        "short": _known_window(0.0, t_short_reset),
                        "weekly": _known_window(52.0, t_far),
                    },
                    "claude": {
                        "short": _known_window(0.0, t_short_reset),
                        "weekly": _known_window(49.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            # Healthy account with real remaining quota — should always win.
            "healthy": {
                "usage_families": {
                    "gemini": {
                        "short": _known_window(56.0, t_far),
                        "weekly": _known_window(73.0, t_far),
                    },
                    "claude": {
                        "short": _known_window(33.0, t_far),
                        "weekly": _known_window(66.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
        },
    }

    # From exhausted_a's perspective — must pick "healthy", not "exhausted_b".
    best = _best_switch_candidate(manager_paths, state, exclude="exhausted_a")
    assert best == "healthy", f"Expected healthy, got {best!r}"

    # From exhausted_b's perspective — must pick "healthy", not "exhausted_a".
    best = _best_switch_candidate(manager_paths, state, exclude="exhausted_b")
    assert best == "healthy", f"Expected healthy, got {best!r}"


def test_squeeze_does_not_prioritize_imminent_reset_when_exhausted(manager_paths: ManagerPaths) -> None:
    """Imminent reset (<=2h) must NOT give urgency tier 0 to an exhausted/quota_low account."""
    now = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
    t_far = (now + timedelta(days=5)).isoformat()
    t_imminent = (now + timedelta(minutes=30)).isoformat()

    def _window(value: float, reset_at: str) -> dict:
        return {"value": value, "status": "known", "reset_at": reset_at}

    state = {
        "switch_policy": {"candidate_strategy": "squeeze"},
        "accounts": {
            # Exhausted account resetting very soon (30 min)
            "depleted_imminent": {
                "usage_families": {
                    "gemini": {
                        "short": _window(0.0, t_imminent),
                        "weekly": _window(50.0, t_far),
                    },
                    "claude": {
                        "short": _window(0.0, t_imminent),
                        "weekly": _window(0.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            # Partially exhausted account (one family 3%, other 35%)
            "partially_exhausted": {
                "usage_families": {
                    "gemini": {
                        "short": _window(3.0, t_far),
                        "weekly": _window(59.0, t_far),
                    },
                    "claude": {
                        "short": _window(35.0, t_far),
                        "weekly": _window(14.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
            # Healthy account with plenty of quota
            "healthy_account": {
                "usage_families": {
                    "gemini": {
                        "short": _window(80.0, t_far),
                        "weekly": _window(80.0, t_far),
                    },
                    "claude": {
                        "short": _window(80.0, t_far),
                        "weekly": _window(80.0, t_far),
                    },
                },
                "health_status": HealthStatus.HEALTHY,
                "status": "standby",
            },
        },
    }

    # When switching away from partially_exhausted, healthy_account must be chosen over depleted_imminent
    best = _best_switch_candidate(manager_paths, state, exclude="partially_exhausted")
    assert best == "healthy_account", f"Expected healthy_account, got {best!r}"

    # When switching away from depleted_imminent, healthy_account must be chosen over partially_exhausted
    best = _best_switch_candidate(manager_paths, state, exclude="depleted_imminent")
    assert best == "healthy_account", f"Expected healthy_account, got {best!r}"

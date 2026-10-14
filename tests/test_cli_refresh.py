from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from antigravity_cli_switcher.cli import main
from antigravity_cli_switcher.cli.formatters import _format_next_refresh
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.manager import (
    account_dir,
    build_paths,
    ensure_layout,
    load_state,
    pick_due_refresh_account,
    refresh_due_account,
    save_state,
)
from antigravity_cli_switcher.manager.quota import UsageRefreshResult


@pytest.fixture
def mock_root(tmp_path: Path) -> Path:
    paths = build_paths(tmp_path)
    ensure_layout(paths)

    acct1 = account_dir(paths, "alpha")
    token_dir1 = acct1 / ".gemini" / "antigravity-cli"
    token_dir1.mkdir(parents=True, exist_ok=True)
    (token_dir1 / "antigravity-oauth-token").write_text(
        json.dumps(
            {
                "token": {"access_token": "ya29.fake1", "refresh_token": "1//fake1", "expiry": "2030-01-01T00:00:00Z"},
                "auth_method": "oauth",
            }
        ),
        encoding="utf-8",
    )

    acct2 = account_dir(paths, "[2] beta")
    token_dir2 = acct2 / ".gemini" / "antigravity-cli"
    token_dir2.mkdir(parents=True, exist_ok=True)
    (token_dir2 / "antigravity-oauth-token").write_text(
        json.dumps(
            {
                "token": {"access_token": "ya29.fake2", "refresh_token": "1//fake2", "expiry": "2030-01-01T00:00:00Z"},
                "auth_method": "oauth",
            }
        ),
        encoding="utf-8",
    )

    state = load_state(paths)
    state["active"] = "alpha"
    state["accounts"]["alpha"] = {
        "enabled": True,
        "status": "active",
        "plan_type": "pro",
        "expected_email": "alpha@example.com",
    }
    state["accounts"]["[2] beta"] = {
        "enabled": True,
        "status": "standby",
        "plan_type": "free",
        "expected_email": "beta@example.com",
    }
    save_state(paths, state)
    return tmp_path


def _make_dummy_usage(paths: Path, name: str, backend: str | None = None) -> UsageRefreshResult:
    return UsageRefreshResult(
        account=name,
        source_home=str(paths),
        project_id=None,
        plan_type="free",
        prompt_credits_available=None,
        prompt_credits_monthly=None,
        short_usage_status="known",
        short_usage_value=85.0,
        short_reset_at=None,
        weekly_usage_status="known",
        weekly_usage_value=80.0,
        weekly_reset_at=None,
        usage_families={},
        bucket_count=0,
        backend=backend or "http",
    )


def test_cli_refresh_due_no_governor_deadlock(
    mock_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refreshed: list[str] = []

    def mock_refresh_account_usage(paths, name, agy_binary=None, timeout_seconds=30, backend=None):
        refreshed.append(name)
        return _make_dummy_usage(paths.root, name, backend)

    monkeypatch.setattr("antigravity_cli_switcher.manager.quota.refresh_account_usage", mock_refresh_account_usage)

    paths = build_paths(mock_root)
    state = load_state(paths)
    state["accounts"]["alpha"]["next_live_check_at"] = "2020-01-01T00:00:00+00:00"
    state["accounts"]["[2] beta"]["next_live_check_at"] = "2030-01-01T00:00:00+00:00"
    save_state(paths, state)

    # First run should pick and refresh alpha without double-pick deadlock
    code1 = main(["--root", str(mock_root), "refresh-due", "--json"])
    assert code1 == 0
    out1 = capsys.readouterr().out
    data1 = json.loads(out1)
    assert data1["refreshed"] is True
    assert data1["account"] == "alpha"
    assert len(refreshed) == 1

    # Second consecutive run immediately afterwards is throttled by the 35s governor
    code2 = main(["--root", str(mock_root), "refresh-due", "--json"])
    assert code2 == 0
    out2 = capsys.readouterr().out
    data2 = json.loads(out2)
    assert data2["refreshed"] is False
    assert data2["reason"] == "no_due_account"
    assert len(refreshed) == 1


def test_cli_refresh_all_delay_seconds_parity() -> None:
    parser = build_parser()
    args_default = parser.parse_args(["refresh-all"])
    assert args_default.delay_seconds == 35.0

    args_custom = parser.parse_args(["refresh-all", "--delay-seconds", "5.0"])
    assert args_custom.delay_seconds == 5.0


def test_refresh_due_account_with_explicit_target_updates_pacing_state(
    mock_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(mock_root)
    monkeypatch.setattr(
        "antigravity_cli_switcher.manager.quota.refresh_account_usage",
        lambda p, name, **kwargs: _make_dummy_usage(p.root, name),
    )

    state = load_state(paths)
    assert state.get("last_background_refresh_at") is None

    res = refresh_due_account(paths, target="alpha")
    assert res is not None
    assert res.account == "alpha"

    state_after = load_state(paths)
    assert state_after.get("last_background_refresh_at") is not None


def test_pick_due_refresh_account_clock_skew_resilience(mock_root: Path) -> None:
    paths = build_paths(mock_root)
    state = load_state(paths)
    state["accounts"]["alpha"]["next_live_check_at"] = "2020-01-01T00:00:00+00:00"

    # Forward clock jump of 10 minutes in the past should not cause permanent deadlock
    state["last_background_refresh_at"] = "2030-01-01T00:00:00+00:00"
    save_state(paths, state)

    # When last_background_refresh_at is far in the future (>35s), it should not deadlock
    candidate = pick_due_refresh_account(paths)
    assert candidate == "alpha"


def test_cli_format_next_refresh_exhausted_sleeping() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    meta = {
        "enabled": True,
        "status": "standby",
        "last_live_check_at": "2026-01-01T11:50:00+00:00",
        "refresh_policy_seconds": 300,
        "usage_windows": {
            "short": {
                "value": 0.0,
                "reset_at": "2026-01-01T13:00:00+00:00",
            }
        },
    }
    # Should display duration until reset ("1h"), NOT "due"
    assert _format_next_refresh(meta, now) == "1h"

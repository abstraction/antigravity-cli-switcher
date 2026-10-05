"""CLI tests for acs fleet and acs set-cost commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from antigravity_cli_switcher.cli import main
from antigravity_cli_switcher.manager import (
    account_dir,
    build_paths,
    ensure_layout,
    load_state,
    save_state,
)


@pytest.fixture
def mock_fleet_root(tmp_path: Path) -> Path:
    paths = build_paths(tmp_path)
    ensure_layout(paths)

    for name in ("alpha", "beta"):
        acct = account_dir(paths, name)
        token_dir = acct / ".gemini" / "antigravity-cli"
        token_dir.mkdir(parents=True, exist_ok=True)
        (token_dir / "antigravity-oauth-token").write_text(
            json.dumps(
                {
                    "token": {"access_token": f"ya29.test_{name}", "expiry": "2030-01-01T00:00:00Z"},
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
    }
    state["accounts"]["beta"] = {
        "enabled": True,
        "status": "standby",
        "plan_type": "pro",
    }
    state["fleet_utilization"] = {
        "accounts": {
            "alpha": {
                "rolling_7d_active_seconds": 3600,
                "rolling_7d_exhaustions": 0,
                "rolling_7d_gemini_consumed": 45.0,
                "rolling_7d_other_consumed": 20.0,
                "rolling_7d_min_gemini_headroom": 55.0,
                "rolling_7d_min_other_headroom": 80.0,
                "is_zombie": False,
                "monthly_cost_usd": 20.0,
            },
            "beta": {
                "rolling_7d_active_seconds": 0,
                "rolling_7d_exhaustions": 0,
                "rolling_7d_gemini_consumed": 0.0,
                "rolling_7d_other_consumed": 0.0,
                "rolling_7d_min_gemini_headroom": 100.0,
                "rolling_7d_min_other_headroom": 100.0,
                "is_zombie": True,
                "monthly_cost_usd": 20.0,
            },
        },
        "daily_peak_burst": {"2026-10-06": 1},
    }
    save_state(paths, state)
    return tmp_path


def test_cli_fleet_text(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "fleet"])
    assert code == 0
    captured = capsys.readouterr()
    assert "=== Fleet Utilization & Rightsizing ===" in captured.out
    assert "Spend: $40/mo" in captured.out
    assert "Waste: $20/mo" in captured.out
    assert "alpha" in captured.out
    assert "beta" in captured.out
    assert "YES" in captured.out  # beta is zombie


def test_cli_fleet_json(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "fleet", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["total_accounts"] == 2
    assert "beta" in payload["zombie_accounts"]
    assert payload["estimated_monthly_spend_usd"] == 40.0
    assert payload["estimated_monthly_waste_usd"] == 20.0
    assert "accounts" in payload
    assert "alpha" in payload["accounts"]


def test_cli_set_cost_text(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "set-cost", "alpha", "35.00"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Set monthly cost for 'alpha' to $35.00/mo." in captured.out

    paths = build_paths(mock_fleet_root)
    state = load_state(paths)
    fleet_util = state.get("fleet_utilization", {})
    assert isinstance(fleet_util, dict)
    assert fleet_util["accounts"]["alpha"]["monthly_cost_usd"] == 35.0


def test_cli_set_cost_json(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "set-cost", "alpha", "40.00", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["account"] == "alpha"
    assert payload["monthly_cost_usd"] == 40.0


def test_headless_fleet_does_not_import_textual(mock_fleet_root: Path) -> None:
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("textual")}
    for k in saved_modules:
        sys.modules.pop(k, None)

    try:
        main(["--root", str(mock_fleet_root), "fleet"])
        loaded_textual = [k for k in sys.modules if k.startswith("textual")]
        assert loaded_textual == [], f"Textual unexpectedly imported by acs fleet: {loaded_textual}"
    finally:
        sys.modules.update(saved_modules)


def test_cli_fleet_days_argument(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "fleet", "--days", "3"])
    assert code == 0
    captured = capsys.readouterr()
    assert "3D DUTY" in captured.out


def test_cli_fleet_invalid_days(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "fleet", "--days", "0"])
    assert code == 1
    captured = capsys.readouterr()
    assert "error: --days must be at least 1" in captured.err


def test_cli_set_cost_rejects_negative(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "set-cost", "alpha", "-5.0"])
    assert code == 1
    captured = capsys.readouterr()
    assert "error: monthly cost must be non-negative" in captured.err


def test_cli_set_cost_unknown_account(mock_fleet_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_fleet_root), "set-cost", "nonexistent_account", "25.0"])
    assert code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err

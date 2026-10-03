"""Integration tests for Textual TUI account sorting across widgets and screens."""

from __future__ import annotations

from pathlib import Path

import pytest

from antigravity_cli_switcher.manager import ManagerPaths, ensure_layout
from antigravity_cli_switcher.models import (
    AccountMeta,
    SnapshotVerification,
    StatusSnapshot,
    UsageWindow,
)
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.screens.dashboard import DashboardScreen
from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar


@pytest.fixture
def temp_paths(tmp_path: Path) -> ManagerPaths:
    root = tmp_path / "acs_manager"
    root.mkdir()
    paths = ManagerPaths(
        root=root,
        accounts_dir=root / "accounts",
        state_file=root / "state.json",
        runtime_dir=root / "runtime",
        lock_file=root / "lock",
    )
    ensure_layout(paths)
    return paths


@pytest.mark.asyncio
async def test_account_table_sort_modes() -> None:
    table = AccountTable()

    acc_high = AccountMeta(
        enabled=True,
        status="standby",
        usage_families={"gemini": {"short": UsageWindow(value=95.0), "weekly": UsageWindow(value=95.0)}},
    )
    acc_low = AccountMeta(
        enabled=True,
        status="standby",
        usage_families={"gemini": {"short": UsageWindow(value=10.0), "weekly": UsageWindow(value=50.0)}},
    )
    acc_mid = AccountMeta(
        enabled=True,
        status="active",
        usage_families={"gemini": {"short": UsageWindow(value=50.0), "weekly": UsageWindow(value=60.0)}},
    )

    snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="acc_mid",
        accounts={"acc_high": acc_high, "acc_low": acc_low, "acc_mid": acc_mid},
    )
    verification = SnapshotVerification(active="acc_mid")

    class TableApp(ACSApp):
        def compose(self):
            yield table

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TableApp(paths=paths)
    async with app.run_test():
        # Test usage-low: lowest remaining quota first
        table.update_accounts(snapshot, verification, sort_mode="usage-low")
        assert table.account_order == ["acc_low", "acc_mid", "acc_high"]

        # Test usage-high: highest remaining quota first
        table.update_accounts(snapshot, verification, sort_mode="usage-high")
        assert table.account_order == ["acc_high", "acc_mid", "acc_low"]


@pytest.mark.asyncio
async def test_dashboard_cycle_sort_through_usage_modes(
    temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = StatusSnapshot(
        root=str(temp_paths.root),
        runtime_dir=str(temp_paths.runtime_dir),
        lock_file=str(temp_paths.lock_file),
        active="alpha",
        accounts={
            "alpha": AccountMeta(
                usage_families={"gemini": {"short": UsageWindow(value=20.0), "weekly": UsageWindow(value=40.0)}}
            ),
            "beta": AccountMeta(
                usage_families={"gemini": {"short": UsageWindow(value=90.0), "weekly": UsageWindow(value=90.0)}}
            ),
        },
    )
    verification = SnapshotVerification(active="alpha")

    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )

    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        header = screen.query_one(HeaderBar)
        table = screen.query_one(AccountTable)

        # Initial sort mode is "usage-low"
        assert screen.sort_mode == "usage-low"
        assert header.sort_mode == "usage-low"
        assert table.account_order == ["alpha", "beta"]

        # 1st press 's' -> "usage-high"
        await pilot.press("s")
        assert screen.sort_mode == "usage-high"
        assert header.sort_mode == "usage-high"
        assert table.account_order == ["beta", "alpha"]

        # 2nd press 's' -> "name"
        await pilot.press("s")
        assert screen.sort_mode == "name"
        assert header.sort_mode == "name"

        # 3rd press 's' -> "state"
        await pilot.press("s")
        assert screen.sort_mode == "state"
        assert header.sort_mode == "state"

        # 4th press 's' -> "health"
        await pilot.press("s")
        assert screen.sort_mode == "health"
        assert header.sort_mode == "health"

        # 5th press 's' -> cycles back to "usage-low"
        await pilot.press("s")
        assert screen.sort_mode == "usage-low"
        assert header.sort_mode == "usage-low"
        assert table.account_order == ["alpha", "beta"]

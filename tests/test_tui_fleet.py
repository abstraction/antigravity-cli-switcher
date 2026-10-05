"""Tests for FleetTab widget component and utilization rightsizing presentation."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Static

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountUtilizationRecord,
    DailyUsageBucket,
    FleetUtilizationState,
    StatusSnapshot,
)
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.widgets.fleet_tab import FleetTab


def create_sample_fleet_snapshot() -> StatusSnapshot:
    accounts = {
        "alpha": AccountMeta(enabled=True, status="active", plan_type="pro"),
        "beta": AccountMeta(enabled=True, status="standby", plan_type="free"),
        "gamma": AccountMeta(enabled=False, status="disabled", plan_type="pro"),
    }
    fleet_util = FleetUtilizationState(
        daily_peak_burst={"2026-10-01": 2, "2026-10-02": 3},
        accounts={
            "alpha": AccountUtilizationRecord(
                rolling_7d_active_seconds=86400,
                rolling_7d_gemini_consumed=60.0,
                rolling_7d_other_consumed=30.0,
                rolling_7d_min_gemini_headroom=40.0,
                rolling_7d_min_other_headroom=70.0,
                is_zombie=False,
                monthly_cost_usd=20.0,
                daily_buckets=[
                    DailyUsageBucket(
                        date="2026-10-01",
                        active_seconds=43200,
                        gemini_weekly_consumed=30.0,
                    ),
                    DailyUsageBucket(
                        date="2026-10-02",
                        active_seconds=43200,
                        gemini_weekly_consumed=30.0,
                    ),
                ],
            ),
            "beta": AccountUtilizationRecord(
                rolling_7d_active_seconds=0,
                rolling_7d_gemini_consumed=0.0,
                rolling_7d_other_consumed=0.0,
                rolling_7d_min_gemini_headroom=100.0,
                rolling_7d_min_other_headroom=100.0,
                is_zombie=True,
                monthly_cost_usd=20.0,
            ),
            "gamma": AccountUtilizationRecord(
                rolling_7d_active_seconds=0,
                rolling_7d_gemini_consumed=0.0,
                rolling_7d_other_consumed=0.0,
                rolling_7d_min_gemini_headroom=100.0,
                rolling_7d_min_other_headroom=100.0,
                is_zombie=True,
                monthly_cost_usd=20.0,
            ),
        },
    )
    return StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="alpha",
        switch_mode="manual",
        accounts=accounts,
        fleet_utilization=fleet_util,
    )


@pytest.mark.asyncio
async def test_fleet_tab_compose_and_columns() -> None:
    tab = FleetTab()

    class TabApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TabApp(paths=paths)
    async with app.run_test():
        summary = tab.query_one("#fleet-summary", Static)
        assert summary is not None

        rec = tab.query_one("#fleet-recommendation", Static)
        assert rec is not None

        table = tab.query_one("#fleet-table", DataTable)
        col_keys = [str(col.label) for col in table.columns.values()]
        assert "Account" in col_keys
        assert "Status" in col_keys
        assert "7D Duty %" in col_keys
        assert "Gemini Burnt %" in col_keys
        assert "Claude Burnt %" in col_keys
        assert "Min Headroom %" in col_keys
        assert "Monthly Cost" in col_keys
        assert "Zombie Flag" in col_keys


@pytest.mark.asyncio
async def test_fleet_tab_populate_and_summary_cards() -> None:
    tab = FleetTab()
    snapshot = create_sample_fleet_snapshot()

    class TabApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TabApp(paths=paths)
    async with app.run_test():
        tab.update_fleet(snapshot)

        # 1. Header / Summary cards
        summary = tab.query_one("#fleet-summary", Static)
        s_rendered = str(summary.content)
        assert "Archetype:" in s_rendered
        assert "Spend:" in s_rendered
        assert "Waste:" in s_rendered
        assert "Peak Burst:" in s_rendered
        assert "Recommended Size:" in s_rendered

        # 2. Recommendations panel
        recommendation = tab.query_one("#fleet-recommendation", Static)
        r_rendered = str(recommendation.content)
        assert "Recommendation:" in r_rendered

        # 3. Table content
        table = tab.query_one("#fleet-table", DataTable)
        assert table.row_count == 3
        assert tab.account_order == ["alpha", "beta", "gamma"]

        # Check alpha row
        alpha_row = [str(cell) for cell in table.get_row("alpha")]
        assert any("alpha" in c for c in alpha_row)
        assert any("★" in c for c in alpha_row)
        assert any("active" in c for c in alpha_row)
        assert any("NO" in c for c in alpha_row)  # alpha is not a zombie

        # Check beta row (zombie)
        beta_row = [str(cell) for cell in table.get_row("beta")]
        assert any("beta" in c for c in beta_row)
        assert any("ZOMBIE" in c for c in beta_row)

        # Selection helper
        assert tab.get_selected_account_name() == "alpha"


@pytest.mark.asyncio
async def test_fleet_tab_in_place_update() -> None:
    tab = FleetTab()
    snapshot = create_sample_fleet_snapshot()

    class TabApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TabApp(paths=paths)
    async with app.run_test():
        tab.update_fleet(snapshot)
        table = tab.query_one("#fleet-table", DataTable)
        assert table.row_count == 3

        # Update in-place with modified cost
        snapshot.fleet_utilization.accounts["alpha"].monthly_cost_usd = 30.0
        tab.update_fleet(snapshot)

        assert table.row_count == 3
        alpha_row = [str(cell) for cell in table.get_row("alpha")]
        assert any("$30/mo" in c for c in alpha_row)


@pytest.mark.asyncio
async def test_fleet_tab_empty_snapshot() -> None:
    tab = FleetTab()
    empty_snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active=None,
        switch_mode="manual",
        accounts={},
        fleet_utilization=FleetUtilizationState(),
    )

    class TabApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TabApp(paths=paths)
    async with app.run_test():
        tab.update_fleet(empty_snapshot)
        table = tab.query_one("#fleet-table", DataTable)
        assert table.row_count == 0
        assert tab.get_selected_account_name() is None


@pytest.mark.asyncio
async def test_fleet_tab_uncalculated_zombie_and_missing_record() -> None:
    tab = FleetTab()
    accounts = {
        "alpha": AccountMeta(enabled=True, status="standby", plan_type="pro"),
        "beta": AccountMeta(enabled=True, status="standby", plan_type="free"),
        "delta": AccountMeta(enabled=True, status="standby", plan_type="pro"),
    }
    # Notice: beta has is_zombie=False (uncalculated default), and delta is missing from fleet_utilization
    fleet_util = FleetUtilizationState(
        daily_peak_burst={"2026-10-01": 1},
        accounts={
            "alpha": AccountUtilizationRecord(
                rolling_7d_active_seconds=3600,
                rolling_7d_gemini_consumed=20.0,
                rolling_7d_other_consumed=10.0,
                is_zombie=False,
            ),
            "beta": AccountUtilizationRecord(
                rolling_7d_active_seconds=0,
                rolling_7d_gemini_consumed=0.0,
                rolling_7d_other_consumed=0.0,
                is_zombie=False,
            ),
        },
    )
    snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="alpha",
        switch_mode="manual",
        accounts=accounts,
        fleet_utilization=fleet_util,
    )

    class TabApp(ACSApp):
        def compose(self):
            yield tab

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = TabApp(paths=paths)
    async with app.run_test():
        tab.update_fleet(snapshot)
        table = tab.query_one("#fleet-table", DataTable)
        assert table.row_count == 3
        assert tab.account_order == ["alpha", "beta", "delta"]

        # 1. alpha is active, so status must display active even if meta.status was standby
        alpha_row = [str(cell) for cell in table.get_row("alpha")]
        assert any("active" in c for c in alpha_row)
        assert any("★" in c for c in alpha_row)

        # 2. beta has 0 usage, so it must be calculated and displayed as ZOMBIE
        beta_row = [str(cell) for cell in table.get_row("beta")]
        assert any("ZOMBIE" in c for c in beta_row)

        # 3. delta was missing from fleet_utilization.accounts, but is now present and classified
        delta_row = [str(cell) for cell in table.get_row("delta")]
        assert any("delta" in c for c in delta_row)
        assert any("ZOMBIE" in c for c in delta_row)


@pytest.mark.asyncio
async def test_fleet_tab_enter_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_cli_switcher.models import SnapshotVerification
    from antigravity_cli_switcher.tui.screens.dashboard import DashboardScreen

    snapshot = create_sample_fleet_snapshot()
    verification = SnapshotVerification()
    switched_to: list[str] = []

    def mock_switch(paths: ManagerPaths, name: str) -> str:
        switched_to.append(name)
        return "alpha"

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    monkeypatch.setattr("antigravity_cli_switcher.tui.app.fetch_snapshot_data", lambda p: (snapshot, verification))
    monkeypatch.setattr("antigravity_cli_switcher.tui.screens.dashboard.switch_account", mock_switch)

    app = ACSApp(paths=paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        # Switch to fleet tab
        await pilot.press("2")
        await pilot.pause()

        # Focus fleet table and press enter on the highlighted row
        table = screen.query_one("#fleet-table", DataTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()

        assert len(switched_to) == 1
        assert switched_to[0] == "alpha"

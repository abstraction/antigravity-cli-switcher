"""Tests for the ACS Textual TUI dashboard and components."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Footer, Input, TabbedContent

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    ProblemStatus,
    ProxyConfig,
    SnapshotVerification,
    StatusSnapshot,
    SwitchHistoryEntry,
)
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.modals.email import EmailModal
from antigravity_cli_switcher.tui.modals.login import LoginModal
from antigravity_cli_switcher.tui.modals.policy import PolicyModal
from antigravity_cli_switcher.tui.screens.dashboard import DashboardScreen
from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.hygiene_tab import HygieneTab


@pytest.fixture
def temp_paths(tmp_path: Path) -> ManagerPaths:
    root = tmp_path
    paths = ManagerPaths(
        root=root,
        accounts_dir=root / "accounts",
        state_file=root / "state.json",
        runtime_dir=root / "runtime",
        lock_file=root / ".manager.lock",
    )
    paths.accounts_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "manager.log").write_text(
        "2026-10-02 12:00:00 [INFO] antigravity_cli_switcher: initialized\n"
        "2026-10-02 12:01:00 [WARNING] antigravity_cli_switcher: high quota\n"
        "2026-10-02 12:02:00 [ERROR] antigravity_cli_switcher: failed to connect\n",
        encoding="utf-8",
    )
    return paths


def create_sample_snapshot() -> tuple[StatusSnapshot, SnapshotVerification]:
    snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="alpha",
        switch_mode="manual",
        switch_history=[
            SwitchHistoryEntry(
                at="2026-10-02 12:00:00",
                previous_active="beta",
                active="alpha",
                switched_to="alpha",
                outcome="success",
                trigger="manual",
                reason="User switch",
            ),
        ],
        accounts={
            "alpha": AccountMeta(
                enabled=True,
                status="active",
                plan_type="pro",
                fail_count=0,
                proxy=ProxyConfig(enabled=True, url="http://127.0.0.1:8080", label="Local"),
            ),
            "beta": AccountMeta(
                enabled=False,
                status="standby",
                plan_type="free",
                fail_count=1,
                proxy=ProxyConfig(enabled=False, url="http://192.168.1.1:1080", label="VPN"),
            ),
            "gamma": AccountMeta(
                enabled=True,
                status="standby",
                plan_type="pro",
                fail_count=0,
                proxy=ProxyConfig(enabled=False),
            ),
        },
    )
    verification = SnapshotVerification(
        active="alpha",
        switch_mode="manual",
        accounts={
            "alpha": AccountVerification(problem_status=ProblemStatus.OK, summary="Ready"),
            "beta": AccountVerification(problem_status=ProblemStatus.DISABLED, summary="Disabled"),
            "gamma": AccountVerification(
                problem_status=ProblemStatus.SYNTHETIC_TOKEN,
                summary="Synthetic test token found",
                is_synthetic=True,
                recommended_action="fix",
            ),
        },
    )
    return snapshot, verification


@pytest.mark.asyncio
async def test_dashboard_screen_pilot(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        # Check table
        table = screen.query_one(AccountTable)
        assert table.row_count == 3

        # Test tab switching via number bindings
        tabs = screen.query_one("#main-tabs", TabbedContent)
        assert tabs.active == "tab-accounts"

        # Account table has initial focus automatically
        assert table.has_focus is True

        # Test tab switching via number bindings
        tabs = screen.query_one("#main-tabs", TabbedContent)
        assert tabs.active == "tab-accounts"

        await pilot.press("2")
        assert tabs.active == "tab-fleet"

        await pilot.press("3")
        assert tabs.active == "tab-logs"

        await pilot.press("4")
        assert tabs.active == "tab-history"

        await pilot.press("5")
        assert tabs.active == "tab-hygiene"

        await pilot.press("6")
        assert tabs.active == "tab-proxies"

        # Test tab navigation with [ and ]
        await pilot.press("]")
        assert tabs.active == "tab-accounts"

        await pilot.press("[")
        assert tabs.active == "tab-proxies"

        # Test tab navigation with Left and Right arrow keys
        await pilot.press("right")
        assert tabs.active == "tab-accounts"

        await pilot.press("right")
        assert tabs.active == "tab-fleet"

        await pilot.press("left")
        assert tabs.active == "tab-accounts"

        # Switch back to accounts via number binding
        await pilot.press("1")
        assert tabs.active == "tab-accounts"

        # Test sort cycle binding 's'
        assert screen.sort_mode == "usage-low"
        await pilot.press("s")
        assert screen.sort_mode == "usage-high"

        # Test manual refresh data binding 't'
        await pilot.press("t")

        # Test refresh interval cycle binding 'T'
        await pilot.press("T")
        assert isinstance(pilot.app, ACSApp)
        assert pilot.app.refresh_interval_seconds == 10

        # Test quit binding 'q'
        await pilot.press("q")


@pytest.mark.asyncio
async def test_dashboard_initial_tab(temp_paths: ManagerPaths) -> None:
    app = ACSApp(paths=temp_paths, initial_tab="tab-proxies")
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        tabs = screen.query_one("#main-tabs", TabbedContent)
        assert tabs.active == "tab-proxies"


@pytest.mark.asyncio
async def test_dashboard_arrow_navigation_and_tab_switching(
    temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        table = screen.query_one("#account-table", AccountTable)
        tabs = screen.query_one("#main-tabs", TabbedContent)

        # Initial mount focuses the account table
        assert screen.focused == table

        # Table navigation with up/down arrows
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()

        # Tab navigation with right arrow (Accounts -> Fleet -> Logs -> History -> Hygiene -> Proxies)
        assert tabs.active == "tab-accounts"
        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-fleet"

        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-logs"

        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-history"

        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-hygiene"

        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-proxies"

        # Tab navigation with left arrow (Proxies -> Hygiene)
        await pilot.press("left")
        await pilot.pause()
        assert tabs.active == "tab-hygiene"


@pytest.mark.asyncio
async def test_dashboard_footer_mounted(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        footers = list(screen.query(Footer))
        assert len(footers) == 1


@pytest.mark.asyncio
async def test_dashboard_modal_bindings(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)

        # Press '@' to open EmailModal
        await pilot.press("@")
        await pilot.pause()
        assert isinstance(pilot.app.screen, EmailModal)
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)

        # Press 'p' to open PolicyModal
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(pilot.app.screen, PolicyModal)
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_dashboard_enter_activates_account(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    switched_to: list[str] = []

    def mock_switch(paths: ManagerPaths, name: str) -> str:
        switched_to.append(name)
        return name

    monkeypatch.setattr("antigravity_cli_switcher.tui.screens.dashboard.switch_account", mock_switch)

    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        table = screen.query_one("#account-table", AccountTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()

        assert len(switched_to) == 1
        assert switched_to[0] in snapshot.accounts


@pytest.mark.asyncio
async def test_app_populates_account_table_on_boot(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        table = screen.query_one("#account-table", AccountTable)
        assert table.row_count == len(snapshot.accounts)
        assert table.row_count > 0


@pytest.mark.asyncio
async def test_tab_switch_focus_restoration(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        account_table = screen.query_one("#account-table", AccountTable)
        assert screen.focused == account_table

        # Switch to Fleet tab via '2'
        await pilot.press("2")
        await pilot.pause()
        fleet_table = screen.query_one("#fleet-table", DataTable)
        assert screen.focused == fleet_table

        # Switch to Logs tab via '3'
        await pilot.press("3")
        await pilot.pause()
        logs_log = screen.query_one("#logs-rich-log")
        assert screen.focused == logs_log

        # Switch to History tab via '4'
        await pilot.press("4")
        await pilot.pause()
        history_table = screen.query_one("#history-table", DataTable)
        assert screen.focused == history_table

        # Switch to Hygiene tab via '5'
        await pilot.press("5")
        await pilot.pause()
        hygiene_table = screen.query_one("#hygiene-table", DataTable)
        assert screen.focused == hygiene_table

        # Switch back to Accounts tab via '1'
        await pilot.press("1")
        await pilot.pause()
        assert screen.focused == account_table

        # Arrow key down moves row selection in account table
        assert account_table.cursor_row == 0
        await pilot.press("down")
        await pilot.pause()
        assert account_table.cursor_row == 1


@pytest.mark.asyncio
async def test_lazy_tab_dirty_tracking(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        # Inactive tabs must be marked dirty initially while on tab-accounts
        assert screen._accounts_dirty is False
        assert screen._hygiene_dirty is True
        assert screen._history_dirty is True
        assert screen._logs_dirty is True
        assert screen._fleet_dirty is True

        # Switch to tab-fleet
        await pilot.press("2")
        await pilot.pause()
        assert screen._fleet_dirty is False

        # Switch to tab-logs
        await pilot.press("3")
        await pilot.pause()
        assert screen._logs_dirty is False

        # Switch to tab-hygiene
        await pilot.press("5")
        await pilot.pause()
        assert screen._hygiene_dirty is False


def test_concurrency_guard_prevents_duplicate_refresh(temp_paths: ManagerPaths) -> None:
    app = ACSApp(paths=temp_paths)
    assert len(app._refreshing_accounts) == 0

    # Simulate in-progress refresh
    app._refreshing_accounts.add("alpha")

    # Second trigger should be ignored
    app.trigger_account_quota_refresh("alpha")
    assert "alpha" in app._refreshing_accounts
    assert len(app._refreshing_accounts) == 1

    # check_due_refresh must return None when refreshes are active
    result = app.check_due_refresh()
    assert result is None


@pytest.mark.asyncio
async def test_dashboard_relogin_key_opens_login_modal_prefilled(
    temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        table = screen.query_one("#account-table", AccountTable)
        table.focus()
        selected_name = table.get_selected_account_name()
        assert selected_name is not None

        # Press 'l' for relogin
        await pilot.press("l")
        await pilot.pause()

        assert len(pilot.app.screen_stack) > 1
        modal = pilot.app.screen_stack[-1]
        assert isinstance(modal, LoginModal)
        assert modal.is_relogin is True
        assert modal.initial_name == selected_name
        name_input = modal.query_one("#login-name", Input)
        assert name_input.value == selected_name

        # Dismiss modal
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_dashboard_hygiene_relogin_flow(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        # Switch to Hygiene tab via '5'
        await pilot.press("5")
        await pilot.pause()

        hygiene_tab = screen.query_one("#hygiene-tab", HygieneTab)
        selected_name = hygiene_tab.get_selected_account_name()
        assert selected_name is not None

        # Click the "Relogin Account" button
        await pilot.click("#btn-hygiene-relogin")
        await pilot.pause()

        assert len(pilot.app.screen_stack) > 1
        modal = pilot.app.screen_stack[-1]
        assert isinstance(modal, LoginModal)
        assert modal.is_relogin is True
        assert modal.initial_name == selected_name

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_suspend_terminal_manages_pause_and_resume(temp_paths: ManagerPaths) -> None:
    app = ACSApp(paths=temp_paths)
    assert app._pause_background.is_set() is False

    inside_executed = False
    with app.suspend_terminal():
        assert app._pause_background.is_set() is True
        inside_executed = True

    assert inside_executed is True
    assert app._pause_background.is_set() is False
    assert app._refresh_timer is not None
    app._refresh_timer.stop()


@pytest.mark.asyncio
async def test_dashboard_relogin_submission_flow(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, verification = create_sample_snapshot()
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    saved_calls: list[tuple[str, str | None, bool]] = []

    def mock_login_account(
        paths: ManagerPaths, name: str, agy_binary: str | None, overwrite_existing: bool = False
    ) -> str:
        saved_calls.append((name, agy_binary, overwrite_existing))
        return name

    monkeypatch.setattr("antigravity_cli_switcher.tui.screens.dashboard_actions.login_account", mock_login_account)

    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        # Trigger relogin via 'l'
        await pilot.press("l")
        await pilot.pause()

        assert len(pilot.app.screen_stack) > 1
        modal = pilot.app.screen_stack[-1]
        assert isinstance(modal, LoginModal)

        # Press enter on modal input to submit
        await pilot.press("enter")
        await pilot.pause()

        assert len(saved_calls) == 1
        assert saved_calls[0][0] == "alpha"
        assert saved_calls[0][2] is True  # overwrite_existing


def test_dashboard_footer_bindings_copywriting() -> None:
    from textual.binding import Binding

    bindings = [b for b in DashboardScreen.BINDINGS if isinstance(b, Binding)]
    binding_map = {b.key: b for b in bindings}
    assert binding_map["enter"].description == "Switch"
    assert binding_map["e"].description == "Enable"
    assert binding_map["e"].show is True


@pytest.mark.asyncio
async def test_dashboard_activate_auto_enables_disabled_account(
    temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, verification = create_sample_snapshot()
    # Mark beta as disabled
    snapshot.accounts["beta"].enabled = False

    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.fetch_snapshot_data",
        lambda paths: (snapshot, verification),
    )
    enabled_calls: list[tuple[str, bool]] = []
    switched_calls: list[str] = []

    def mock_set_enabled(paths: ManagerPaths, name: str, enabled: bool) -> None:
        enabled_calls.append((name, enabled))

    def mock_switch_account(paths: ManagerPaths, name: str) -> str:
        switched_calls.append(name)
        return name

    monkeypatch.setattr("antigravity_cli_switcher.tui.screens.dashboard_actions.set_enabled", mock_set_enabled)
    monkeypatch.setattr("antigravity_cli_switcher.tui.screens.dashboard.switch_account", mock_switch_account)

    app = ACSApp(paths=temp_paths)
    async with app.run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, DashboardScreen)
        await pilot.pause()

        screen.action_activate("beta")
        await pilot.pause()

        assert ("beta", True) in enabled_calls
        assert "beta" in switched_calls

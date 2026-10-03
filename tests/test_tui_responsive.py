"""Tests for TUI small device responsiveness, adaptive columns, and help modal."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Static

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    ProblemStatus,
    ProxyConfig,
    SnapshotVerification,
    StatusSnapshot,
)
from antigravity_cli_switcher.tui.app import ACSApp
from antigravity_cli_switcher.tui.modals.help import HelpModal
from antigravity_cli_switcher.tui.screens.dashboard import DashboardScreen
from antigravity_cli_switcher.tui.widgets.account_table import (
    ALL_COL_KEYS,
    COMPACT_COL_KEYS,
    MOBILE_COL_KEYS,
    AccountTable,
)
from antigravity_cli_switcher.tui.widgets.detail_panel import DetailPanel
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar


def create_sample_snapshot() -> tuple[StatusSnapshot, SnapshotVerification]:
    snapshot = StatusSnapshot(
        root="/tmp/test",
        runtime_dir="/tmp/test/runtime",
        lock_file="/tmp/test/lock",
        active="alpha",
        switch_mode="manual",
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
        },
    )
    verification = SnapshotVerification(
        active="alpha",
        switch_mode="manual",
        accounts={
            "alpha": AccountVerification(problem_status=ProblemStatus.OK, summary="Ready"),
            "beta": AccountVerification(problem_status=ProblemStatus.DISABLED, summary="Disabled"),
        },
    )
    return snapshot, verification


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
    (logs_dir / "manager.log").write_text("", encoding="utf-8")
    return paths


@pytest.mark.asyncio
async def test_help_modal_display_and_dismiss() -> None:
    modal = HelpModal()

    class HelpApp(ACSApp):
        def compose(self):
            yield Static("background")

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = HelpApp(paths=paths)
    async with app.run_test() as pilot:
        app.push_screen(modal)
        await pilot.pause()
        assert app.screen == modal

        # Check content is rendered
        content = modal.query_one("#help-content", Static)
        text_str = str(content.content)
        assert "Account Management" in text_str
        assert "Health & Hygiene" in text_str
        assert "Navigation & View" in text_str
        assert "Enter" in text_str
        assert "Toggle Details" in text_str

        # Dismiss via escape
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen != modal


@pytest.mark.asyncio
async def test_dashboard_toggle_details_panel(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
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

        panel = screen.query_one("#detail-panel", DetailPanel)
        initial_collapsed = panel.has_class("collapsed")

        # Press 'o' to toggle details panel
        await pilot.press("o")
        await pilot.pause()
        assert panel.has_class("collapsed") != initial_collapsed

        # Press 'o' again to toggle back
        await pilot.press("o")
        await pilot.pause()
        assert panel.has_class("collapsed") == initial_collapsed


@pytest.mark.asyncio
async def test_dashboard_question_mark_opens_help(temp_paths: ManagerPaths, monkeypatch: pytest.MonkeyPatch) -> None:
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

        # Press '?' to open help modal
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HelpModal)

        # Press 'escape' to close help modal
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)


def test_account_table_column_keys_for_widths() -> None:
    table = AccountTable()

    # Mobile width (< 75)
    assert table._get_column_keys_for_width(60) == MOBILE_COL_KEYS
    assert len(MOBILE_COL_KEYS) == 7

    # Compact width (75 - 104)
    assert table._get_column_keys_for_width(80) == COMPACT_COL_KEYS
    assert len(COMPACT_COL_KEYS) == 9

    # Full width (>= 105)
    assert table._get_column_keys_for_width(120) == ALL_COL_KEYS
    assert len(ALL_COL_KEYS) == 11

    # Unmeasured / default (0)
    assert table._get_column_keys_for_width(0) == ALL_COL_KEYS


def test_header_bar_responsive_content() -> None:
    header = HeaderBar()
    header.active_account = "alpha"
    header.account_count = 3
    header.refresh_interval = 5
    header.switch_mode = "manual"
    header.sort_mode = "name"

    # Full width format
    header._refresh_content()
    # Check reactive values reflect correctly
    assert header.active_account == "alpha"
    assert header.account_count == 3


@pytest.mark.asyncio
async def test_detail_panel_compact_presentation() -> None:
    panel = DetailPanel()
    snapshot, verification = create_sample_snapshot()

    class DetailApp(ACSApp):
        def compose(self):
            yield panel

    paths = ManagerPaths(
        root=Path("/tmp"),
        accounts_dir=Path("/tmp/acc"),
        state_file=Path("/tmp/state.json"),
        runtime_dir=Path("/tmp/run"),
        lock_file=Path("/tmp/lock"),
    )
    app = DetailApp(paths=paths)
    async with app.run_test(size=(60, 24)):
        meta = snapshot.accounts["alpha"]
        ver = verification.accounts["alpha"]
        panel.update_detail("alpha", meta, ver)
        content_static = panel.query_one("#detail-content", Static)
        assert content_static.content is not None
        rendered = str(content_static.content)
        assert "Token" in rendered
        assert "Backend" in rendered
        assert "Status" in rendered
        assert "Problem" in rendered

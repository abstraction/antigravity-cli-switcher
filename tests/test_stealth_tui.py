"""Unit and pilot tests for ACS TUI stealth components, modals, and detail panel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from antigravity_cli_switcher.cli.commands import dispatch_command
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.manager.paths import build_paths, ensure_layout
from antigravity_cli_switcher.manager.state import load_state, save_state
from antigravity_cli_switcher.models import AccountMeta, AccountVerification
from antigravity_cli_switcher.tui.modals.cooldown import StealthCooldownModal
from antigravity_cli_switcher.tui.widgets.detail_panel import DetailPanel
from antigravity_cli_switcher.tui.widgets.status_bar import StatusBar


class ModalTestApp(App[object]):
    """Minimal test harness for mounting and dismissing modals."""

    def compose(self) -> ComposeResult:
        yield Button("Open", id="btn-open")


@pytest.mark.asyncio
async def test_stealth_cooldown_modal_force_switch() -> None:
    results: list[str | None] = []
    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = StealthCooldownModal(target_account="account_one", remaining_seconds=45.0)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        # Press 'f' to force switch immediately
        await pilot.press("f")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == "force"


@pytest.mark.asyncio
async def test_stealth_cooldown_modal_cancel() -> None:
    results: list[str | None] = []
    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = StealthCooldownModal(target_account="account_one", remaining_seconds=45.0)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        # Press Escape to cancel
        await pilot.press("escape")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == "cancel"


@pytest.mark.asyncio
async def test_stealth_cooldown_modal_wait_and_tick() -> None:
    results: list[str | None] = []
    app = ModalTestApp()
    async with app.run_test() as pilot:
        # Start modal with small remaining seconds
        modal = StealthCooldownModal(target_account="account_one", remaining_seconds=1.5)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        # Press Enter for wait and switch
        await pilot.press("enter")
        assert modal.waiting_to_switch is True

        # Manually tick down to 0
        modal._tick()
        modal._tick()
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == "switch"


def test_status_bar_stealth_styling() -> None:
    bar = StatusBar()
    bar.message = "⚠️ [STEALTH] Terminated external CLI session (PID: 1234) to purge session header."
    # Calling _refresh_content without crashing
    bar._refresh_content()

    bar.message = "[STEALTH] Cooldown active: 45s remaining."
    bar._refresh_content()


@pytest.mark.asyncio
async def test_detail_panel_stealth_rendering() -> None:
    from textual.widgets import Static

    panel = DetailPanel()

    class PanelApp(App[object]):
        def compose(self) -> ComposeResult:
            yield panel

    app = PanelApp()
    async with app.run_test():
        meta = AccountMeta(
            enabled=True,
            status="standby",
        )
        verification = AccountVerification(
            installation_id="11111111-2222-3333-4444-555555555555",
            root_installation_id="66666666-7777-8888-9999-000000000000",
            installation_id_status="MATCH",
        )

        # Render with active cooldown
        panel.update_detail(
            name="test_acc",
            meta=meta,
            verification=verification,
            cooldown_remaining=42.5,
        )
        content_static = panel.query_one("#detail-content", Static)
        rendered_plain = str(content_static.content)
        assert "Install ID" in rendered_plain
        assert "MATCH" in rendered_plain
        assert "Root ID" in rendered_plain
        assert "Stealth CD" in rendered_plain
        assert "42s" in rendered_plain


def test_cli_switch_force_flag_parsing_and_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = build_parser()
    args = parser.parse_args(["switch", "test_acc", "--force"])
    assert args.command == "switch"
    assert args.force is True

    args_next = parser.parse_args(["switch-next", "-f"])
    assert args_next.command == "switch-next"
    assert args_next.force is True

    # Test error handling when cooldown active without force
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    acc = "acc_a"
    (paths.accounts_dir / acc).mkdir(parents=True)
    state = load_state(paths)
    state["accounts"] = {acc: {"enabled": True, "status": "standby"}}
    state["active"] = acc
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")
    # Switch once to activate cooldown
    with patch(
        "antigravity_cli_switcher.cli.commands.switch_account", side_effect=RuntimeError("Stealth cooldown active: 60s")
    ):
        args_no_force = parser.parse_args(["switch", acc])
        ret = dispatch_command(paths, args_no_force, parser)
        assert ret == 1


@pytest.mark.asyncio
async def test_stealth_cooldown_modal_does_not_auto_switch_without_enter() -> None:
    results: list[str | None] = []
    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = StealthCooldownModal(target_account="account_one", remaining_seconds=1.5)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        # Did NOT press Enter. Tick down to 0
        modal._tick()
        modal._tick()
        await pilot.pause()

        # Modal is NOT dismissed because user hasn't confirmed wait-and-switch
        assert len(results) == 0
        assert modal.remaining_seconds == 0.0

        # Pressing Enter now immediately executes the switch
        await pilot.press("enter")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == "switch"


def test_app_in_memory_cooldown_ticks_without_disk_io(tmp_path: Path) -> None:
    from antigravity_cli_switcher.manager.state import utc_now
    from antigravity_cli_switcher.tui.app import ACSApp

    paths = build_paths(tmp_path / "switcher")
    app = ACSApp(paths)

    # Initial state with no switches
    assert app.get_switch_cooldown_remaining() == 0.0

    # Record switch in memory
    app.record_account_switch(utc_now().isoformat())

    # Mock load_state to ensure it is NOT called
    with patch("antigravity_cli_switcher.manager.state.load_state", side_effect=AssertionError("Disk read called!")):
        rem = app.get_switch_cooldown_remaining()
        assert 50 < rem <= 60

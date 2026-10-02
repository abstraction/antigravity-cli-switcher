"""Unit and pilot tests for ACS TUI modal dialogs."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Select

from antigravity_cli_switcher.models import ProxyConfig, SwitchPolicy, SwitchPolicyUpdate
from antigravity_cli_switcher.tui.modals.confirm import ConfirmModal
from antigravity_cli_switcher.tui.modals.email import EmailModal
from antigravity_cli_switcher.tui.modals.login import LoginModal
from antigravity_cli_switcher.tui.modals.policy import PolicyModal
from antigravity_cli_switcher.tui.modals.proxy import ProxyModal
from antigravity_cli_switcher.tui.modals.rename import RenameModal


class ModalTestApp(App[object]):
    """Minimal test harness for mounting and dismissing modals."""

    def compose(self) -> ComposeResult:
        yield Button("Open", id="btn-open")


@pytest.mark.asyncio
async def test_email_modal_submit() -> None:
    """Test EmailModal successfully returns a submitted email."""
    result_holder: list[str | None] = []

    def on_result(val: str | None) -> None:
        result_holder.append(val)

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = EmailModal(account_name="acc1", current_email="old@example.com")
        app.push_screen(modal, on_result)
        await pilot.pause()

        email_input = modal.query_one("#email-input", Input)
        assert email_input.value == "old@example.com"

        email_input.value = "new@example.com"
        await pilot.click("#btn-submit")
        await pilot.pause()

        assert len(result_holder) == 1
        assert result_holder[0] == "new@example.com"


@pytest.mark.asyncio
async def test_email_modal_clear_and_cancel() -> None:
    """Test EmailModal clear returns empty string and cancel returns None."""
    clear_results: list[str | None] = []
    cancel_results: list[str | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        # Test Clear (saving empty value clears email)
        modal_clear = EmailModal(account_name="acc1", current_email="some@example.com")
        app.push_screen(modal_clear, lambda r: clear_results.append(r))
        await pilot.pause()
        email_input = modal_clear.query_one("#email-input", Input)
        email_input.value = ""
        await pilot.click("#btn-submit")
        await pilot.pause()
        assert len(clear_results) == 1
        assert clear_results[0] == ""

        # Test Cancel
        modal_cancel = EmailModal(account_name="acc1", current_email="some@example.com")
        app.push_screen(modal_cancel, lambda r: cancel_results.append(r))
        await pilot.pause()
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert len(cancel_results) == 1
        assert cancel_results[0] is None


@pytest.mark.asyncio
async def test_policy_modal_submit() -> None:
    """Test PolicyModal returns updated SwitchPolicyUpdate on save."""
    policy_results: list[SwitchPolicyUpdate | None] = []
    initial_policy = SwitchPolicy(
        short_usage_threshold_percent=85,
        refresh_failure_threshold=4,
        candidate_strategy="balanced",
        family_fallback_strategy="same-family-first",
    )

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = PolicyModal(current_policy=initial_policy)
        app.push_screen(modal, lambda r: policy_results.append(r))
        await pilot.pause()

        # Update input values
        short_input = modal.query_one("#input-short-threshold", Input)
        short_input.value = "75"

        strat_select = modal.query_one("#select-candidate-strategy", Select)
        strat_select.value = "highest-short"

        await pilot.click("#btn-submit")
        await pilot.pause()

        assert len(policy_results) == 1
        res = policy_results[0]
        assert res is not None
        assert res.short_usage_threshold_percent == 75
        assert res.candidate_strategy == "highest-short"


@pytest.mark.asyncio
async def test_policy_modal_cancel() -> None:
    """Test PolicyModal returns None when canceled."""
    policy_results: list[SwitchPolicyUpdate | None] = []
    initial_policy = SwitchPolicy()

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = PolicyModal(current_policy=initial_policy)
        app.push_screen(modal, lambda r: policy_results.append(r))
        await pilot.pause()

        await pilot.click("#btn-cancel")
        await pilot.pause()

        assert len(policy_results) == 1
        assert policy_results[0] is None


@pytest.mark.asyncio
async def test_confirm_modal_flow() -> None:
    """Test ConfirmModal returns True on confirm and False on cancel."""
    results: list[bool | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        # Confirm
        modal = ConfirmModal("Delete", "Are you sure?")
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()
        await pilot.click("#btn-confirm")
        await pilot.pause()
        assert results == [True]

        # Cancel
        modal_no = ConfirmModal("Delete", "Are you sure?")
        app.push_screen(modal_no, lambda r: results.append(r))
        await pilot.pause()
        await pilot.click("#btn-cancel")
        await pilot.pause()
        assert results == [True, False]


@pytest.mark.asyncio
async def test_rename_modal_flow() -> None:
    """Test RenameModal returns new name on submit and None on cancel."""
    results: list[str | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = RenameModal("old_acc")
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        name_input = modal.query_one("#rename-input", Input)
        name_input.value = "new_acc"
        await pilot.click("#btn-submit")
        await pilot.pause()
        assert results == ["new_acc"]


@pytest.mark.asyncio
async def test_proxy_modal_flow() -> None:
    """Test ProxyModal returns proxy configuration tuple."""
    results: list[tuple[str, str | None, bool] | None] = []
    cfg = ProxyConfig(url="http://proxy.test:8080", label="Test", enabled=True)

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = ProxyModal("account1", proxy=cfg)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        url_input = modal.query_one("#proxy-url", Input)
        url_input.value = "http://updated.proxy:8080"
        await pilot.click("#btn-submit")
        await pilot.pause()

        assert len(results) == 1
        res = results[0]
        assert res is not None
        url, label, enabled = res
        assert url == "http://updated.proxy:8080"
        assert label == "Test"
        assert enabled is True


@pytest.mark.asyncio
async def test_login_modal_new_flow() -> None:
    """Test LoginModal for a new account without initial name."""
    results: list[tuple[str, str | None] | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = LoginModal()
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        name_input = modal.query_one("#login-name", Input)
        assert name_input.value == ""
        name_input.value = "brand_new_acc"

        binary_input = modal.query_one("#login-binary", Input)
        binary_input.value = "/custom/bin/agy"

        await pilot.click("#btn-submit")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == ("brand_new_acc", "/custom/bin/agy")


@pytest.mark.asyncio
async def test_login_modal_relogin_flow() -> None:
    """Test LoginModal for relogging in with prefilled account name."""
    results: list[tuple[str, str | None] | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = LoginModal(initial_name="[3] gamma", is_relogin=True)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        name_input = modal.query_one("#login-name", Input)
        assert name_input.value == "[3] gamma"

        # Direct submit without changing name
        await pilot.click("#btn-submit")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] == ("[3] gamma", None)


@pytest.mark.asyncio
async def test_login_modal_cancel() -> None:
    """Test LoginModal dismisses with None on cancel."""
    results: list[tuple[str, str | None] | None] = []

    app = ModalTestApp()
    async with app.run_test() as pilot:
        modal = LoginModal(initial_name="some_acc", is_relogin=True)
        app.push_screen(modal, lambda r: results.append(r))
        await pilot.pause()

        await pilot.click("#btn-cancel")
        await pilot.pause()

        assert len(results) == 1
        assert results[0] is None

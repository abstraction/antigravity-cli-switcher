"""Base screen with action handlers for DashboardScreen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.screen import Screen

from antigravity_cli_switcher.manager import (
    clear_bad,
    delete_account,
    import_current,
    login_account,
    mark_bad,
    rename_account,
    set_enabled,
    set_expected_email,
    set_switch_mode,
    switch_next,
    update_switch_policy,
)
from antigravity_cli_switcher.manager.login import _ensure_safe_account_switch
from antigravity_cli_switcher.models import (
    SnapshotVerification,
    StatusSnapshot,
    SwitchPolicyUpdate,
)
from antigravity_cli_switcher.tui.modals.confirm import ConfirmModal
from antigravity_cli_switcher.tui.modals.email import EmailModal
from antigravity_cli_switcher.tui.modals.login import LoginModal
from antigravity_cli_switcher.tui.modals.policy import PolicyModal
from antigravity_cli_switcher.tui.modals.rename import RenameModal
from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp


class DashboardActionsScreenBase(Screen[None]):
    """Base screen defining state attributes and action handlers for DashboardScreen."""

    snapshot: StatusSnapshot | None
    verification: SnapshotVerification | None
    sort_mode: str
    _sort_modes: list[str]
    _sort_idx: int

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def _selected_account_name(self) -> str | None:
        raise NotImplementedError

    def _set_status(self, message: str, worker_status: str | None = None) -> None:
        raise NotImplementedError

    def _update_detail_from_selection(self) -> None:
        raise NotImplementedError

    @work(thread=True)
    def action_activate(self, account_name: str | None = None) -> None:
        name = account_name or self._selected_account_name()
        if not name:
            self.app.call_from_thread(self._set_status, "No account selected.")
            return
        self.app.call_from_thread(self._set_status, f"Activating {name}...")
        try:
            import antigravity_cli_switcher.tui.screens.dashboard as dash_mod

            dash_mod.switch_account(self.acs_app.paths, name)
            self.app.call_from_thread(self._set_status, f"Activated {name}.")
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Activation failed: {exc}")

    @work(thread=True)
    def action_switch_next(self) -> None:
        self.app.call_from_thread(self._set_status, "Switching to next account...")
        try:
            next_name = switch_next(self.acs_app.paths)
            self.app.call_from_thread(self._set_status, f"Switched to {next_name}.")
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Switch failed: {exc}")

    @work(thread=True)
    def action_toggle_enabled(self) -> None:
        name = self._selected_account_name()
        if not name or not self.snapshot or name not in self.snapshot.accounts:
            return
        meta = self.snapshot.accounts[name]
        new_state = not meta.enabled
        try:
            set_enabled(self.acs_app.paths, name, new_state)
            self.app.call_from_thread(
                self._set_status,
                f"Set {name} to {'enabled' if new_state else 'disabled'}.",
            )
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Toggle state failed: {exc}")

    @work(thread=True)
    def action_clear_bad(self) -> None:
        name = self._selected_account_name()
        if not name:
            return
        try:
            clear_bad(self.acs_app.paths, name)
            self.app.call_from_thread(self._set_status, f"Cleared broken flag for {name}.")
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Clear broken flag failed: {exc}")

    @work(thread=True)
    def action_mark_bad(self) -> None:
        name = self._selected_account_name()
        if not name:
            return
        try:
            mark_bad(self.acs_app.paths, name, reason="Manually flagged in TUI", cooldown_minutes=30)
            self.app.call_from_thread(self._set_status, f"Flagged {name} as broken (30m cooldown).")
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Flag broken failed: {exc}")

    def action_rename(self) -> None:
        name = self._selected_account_name()
        if not name:
            return

        def on_rename_submitted(new_name: str | None) -> None:
            if not new_name or new_name == name:
                return
            try:
                rename_account(self.acs_app.paths, name, new_name)
                self._set_status(f"Renamed {name} to {new_name}.")
                self.acs_app.refresh_snapshot()
            except Exception as exc:
                self._set_status(f"Rename failed: {exc}")

        self.app.push_screen(RenameModal(current_name=name), on_rename_submitted)

    def action_delete(self) -> None:
        name = self._selected_account_name()
        if not name:
            return

        def on_delete_confirmed(confirmed: bool | None) -> None:
            if not confirmed:
                return
            try:
                delete_account(self.acs_app.paths, name)
                self._set_status(f"Deleted {name}.")
                self.acs_app.refresh_snapshot()
            except Exception as exc:
                self._set_status(f"Delete failed: {exc}")

        self.app.push_screen(
            ConfirmModal("Delete account", f"Delete account '{name}'?"),
            on_delete_confirmed,
        )

    def action_new_login(self) -> None:
        def on_login_submitted(result: tuple[str, str | None] | None) -> None:
            if not result:
                return
            acc_name, binary = result
            try:
                _ensure_safe_account_switch()
            except RuntimeError as exc:
                self._set_status(f"Login blocked: {exc}")
                return

            try:
                with self.acs_app.suspend_terminal():
                    saved = login_account(self.acs_app.paths, acc_name, binary)
                if saved:
                    self._set_status(f"Logged in as {saved}.")
                else:
                    self._set_status("Login cancelled.")
                self.acs_app.refresh_snapshot()
            except (KeyboardInterrupt, EOFError):
                self._set_status("Login cancelled.")
            except Exception as exc:
                self._set_status(f"Login failed: {exc}")

        self.app.push_screen(LoginModal(), on_login_submitted)

    def action_relogin(self, account_name: str | None = None) -> None:
        target_name = account_name or self._selected_account_name()
        if not target_name:
            self._set_status("No account selected to relogin.")
            return

        def on_relogin_submitted(result: tuple[str, str | None] | None) -> None:
            if not result:
                return
            acc_name, binary = result
            try:
                _ensure_safe_account_switch()
            except RuntimeError as exc:
                self._set_status(f"Relogin blocked: {exc}")
                return

            try:
                with self.acs_app.suspend_terminal():
                    saved = login_account(self.acs_app.paths, acc_name, binary, overwrite_existing=True)
                if saved:
                    self._set_status(f"Relogged in as {saved}.")
                else:
                    self._set_status("Relogin cancelled.")
                self.acs_app.refresh_snapshot()
            except (KeyboardInterrupt, EOFError):
                self._set_status("Relogin cancelled.")
            except Exception as exc:
                self._set_status(f"Relogin failed: {exc}")

        self.app.push_screen(
            LoginModal(initial_name=target_name, is_relogin=True),
            on_relogin_submitted,
        )

    def action_import_profile(self) -> None:
        def on_import_submitted(name: str | None) -> None:
            if not name:
                return
            try:
                import_current(self.acs_app.paths, name)
                self._set_status(f"Imported active profile as {name}.")
                self.acs_app.refresh_snapshot()
            except Exception as exc:
                self._set_status(f"Import failed: {exc}")

        self.app.push_screen(RenameModal(current_name="imported"), on_import_submitted)

    def action_refresh_data(self) -> None:
        name = self._selected_account_name()
        if name:
            self.acs_app.trigger_account_quota_refresh(name)
            return
        self._set_status("Refreshing snapshot...")
        self.acs_app.refresh_snapshot()

    def action_refresh_usage(self) -> None:
        name = self._selected_account_name()
        if not name:
            self._set_status("No account selected.")
            return
        self.acs_app.trigger_account_quota_refresh(name)

    @work(thread=True)
    def action_toggle_switch_mode(self) -> None:
        if not self.snapshot:
            return
        new_mode = "manual" if self.snapshot.switch_mode == "auto" else "auto"
        try:
            set_switch_mode(self.acs_app.paths, new_mode)
            self.app.call_from_thread(self._set_status, f"Switch mode set to {new_mode}.")
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Change mode failed: {exc}")

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self._sort_modes)
        self.sort_mode = self._sort_modes[self._sort_idx]
        header = self.query_one("#header-bar", HeaderBar)
        header.sort_mode = self.sort_mode
        if self.snapshot and self.verification:
            table = self.query_one("#account-table", AccountTable)
            table.update_accounts(self.snapshot, self.verification, sort_mode=self.sort_mode)
            self._update_detail_from_selection()
        self._set_status(f"Sort: {self.sort_mode}")

    def action_cycle_refresh(self) -> None:
        intervals = [5, 10, 15, 30]
        cur = self.acs_app.refresh_interval_seconds
        idx = intervals.index(cur) if cur in intervals else 0
        new_interval = intervals[(idx + 1) % len(intervals)]
        self.acs_app.set_refresh_interval(new_interval)
        header = self.query_one("#header-bar", HeaderBar)
        header.refresh_interval = new_interval
        self._set_status(f"Interval set to {new_interval}s.")

    def action_edit_email(self) -> None:
        name = self._selected_account_name()
        if not name or not self.snapshot or name not in self.snapshot.accounts:
            self._set_status("No account selected.")
            return
        cur_email = self.snapshot.accounts[name].expected_email

        def on_email_submitted(result: str | None) -> None:
            if result is None:
                return
            try:
                set_expected_email(self.acs_app.paths, name, result)
                if result:
                    self._set_status(f"Set expected email for {name}: {result}")
                else:
                    self._set_status(f"Cleared expected email for {name}.")
                self.acs_app.refresh_snapshot()
            except Exception as exc:
                self._set_status(f"Set expected email failed: {exc}")

        self.app.push_screen(EmailModal(account_name=name, current_email=cur_email), on_email_submitted)

    def action_edit_policy(self) -> None:
        if not self.snapshot:
            self._set_status("No snapshot available.")
            return
        cur_policy = self.snapshot.switch_policy

        def on_policy_submitted(result: SwitchPolicyUpdate | None) -> None:
            if not result:
                return
            try:
                update_switch_policy(
                    self.acs_app.paths,
                    short_usage_threshold_percent=result.short_usage_threshold_percent,
                    refresh_failure_threshold=result.refresh_failure_threshold,
                    candidate_strategy=result.candidate_strategy,
                    family_fallback_strategy=result.family_fallback_strategy,
                )
                self._set_status(f"Saved switch policy ({result.candidate_strategy}).")
                self.acs_app.refresh_snapshot()
            except Exception as exc:
                self._set_status(f"Save switch policy failed: {exc}")

        self.app.push_screen(PolicyModal(current_policy=cur_policy), on_policy_submitted)

    def action_quit(self) -> None:
        self.app.exit(0)

"""Dashboard screen for ACS Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, TabbedContent, TabPane

from antigravity_cli_switcher.manager import switch_account
from antigravity_cli_switcher.models import (
    SnapshotVerification,
    StatusSnapshot,
)
from antigravity_cli_switcher.tui.messages import SnapshotUpdated, StatusMessage
from antigravity_cli_switcher.tui.modals import HelpModal
from antigravity_cli_switcher.tui.screens.dashboard_actions import DashboardActionsScreenBase
from antigravity_cli_switcher.tui.widgets.account_table import AccountTable
from antigravity_cli_switcher.tui.widgets.detail_panel import DetailPanel
from antigravity_cli_switcher.tui.widgets.fleet_tab import FleetTab
from antigravity_cli_switcher.tui.widgets.header_bar import HeaderBar
from antigravity_cli_switcher.tui.widgets.history_tab import HistoryTab
from antigravity_cli_switcher.tui.widgets.hygiene_tab import HygieneTab
from antigravity_cli_switcher.tui.widgets.logs_tab import LogsTab
from antigravity_cli_switcher.tui.widgets.proxy_tab import ProxyTab
from antigravity_cli_switcher.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp

__all__ = ["DashboardScreen", "switch_account"]


class DashboardScreen(DashboardActionsScreenBase):
    """Main dashboard screen displaying tabbed content, account overview, and shortcuts."""

    TAB_IDS: ClassVar[list[str]] = [
        "tab-accounts",
        "tab-fleet",
        "tab-logs",
        "tab-history",
        "tab-hygiene",
        "tab-proxies",
    ]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "switch_tab('tab-accounts')", "Accounts", show=False),
        Binding("2", "switch_tab('tab-fleet')", "Fleet", show=False),
        Binding("3", "switch_tab('tab-logs')", "Logs", show=False),
        Binding("4", "switch_tab('tab-history')", "History", show=False),
        Binding("5", "switch_tab('tab-hygiene')", "Hygiene", show=False),
        Binding("6", "switch_tab('tab-proxies')", "Proxies", show=False),
        Binding("left", "prev_tab", "Prev Tab", show=False, priority=True),
        Binding("right", "next_tab", "Next Tab", show=False, priority=True),
        Binding("[", "prev_tab", "Prev Tab", show=False),
        Binding("]", "next_tab", "Next Tab", show=False),
        Binding("enter", "activate", "Switch"),
        Binding("a", "activate", "Switch", show=False),
        Binding("l", "relogin", "Relogin"),
        Binding("n", "new_login", "New", show=False),
        Binding("i", "import_profile", "Import", show=False),
        Binding("r", "switch_next", "Next"),
        Binding("e", "toggle_enabled", "Enable", show=True),
        Binding("c", "clear_bad", "Clear", show=False),
        Binding("m", "mark_bad", "Flag", show=False),
        Binding("f2", "rename", "Rename", show=False),
        Binding("v", "rename", "Rename", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("u", "refresh_usage", "Quota"),
        Binding("w", "toggle_switch_mode", "Mode", show=False),
        Binding("s", "cycle_sort", "Sort", show=False),
        Binding("t", "refresh_data", "Refresh", show=False),
        Binding("f5", "refresh_data", "Refresh", show=False),
        Binding("T", "cycle_refresh", "Interval", show=False),
        Binding("@", "edit_email", "Email", show=False),
        Binding("p", "edit_policy", "Policy", show=False),
        Binding("o", "toggle_detail", "Details"),
        Binding("question_mark", "show_help", "Help"),
        Binding("?", "show_help", "Help", show=False),
        Binding("h", "show_help", "Help", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        initial_tab: str = "tab-accounts",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.initial_tab = initial_tab
        self.snapshot: StatusSnapshot | None = None
        self.verification: SnapshotVerification | None = None
        self.sort_mode: str = "usage-low"
        self._sort_modes = ["usage-low", "usage-high", "name", "state", "health"]
        self._sort_idx = 0
        self._accounts_dirty: bool = False
        self._hygiene_dirty: bool = True
        self._history_dirty: bool = True
        self._logs_dirty: bool = True
        self._proxy_dirty: bool = True
        self._fleet_dirty: bool = True

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        with TabbedContent(initial=self.initial_tab, id="main-tabs"):
            with TabPane("Accounts", id="tab-accounts"):
                yield AccountTable(id="account-table")
                yield DetailPanel(id="detail-panel")
            with TabPane("Fleet", id="tab-fleet"):
                yield FleetTab(id="fleet-tab")
            with TabPane("Logs", id="tab-logs"):
                yield LogsTab(id="logs-tab")
            with TabPane("History", id="tab-history"):
                yield HistoryTab(id="history-tab")
            with TabPane("Hygiene", id="tab-hygiene"):
                yield HygieneTab(id="hygiene-tab")
            with TabPane("Proxies", id="tab-proxies"):
                yield ProxyTab(id="proxy-tab")
        with Vertical(id="bottom-container"):
            yield StatusBar(id="status-bar")
            yield Footer()

    def on_mount(self) -> None:
        if self.initial_tab == "tab-accounts":
            self.query_one("#account-table", AccountTable).focus()
        if 0 < self.size.height < 24:
            self.query_one("#detail-panel", DetailPanel).add_class("collapsed")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Focus active tab widget and apply pending dirty updates."""
        pane_id = event.pane.id if event.pane else ""
        if pane_id == "tab-accounts":
            table = self.query_one("#account-table", AccountTable)
            table.focus()
            if self._accounts_dirty and self.snapshot and self.verification:
                table.update_accounts(self.snapshot, self.verification, sort_mode=self.sort_mode)
                self._update_detail_from_selection()
                self._accounts_dirty = False
        elif pane_id == "tab-hygiene":
            self.query_one("#hygiene-table", DataTable).focus()
            if self._hygiene_dirty and self.snapshot and self.verification:
                self.query_one("#hygiene-tab", HygieneTab).update_hygiene(self.snapshot, self.verification)
                self._hygiene_dirty = False
        elif pane_id == "tab-history":
            self.query_one("#history-table", DataTable).focus()
            if self._history_dirty and self.snapshot:
                self.query_one("#history-tab", HistoryTab).update_history(self.snapshot)
                self._history_dirty = False
        elif pane_id == "tab-logs":
            self.query_one("#logs-rich-log").focus()
            if self._logs_dirty:
                self.query_one("#logs-tab", LogsTab).poll_new_logs()
                self._logs_dirty = False
        elif pane_id == "tab-proxies":
            self.query_one("#proxy-table", DataTable).focus()
            if self._proxy_dirty and self.snapshot:
                self.query_one("#proxy-tab", ProxyTab).update_proxies(self.snapshot)
                self._proxy_dirty = False
        elif pane_id == "tab-fleet":
            self.query_one("#fleet-table", DataTable).focus()
            if self._fleet_dirty and self.snapshot:
                self.query_one("#fleet-tab", FleetTab).update_fleet(self.snapshot)
                self._fleet_dirty = False

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        """Handle incoming snapshot updates from background worker."""
        self.snapshot = message.snapshot
        self.verification = message.verification

        header = self.query_one("#header-bar", HeaderBar)
        header.active_account = self.snapshot.active
        header.account_count = len(self.snapshot.accounts)
        header.switch_mode = self.snapshot.switch_mode
        header.quota_backend = self.snapshot.quota_backend
        header.candidate_strategy = self.snapshot.switch_policy.candidate_strategy
        header.refresh_interval = self.acs_app.refresh_interval_seconds
        header.sort_mode = self.sort_mode

        active_tab = self.query_one("#main-tabs", TabbedContent).active or "tab-accounts"
        self._accounts_dirty = active_tab != "tab-accounts"
        self._hygiene_dirty = active_tab != "tab-hygiene"
        self._history_dirty = active_tab != "tab-history"
        self._logs_dirty = active_tab != "tab-logs"
        self._proxy_dirty = active_tab != "tab-proxies"
        self._fleet_dirty = active_tab != "tab-fleet"

        if active_tab == "tab-accounts":
            table = self.query_one("#account-table", AccountTable)
            table.update_accounts(self.snapshot, self.verification, sort_mode=self.sort_mode)
            self._update_detail_from_selection()
        elif active_tab == "tab-hygiene":
            self.query_one("#hygiene-tab", HygieneTab).update_hygiene(self.snapshot, self.verification)
        elif active_tab == "tab-history":
            self.query_one("#history-tab", HistoryTab).update_history(self.snapshot)
        elif active_tab == "tab-logs":
            self.query_one("#logs-tab", LogsTab).poll_new_logs()
        elif active_tab == "tab-proxies":
            self.query_one("#proxy-tab", ProxyTab).update_proxies(self.snapshot)
        elif active_tab == "tab-fleet":
            self.query_one("#fleet-tab", FleetTab).update_fleet(self.snapshot)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Update detail panel when account selection changes in the account table."""
        if event.data_table.id == "account-table":
            self._update_detail_from_selection()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Trigger account activation when a row is selected via Enter or click in the account or fleet table."""
        if event.data_table.id in ("account-table", "fleet-table"):
            row_key_val = str(event.row_key.value) if event.row_key and event.row_key.value is not None else None
            self.action_activate(row_key_val)

    def _update_detail_from_selection(self) -> None:
        if not self.snapshot:
            return
        table = self.query_one("#account-table", AccountTable)
        name = table.get_selected_account_name()
        detail = self.query_one("#detail-panel", DetailPanel)
        if name and name in self.snapshot.accounts:
            meta = self.snapshot.accounts[name]
            ver = self.verification.accounts.get(name) if self.verification else None
            util = (
                self.snapshot.fleet_utilization.accounts.get(name)
                if self.snapshot.fleet_utilization and name in self.snapshot.fleet_utilization.accounts
                else None
            )
            detail.update_detail(name, meta, ver, util)
        else:
            detail.update_detail(None, None, None, None)

    def _selected_account_name(self) -> str | None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        cur = tabs.active or "tab-accounts"
        if cur == "tab-hygiene":
            return self.query_one("#hygiene-tab", HygieneTab).get_selected_account_name()
        if cur == "tab-proxies":
            return self.query_one("#proxy-tab", ProxyTab).get_selected_account_name()
        if cur == "tab-fleet":
            return self.query_one("#fleet-tab", FleetTab).get_selected_account_name()
        table = self.query_one("#account-table", AccountTable)
        return table.get_selected_account_name()

    def action_switch_tab(self, tab_id: str) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = tab_id

    def action_prev_tab(self) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        cur = tabs.active or "tab-accounts"
        idx = self.TAB_IDS.index(cur) if cur in self.TAB_IDS else 0
        tabs.active = self.TAB_IDS[(idx - 1) % len(self.TAB_IDS)]

    def action_next_tab(self) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        cur = tabs.active or "tab-accounts"
        idx = self.TAB_IDS.index(cur) if cur in self.TAB_IDS else 0
        tabs.active = self.TAB_IDS[(idx + 1) % len(self.TAB_IDS)]

    def action_toggle_detail(self) -> None:
        """Toggle visibility of the detail overview panel."""
        self.query_one("#detail-panel", DetailPanel).toggle_class("collapsed")

    def action_show_help(self) -> None:
        """Display keyboard shortcuts help dialog."""
        self.app.push_screen(HelpModal())

    def on_status_message(self, message: StatusMessage) -> None:
        """Display status message in status bar."""
        message.stop()
        self._set_status(message.message, worker_status=message.worker_status)

    def _set_status(self, message: str, worker_status: str | None = None) -> None:
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.message = message
        if worker_status is not None:
            status_bar.worker_status = worker_status

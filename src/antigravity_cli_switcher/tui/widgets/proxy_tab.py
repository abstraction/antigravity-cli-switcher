"""Proxy management tab widget for ACS Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from antigravity_cli_switcher.manager import clear_account_proxy, set_account_proxy
from antigravity_cli_switcher.models import AccountMeta, StatusSnapshot
from antigravity_cli_switcher.tui.messages import StatusMessage
from antigravity_cli_switcher.tui.modals.proxy import ProxyModal
from antigravity_cli_switcher.tui.workers import check_proxy_health

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp


class ProxyTab(Widget):
    """Tab widget for viewing and managing account proxy configurations."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.account_order: list[str] = []
        self._proxy_latencies: dict[str, str] = {}
        self.snapshot: StatusSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Static("Proxies: loading...", id="proxy-summary")
        table: DataTable[Text | str] = DataTable(
            id="proxy-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield table
        with Horizontal(id="proxy-actions"):
            yield Button("Configure", id="btn-proxy-edit", variant="primary")
            yield Button("Toggle", id="btn-proxy-toggle", variant="default")
            yield Button("Clear", id="btn-proxy-clear", variant="error")
            yield Button("Test", id="btn-proxy-test", variant="default")

    def on_mount(self) -> None:
        table = self.query_one("#proxy-table", DataTable)
        table.add_column("Sel", key="sel", width=3)
        table.add_column("Account", key="account", width=16)
        table.add_column("State", key="state", width=8)
        table.add_column("Proxy", key="proxy_status", width=7)
        table.add_column("Label", key="label", width=12)
        table.add_column("URL", key="url", width=22)
        table.add_column("Latency", key="latency", width=10)

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def get_selected_account_name(self) -> str | None:
        """Return the name of the currently selected account in the table."""
        table = self.query_one("#proxy-table", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self.account_order):
            return self.account_order[table.cursor_row]
        return None

    def _row_values(
        self,
        name: str,
        meta: AccountMeta,
        active_name: str | None,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text]:
        proxy = meta.proxy
        is_active = name == active_name
        state_str = "active" if is_active else ("disabled" if not meta.enabled else "standby")

        marker = Text(" ★", style="bold green") if is_active else Text("")
        name_style = "bold green" if is_active else ("dim italic" if not meta.enabled else "bold cyan")
        name_text = Text(name, style=name_style)

        state_style = "bold green" if state_str == "active" else ("dim" if state_str == "disabled" else "white")
        state_text = Text(state_str.capitalize(), style=state_style)

        if proxy.enabled and proxy.url:
            proxy_badge = Text(" ON ", style="bold black on green")
        elif proxy.url:
            proxy_badge = Text(" SAVED ", style="bold black on yellow")
        else:
            proxy_badge = Text(" - ", style="dim")

        label_text = Text(proxy.label or "-", style="white" if proxy.label else "dim")
        url_text = Text(proxy.url or "-", style="cyan" if proxy.url else "dim")

        latency = self._proxy_latencies.get(name, proxy.status)
        lat_style = "green" if latency == "ok" else ("yellow" if latency == "testing" else "dim")
        latency_text = Text(latency, style=lat_style)

        return (
            marker,
            name_text,
            state_text,
            proxy_badge,
            label_text,
            url_text,
            latency_text,
        )

    def update_proxies(self, snapshot: StatusSnapshot) -> None:
        """Update proxy table from latest status snapshot."""
        self.snapshot = snapshot
        table = self.query_one("#proxy-table", DataTable)
        summary = self.query_one("#proxy-summary", Static)

        active_name = snapshot.active
        items = sorted(snapshot.accounts.items(), key=lambda p: p[0].lower())

        enabled_count = sum(1 for _, m in items if m.proxy.enabled and m.proxy.url)
        saved_count = sum(1 for _, m in items if m.proxy.url and not m.proxy.enabled)

        active_proxy_str = "-"
        if active_name and active_name in snapshot.accounts:
            act_p = snapshot.accounts[active_name].proxy
            if act_p.url:
                active_proxy_str = f"{act_p.label or act_p.url} ({'ON' if act_p.enabled else 'OFF'})"

        summary_text = Text()
        summary_text.append("Proxies: ", style="bold")
        summary_text.append(f"{enabled_count} active", style="bold green")
        summary_text.append(" │ ", style="dim")
        summary_text.append(f"{saved_count} saved", style="yellow")
        summary_text.append(" │ Active proxy: ", style="dim")
        summary_text.append(active_proxy_str, style="bold cyan")
        summary.update(summary_text)

        cols = ("sel", "account", "state", "proxy_status", "label", "url", "latency")
        new_account_order = [name for name, _ in items]

        if self.account_order == new_account_order and table.row_count == len(new_account_order):
            for name, meta in items:
                vals = self._row_values(name, meta, active_name)
                for col_key, val in zip(cols, vals, strict=True):
                    table.update_cell(name, col_key, val, update_width=False)
            return

        saved_account = self.get_selected_account_name()
        table.clear()
        self.account_order = new_account_order

        new_cursor_row = 0
        for idx, (name, meta) in enumerate(items):
            if name == saved_account:
                new_cursor_row = idx
            vals = self._row_values(name, meta, active_name)
            table.add_row(*vals, key=name)

        if table.row_count > 0:
            table.cursor_coordinate = Coordinate(new_cursor_row, 0)

    def set_account_latency(self, name: str, result: str) -> None:
        """Update latency/health status for an account proxy."""
        self._proxy_latencies[name] = result

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle proxy action button clicks."""
        btn_id = event.button.id
        if btn_id == "btn-proxy-edit":
            self._handle_proxy_edit()
        elif btn_id == "btn-proxy-toggle":
            self._handle_proxy_toggle()
        elif btn_id == "btn-proxy-clear":
            self._handle_proxy_clear()
        elif btn_id == "btn-proxy-test":
            self._handle_proxy_test()

    def _handle_proxy_edit(self) -> None:
        name = self.get_selected_account_name()
        if not name or not self.snapshot or name not in self.snapshot.accounts:
            self.post_message(StatusMessage("No account selected."))
            return

        current_proxy = self.snapshot.accounts[name].proxy

        def on_proxy_submitted(result: tuple[str, str | None, bool] | None) -> None:
            if not result:
                return
            url, label, enabled = result
            self._set_proxy_worker(name, url, label, enabled)

        self.app.push_screen(
            ProxyModal(account_name=name, proxy=current_proxy),
            on_proxy_submitted,
        )

    @work(thread=True)
    def _set_proxy_worker(self, name: str, url: str, label: str | None, enabled: bool) -> None:
        try:
            set_account_proxy(self.acs_app.paths, name, url=url, label=label, enabled=enabled)
            self.app.call_from_thread(self.post_message, StatusMessage(f"Updated proxy for {name}."))
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self.post_message, StatusMessage(f"Proxy update failed: {exc}"))

    def _handle_proxy_toggle(self) -> None:
        name = self.get_selected_account_name()
        if not name or not self.snapshot or name not in self.snapshot.accounts:
            return
        cur = self.snapshot.accounts[name].proxy
        if not cur.url:
            self.post_message(StatusMessage(f"No proxy configured for {name}."))
            return
        self._toggle_proxy_worker(name, cur.url, cur.label, not cur.enabled)

    @work(thread=True)
    def _toggle_proxy_worker(self, name: str, url: str, label: str, new_enabled: bool) -> None:
        try:
            set_account_proxy(self.acs_app.paths, name, url=url, label=label, enabled=new_enabled)
            self.app.call_from_thread(
                self.post_message,
                StatusMessage(f"Set proxy for {name} to {'enabled' if new_enabled else 'disabled'}."),
            )
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self.post_message, StatusMessage(f"Toggle proxy failed: {exc}"))

    def _handle_proxy_clear(self) -> None:
        name = self.get_selected_account_name()
        if not name:
            return
        self._clear_proxy_worker(name)

    @work(thread=True)
    def _clear_proxy_worker(self, name: str) -> None:
        try:
            clear_account_proxy(self.acs_app.paths, name)
            self.app.call_from_thread(self.post_message, StatusMessage(f"Cleared proxy for {name}."))
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self.post_message, StatusMessage(f"Clear proxy failed: {exc}"))

    def _handle_proxy_test(self) -> None:
        name = self.get_selected_account_name()
        if not name or not self.snapshot or name not in self.snapshot.accounts:
            return
        cur = self.snapshot.accounts[name].proxy
        if not cur.url:
            self.post_message(StatusMessage(f"No proxy configured for {name}."))
            return
        self._test_proxy_worker(name, cur.url)

    @work(thread=True)
    def _test_proxy_worker(self, name: str, url: str) -> None:
        self.app.call_from_thread(self.post_message, StatusMessage(f"Testing proxy for {name}..."))
        ok, result = check_proxy_health(url)
        status_msg = f"Proxy {name}: {result}" if ok else f"Proxy {name} test failed: {result}"
        self.app.call_from_thread(self._apply_proxy_test_result, name, result, status_msg)

    def _apply_proxy_test_result(self, name: str, result: str, status_msg: str) -> None:
        self.set_account_latency(name, result)
        if self.snapshot:
            self.update_proxies(self.snapshot)
        self.post_message(StatusMessage(status_msg))

"""Header bar widget for the ACS Textual dashboard."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class HeaderBar(Widget):
    """Top summary bar showing system state, active account, and refresh info."""

    active_account: reactive[str | None] = reactive(None)
    account_count: reactive[int] = reactive(0)
    refresh_interval: reactive[int] = reactive(5)
    switch_mode: reactive[str] = reactive("manual")
    sort_mode: reactive[str] = reactive("name")

    def compose(self) -> ComposeResult:
        yield Static(id="header-content")

    def watch_active_account(self, value: str | None) -> None:
        self._refresh_content()

    def watch_account_count(self, value: int) -> None:
        self._refresh_content()

    def watch_refresh_interval(self, value: int) -> None:
        self._refresh_content()

    def watch_switch_mode(self, value: str) -> None:
        self._refresh_content()

    def watch_sort_mode(self, value: str) -> None:
        self._refresh_content()

    def _refresh_content(self) -> None:
        try:
            content_static = self.query_one("#header-content", Static)
        except Exception:
            return

        text = Text()
        text.append(" ACS ", style="bold black on green")
        text.append(" Active: ", style="bold")
        if self.active_account:
            text.append(f"{self.active_account} ", style="bold green")
        else:
            text.append("none ", style="bold red")

        text.append("│ Accounts: ", style="dim")
        text.append(f"{self.account_count} ", style="bold cyan")

        text.append("│ Mode: ", style="dim")
        text.append(f"{self.switch_mode} ", style="bold magenta" if self.switch_mode == "auto" else "yellow")

        text.append("│ Interval: ", style="dim")
        text.append(f"{self.refresh_interval}s ", style="cyan")

        text.append("│ Sort: ", style="dim")
        text.append(f"{self.sort_mode}", style="blue")

        content_static.update(text)

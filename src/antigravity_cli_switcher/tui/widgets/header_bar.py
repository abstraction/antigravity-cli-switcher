"""Header bar widget for the ACS Textual dashboard."""

from __future__ import annotations

from rich.text import Text
from textual import events
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
    sort_mode: reactive[str] = reactive("usage-low")
    quota_backend: reactive[str] = reactive("http")
    candidate_strategy: reactive[str] = reactive("squeeze")

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

    def watch_quota_backend(self, value: str) -> None:
        self._refresh_content()

    def watch_candidate_strategy(self, value: str) -> None:
        self._refresh_content()

    def on_resize(self, event: events.Resize) -> None:
        self._refresh_content()

    def _refresh_content(self) -> None:
        try:
            content_static = self.query_one("#header-content", Static)
        except Exception:
            return

        text = Text()
        text.append(" ACS ", style="bold black on green")

        active_label = self.active_account or "none"
        active_style = "bold green" if self.active_account else "bold red"
        mode_style = "bold magenta" if self.switch_mode == "auto" else "yellow"
        width = self.size.width

        backend_style = "bold cyan" if self.quota_backend == "http" else "bold yellow"

        if 0 < width < 55:
            # Ultra-compact mode for very narrow mobile screens
            text.append(f" {active_label} ", style=active_style)
            text.append("│ ", style="dim")
            text.append(f"{self.quota_backend} ", style=backend_style)
            text.append("│ ", style="dim")
            text.append(self.switch_mode, style=mode_style)
        elif 55 <= width < 90:
            # Compact mode for 80-column terminals
            text.append(f" {active_label} ", style=active_style)
            text.append("│ ", style="dim")
            text.append(f"{self.switch_mode} ", style=mode_style)
            text.append("│ ", style="dim")
            text.append(f"{self.quota_backend} ", style=backend_style)
            text.append("│ ", style="dim")
            text.append(f"{self.candidate_strategy} ", style="bold blue")
            text.append("│ ", style="dim")
            text.append(f"{self.refresh_interval}s ", style="cyan")
            text.append("│ ", style="dim")
            text.append(self.sort_mode, style="blue")
        else:
            # Full verbose format
            text.append(" Active: ", style="bold")
            text.append(f"{active_label} ", style=active_style)
            text.append("│ Accounts: ", style="dim")
            text.append(f"{self.account_count} ", style="bold cyan")
            text.append("│ Mode: ", style="dim")
            text.append(f"{self.switch_mode} ", style=mode_style)
            text.append("│ Quota: ", style="dim")
            text.append(f"{self.quota_backend} ", style=backend_style)
            text.append("│ Strategy: ", style="dim")
            text.append(f"{self.candidate_strategy} ", style="bold blue")
            text.append("│ Interval: ", style="dim")
            text.append(f"{self.refresh_interval}s ", style="cyan")
            text.append("│ Sort: ", style="dim")
            text.append(self.sort_mode, style="blue")

        content_static.update(text)

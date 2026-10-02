"""Bottom status bar widget for the ACS Textual dashboard."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Status bar showing operational messages and worker status."""

    message: reactive[str] = reactive("Ready.")
    worker_status: reactive[str | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static(id="status-message")

    def watch_message(self, value: str) -> None:
        self._refresh_content()

    def watch_worker_status(self, value: str | None) -> None:
        self._refresh_content()

    def _refresh_content(self) -> None:
        try:
            status_static = self.query_one("#status-message", Static)
        except Exception:
            return

        text = Text()
        if self.worker_status:
            text.append(f"[{self.worker_status}] ", style="bold cyan")
        text.append(self.message, style="bold white")
        status_static.update(text)

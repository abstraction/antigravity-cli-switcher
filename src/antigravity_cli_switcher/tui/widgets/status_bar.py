"""Bottom status bar widget for the ACS Textual dashboard."""

from __future__ import annotations

from rich.text import Text
from textual import events
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

    def on_mount(self) -> None:
        self.tooltip = "ACS Status Bar: Displays active background worker progress, quota polling events, and errors."

    def on_resize(self, event: events.Resize) -> None:
        self._refresh_content()

    def _refresh_content(self) -> None:
        try:
            status_static = self.query_one("#status-message", Static)
        except Exception:
            return

        text = Text()
        msg = self.message
        width = self.size.width

        if "fail" in msg.lower() or "error" in msg.lower() or "blocked" in msg.lower():
            text.append("✖ ", style="bold #f85149")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #f85149")
            text.append(msg, style="bold #f85149")
        elif msg.startswith("Ready"):
            text.append("● ", style="bold #3fb950")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #58a6ff")
            text.append(msg, style="#8b949e")
        else:
            text.append("◐ ", style="bold #58a6ff")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #58a6ff")
            text.append(msg, style="#e6edf3")

        hint_text = Text("[1-6] Tabs  [↑↓] Select", style="#8b949e")
        if width > 0:
            pad = width - text.cell_len - hint_text.cell_len - 2
            if pad > 2:
                text.append(" " * pad)
                text.append_text(hint_text)

        status_static.update(text)

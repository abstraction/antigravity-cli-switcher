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
            text.append("✖ ", style="bold #dc4c4c")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #dc4c4c")
            text.append(msg, style="bold #dc4c4c")
        elif msg.startswith("Ready"):
            text.append("● ", style="bold #77ca9b")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #4897d4")
            text.append(msg, style="#888888")
        else:
            text.append("◐ ", style="bold #4897d4")
            if self.worker_status:
                text.append(f"[{self.worker_status}] ", style="bold #4897d4")
            text.append(msg, style="#cccccc")

        hint_text = Text("[1-6] Tabs ╎ [↑↓] Select", style="#666666")
        if width > 0:
            pad = width - text.cell_len - hint_text.cell_len - 2
            if pad > 2:
                text.append(" " * pad)
                text.append_text(hint_text)

        status_static.update(text)

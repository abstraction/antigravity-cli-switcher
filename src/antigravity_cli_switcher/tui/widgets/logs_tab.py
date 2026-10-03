"""Logs tab widget for real-time log tailing and filtering in ACS Textual application."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, RichLog, Static

from antigravity_cli_switcher.manager import ManagerPaths

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp

LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[([A-Z]+)\]\s+([^:]+):\s+(.*)$")
DEFAULT_LOG_BUFFER_LINES: int = 10_000


class LogsTab(Widget):
    """Tab widget for real-time tailing and filtering of switcher logs."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.active_level_filter: str = "ALL"
        self._raw_lines: list[str] = []
        self._file_offset: int = 0
        self._log_file: Path | None = None

        self._loaded: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="logs-filter-bar"):
            yield Static("Level: ", id="logs-filter-label")
            yield Button("ALL", id="btn-filter-all", variant="primary")
            yield Button("INFO", id="btn-filter-info", variant="default")
            yield Button("WARNING", id="btn-filter-warning", variant="default")
            yield Button("ERROR", id="btn-filter-error", variant="default")
            yield Button("Clear", id="btn-logs-clear", variant="default")
            yield Static("", id="logs-path-info")
        yield RichLog(
            id="logs-rich-log",
            highlight=False,
            markup=False,
            wrap=True,
            auto_scroll=True,
            max_lines=None,
        )

    def on_mount(self) -> None:
        if self.acs_app.initial_tab == "tab-logs":
            self.load_initial_logs()
            self._loaded = True

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def _resolve_log_file(self, paths: ManagerPaths | None = None) -> Path:
        if self._log_file is not None:
            return self._log_file
        if paths is not None:
            log_path = paths.root / "logs" / "manager.log"
        else:
            log_path = self.acs_app.paths.root / "logs" / "manager.log"
        self._log_file = log_path
        return log_path

    def load_initial_logs(self, paths: ManagerPaths | None = None) -> None:
        """Read existing log file lines into memory and render them."""
        log_file = self._resolve_log_file(paths)
        path_info = self.query_one("#logs-path-info", Static)
        path_info.update(f"Log: {log_file}")

        if not log_file.is_file():
            self._raw_lines = ["No log file found at " + str(log_file)]
            self._render_filtered_lines()
            return

        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                self._file_offset = f.tell()
            # Keep up to last DEFAULT_LOG_BUFFER_LINES
            self._raw_lines = lines[-DEFAULT_LOG_BUFFER_LINES:]
            self._render_filtered_lines()
        except Exception as exc:
            self._raw_lines = [f"Error reading log file: {exc}"]
            self._render_filtered_lines()

    def poll_new_logs(self, paths: ManagerPaths | None = None) -> None:
        """Tail newly appended lines from log file."""
        if not self._loaded:
            self.load_initial_logs(paths)
            self._loaded = True
            return

        log_file = self._resolve_log_file(paths)
        if not log_file.is_file():
            return

        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                cur_size = f.tell()
                if cur_size < self._file_offset:
                    # File was rotated/truncated
                    self._file_offset = 0
                f.seek(self._file_offset)
                new_lines = f.readlines()
                self._file_offset = f.tell()

            if not new_lines:
                return

            rich_log = self.query_one("#logs-rich-log", RichLog)
            for line in new_lines:
                self._raw_lines.append(line)
                if len(self._raw_lines) > DEFAULT_LOG_BUFFER_LINES:
                    self._raw_lines.pop(0)

                if self._matches_filter(line):
                    rich_log.write(self._format_line(line))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-filter-all":
            self.set_filter_level("ALL")
        elif btn_id == "btn-filter-info":
            self.set_filter_level("INFO")
        elif btn_id == "btn-filter-warning":
            self.set_filter_level("WARNING")
        elif btn_id == "btn-filter-error":
            self.set_filter_level("ERROR")
        elif btn_id == "btn-logs-clear":
            rich_log = self.query_one("#logs-rich-log", RichLog)
            rich_log.clear()

    def set_filter_level(self, level: str) -> None:
        """Update active filter level and re-render."""
        self.active_level_filter = level
        # Update button variants
        levels = ["ALL", "INFO", "WARNING", "ERROR"]
        for lvl in levels:
            btn = self.query_one(f"#btn-filter-{lvl.lower()}", Button)
            btn.variant = "primary" if lvl == level else "default"
        self._render_filtered_lines()

    def _matches_filter(self, line: str) -> bool:
        if self.active_level_filter == "ALL":
            return True
        match = LOG_LINE_RE.match(line)
        if match:
            level = match.group(2)
            if self.active_level_filter == "INFO":
                return level in ("INFO", "WARNING", "ERROR")
            if self.active_level_filter == "WARNING":
                return level in ("WARNING", "ERROR")
            if self.active_level_filter == "ERROR":
                return level == "ERROR"
        return True

    def _format_line(self, line: str) -> Text:
        match = LOG_LINE_RE.match(line.rstrip("\r\n"))
        if not match:
            return Text(line.rstrip("\r\n"))

        ts, level, name, msg = match.groups()
        t = Text()
        t.append(ts, style="dim")
        t.append(" [")

        if level == "ERROR":
            t.append(level, style="bold red")
        elif level == "WARNING":
            t.append(level, style="bold yellow")
        elif level == "INFO":
            t.append(level, style="bold cyan")
        else:
            t.append(level, style="dim")

        t.append("] ")
        t.append(f"{name}: ", style="dim")

        msg_style = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "white")
        t.append(msg, style=msg_style)
        return t

    def _render_filtered_lines(self) -> None:
        rich_log = self.query_one("#logs-rich-log", RichLog)
        rich_log.clear()
        for line in self._raw_lines:
            if self._matches_filter(line):
                rich_log.write(self._format_line(line))

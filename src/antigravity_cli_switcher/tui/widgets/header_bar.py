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

    def on_mount(self) -> None:
        self.tooltip = "Status: Active account, switch mode, quota backend, and sync interval."

    def _refresh_content(self) -> None:
        try:
            content_static = self.query_one("#header-content", Static)
        except Exception:
            return

        text = Text()
        text.append(" ACS ", style="bold #ffffff on #1c2128")

        active_label = self.active_account or "none"
        active_dot = "● " if self.active_account else "○ "
        active_style = "bold #77ca9b" if self.active_account else "bold #dc4c4c"
        mode_style = "bold #9b82d4" if self.switch_mode == "auto" else "#cbc06c"
        backend_style = "bold #4897d4" if self.quota_backend == "http" else "bold #cbc06c"
        sep_style = "#444444"
        lbl_style = "#777777"
        val_style = "#cccccc"
        width = self.size.width

        # Always include active account token
        text.append(" ")
        text.append(f"{active_dot}{active_label}", style=active_style)

        # Build candidate tokens in priority order
        candidates: list[tuple[str, str]] = []
        if width >= 105:
            candidates.append((f"Accounts: {self.account_count}", val_style))
            candidates.append((f"Mode: {self.switch_mode}", mode_style))
            candidates.append((f"Quota: {self.quota_backend}", backend_style))
            candidates.append((f"Strategy: {self.candidate_strategy}", val_style))
            candidates.append((f"Interval: {self.refresh_interval}s", val_style))
            candidates.append((f"Sort: {self.sort_mode}", lbl_style))
        elif width >= 70:
            candidates.append((f"{self.account_count} accs", val_style))
            candidates.append((self.switch_mode, mode_style))
            candidates.append((self.quota_backend, backend_style))
            candidates.append((self.candidate_strategy, val_style))
            candidates.append((f"{self.refresh_interval}s", val_style))
            candidates.append((self.sort_mode, lbl_style))
        else:
            candidates.append((self.quota_backend, backend_style))
            candidates.append((self.switch_mode, mode_style))

        # Dynamically append tokens that fit comfortably within width
        current_len = text.cell_len
        target_max = max(width - 2, 20) if width > 0 else 120

        for tok_str, tok_style in candidates:
            needed = 3 + len(tok_str)  # " ╎ " + string
            if width > 0 and (current_len + needed) > target_max:
                break
            text.append(" ╎ ", style=sep_style)
            text.append(tok_str, style=tok_style)
            current_len += needed

        content_static.update(text)

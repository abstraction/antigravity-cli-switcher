"""History tab widget for ACS Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Static

from antigravity_cli_switcher.models import StatusSnapshot

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp


class HistoryTab(Widget):
    """Tab widget displaying recent account switch and auto-failover audit logs."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)

        self._last_history_len: int = -1
        self._last_active: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Switch log: loading...", id="history-summary")
        table: DataTable[Text | str] = DataTable(
            id="history-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_column("#", key="idx", width=3)
        table.add_column("Timestamp", key="timestamp", width=19)
        table.add_column("From", key="from_acc", width=12)
        table.add_column("To", key="to_acc", width=12)
        table.add_column("Outcome", key="outcome", width=9)
        table.add_column("Trigger", key="trigger", width=10)
        table.add_column("Reason", key="reason", width=18)
        table.add_column("Details", key="req_id", width=12)

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def update_history(self, snapshot: StatusSnapshot) -> None:
        """Update history table from snapshot switch history."""
        table = self.query_one("#history-table", DataTable)
        summary = self.query_one("#history-summary", Static)

        history = snapshot.switch_history
        total_events = len(history)

        summary_text = Text()
        summary_text.append("Switch log: ", style="bold")
        summary_text.append(f"{total_events} events", style="bold cyan")
        summary_text.append(" │ Active: ", style="dim")
        summary_text.append(snapshot.active or "-", style="bold green")
        summary_text.append(" │ Mode: ", style="dim")
        summary_text.append(snapshot.switch_mode.upper(), style="bold yellow")
        summary.update(summary_text)

        if total_events == self._last_history_len and snapshot.active == self._last_active:
            return
        self._last_history_len = total_events
        self._last_active = snapshot.active

        table.clear()
        if not history:
            return

        # Show most recent events first
        for idx, event in enumerate(reversed(history), start=1):
            idx_text = Text(str(idx), style="dim")
            ts_text = Text(event.at or "-", style="cyan")
            from_text = Text(event.previous_active or "-", style="dim")
            to_acc = event.switched_to or event.active or "-"
            to_text = Text(to_acc, style="bold green" if to_acc == snapshot.active else "white")

            outcome = event.outcome or "unknown"
            if outcome == "success":
                outcome_text = Text("SUCCESS", style="bold green")
            elif outcome in ("failed", "error"):
                outcome_text = Text("FAILED", style="bold red")
            else:
                outcome_text = Text(outcome.upper(), style="yellow")

            trigger_text = Text(event.trigger or "-", style="magenta")
            reason_text = Text(event.reason or "-", style="white")

            detail_str = event.request_id or (f"cooldown={event.cooldown_minutes}m" if event.cooldown_minutes else "-")
            detail_text = Text(detail_str, style="dim")

            table.add_row(
                idx_text,
                ts_text,
                from_text,
                to_text,
                outcome_text,
                trigger_text,
                reason_text,
                detail_text,
            )

"""Account table widget using Textual DataTable."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    HealthStatus,
    ProblemStatus,
    SnapshotVerification,
    StatusSnapshot,
)
from antigravity_cli_switcher.tui.formatters import (
    format_countdown,
    format_last_error,
    format_model_usage,
    format_next_refresh,
)
from antigravity_cli_switcher.tui.theme import (
    format_health_badge,
    format_plan_badge,
    format_state,
)
from antigravity_cli_switcher.tui.usage_scoring import usage_sort_key


class AccountTable(DataTable[Text | str]):
    """Responsive DataTable for managing Antigravity accounts."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            cursor_type="row",
            zebra_stripes=True,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.account_order: list[str] = []

    def on_mount(self) -> None:
        self.add_column("Sel", key="sel", width=4)
        self.add_column("Account", key="name", width=22)
        self.add_column("State", key="state", width=10)
        self.add_column("Plan", key="plan", width=8)
        self.add_column("Health", key="health", width=8)
        self.add_column("Gemini (S/W)", key="gemini", width=14)
        self.add_column("Claude (S/W)", key="claude", width=14)
        self.add_column("Reset (S/W)", key="reset", width=18)
        self.add_column("Next", key="next", width=8)
        self.add_column("Last Error", key="error", width=22)

    def get_selected_account_name(self) -> str | None:
        """Return the name of the currently selected/highlighted account."""
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self.account_order):
            return self.account_order[self.cursor_row]
        return None

    def _row_values(
        self,
        name: str,
        meta: AccountMeta,
        ver: AccountVerification | None,
        active_name: str | None,
        now: datetime,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text, Text, Text]:
        if ver is not None:
            prob_status = ver.problem_status.value
        elif meta.health_status == HealthStatus.INELIGIBLE or meta.stored_health_status == HealthStatus.INELIGIBLE:
            prob_status = ProblemStatus.INELIGIBLE.value
        else:
            prob_status = "ok"
        state_str = "active" if name == active_name else ("disabled" if not meta.enabled else "standby")

        marker = Text(" ★", style="bold green") if name == active_name else Text("")

        name_style = "bold green" if name == active_name else ("dim italic" if not meta.enabled else "bold cyan")
        name_text = Text(name, style=name_style)

        state_badge = format_state(state_str)
        plan_badge = format_plan_badge(meta.plan_type)
        health_badge = format_health_badge(prob_status)

        if not meta.enabled:
            gemini_str = Text("-", style="dim")
            claude_str = Text("-", style="dim")
            reset_str = Text("-", style="dim")
            next_str = Text("-", style="dim")
            err_str = Text("-", style="dim")
        else:
            gemini_str = Text(format_model_usage(meta, "gemini"))
            claude_str = Text(format_model_usage(meta, "claude"))
            reset_str = Text(format_countdown(meta, now), style="dim")
            next_str = Text(format_next_refresh(meta, now), style="cyan")
            err_str = Text(format_last_error(meta), style="dim red" if meta.last_error else "dim")

        return (
            marker,
            name_text,
            state_badge,
            plan_badge,
            health_badge,
            gemini_str,
            claude_str,
            reset_str,
            next_str,
            err_str,
        )

    def update_accounts(
        self,
        snapshot: StatusSnapshot,
        verification: SnapshotVerification,
        sort_mode: str = "name",
    ) -> None:
        """Update table rows from snapshot and verification data."""
        now = datetime.now(timezone.utc)
        active_name = snapshot.active
        items = list(snapshot.accounts.items())

        if sort_mode == "name":
            items.sort(key=lambda pair: pair[0].lower())
        elif sort_mode == "state":
            items.sort(key=lambda pair: (pair[0] != active_name, not pair[1].enabled, pair[0]))
        elif sort_mode == "health":
            items.sort(
                key=lambda pair: (
                    verification.accounts.get(pair[0], None) is not None
                    and verification.accounts[pair[0]].problem_status == "ok",
                    pair[0],
                )
            )
        elif sort_mode == "usage-low":
            items.sort(
                key=lambda pair: usage_sort_key(
                    pair[0],
                    pair[1],
                    verification.accounts.get(pair[0]) if verification is not None else None,
                    now,
                    descending=False,
                )
            )
        elif sort_mode == "usage-high":
            items.sort(
                key=lambda pair: usage_sort_key(
                    pair[0],
                    pair[1],
                    verification.accounts.get(pair[0]) if verification is not None else None,
                    now,
                    descending=True,
                )
            )

        new_account_order = [name for name, _ in items]
        cols = ("sel", "name", "state", "plan", "health", "gemini", "claude", "reset", "next", "error")

        # In-place cell update if account order and count match existing rows
        if self.account_order == new_account_order and self.row_count == len(new_account_order):
            for name, meta in items:
                ver = verification.accounts.get(name)
                vals = self._row_values(name, meta, ver, active_name, now)
                for col_key, val in zip(cols, vals, strict=True):
                    self.update_cell(name, col_key, val, update_width=False)
            return

        saved_account = self.get_selected_account_name()
        self.clear()
        self.account_order = new_account_order

        new_cursor_row = 0
        for idx, (name, meta) in enumerate(items):
            if name == saved_account:
                new_cursor_row = idx

            ver = verification.accounts.get(name)
            vals = self._row_values(name, meta, ver, active_name, now)
            self.add_row(*vals, key=name)

        if self.row_count > 0:
            self.cursor_coordinate = Coordinate(new_cursor_row, 0)

"""Account table widget using Textual DataTable."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from rich.text import Text
from textual import events
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
from antigravity_cli_switcher.tui.usage_scoring import (
    calculate_account_usage_score,
    usage_sort_key,
)

COLUMN_CONFIG: Final[dict[str, tuple[str, int]]] = {
    "sel": ("Sel", 3),
    "name": ("Account", 16),
    "state": ("State", 8),
    "plan": ("Plan", 6),
    "health": ("Health", 6),
    "eff": ("Eff %", 5),
    "gemini": ("Gemini (S/W)", 12),
    "claude": ("Claude (S/W)", 12),
    "reset": ("Reset (S/W)", 16),
    "next": ("Next", 8),
    "error": ("Last Error", 14),
}

ALL_COL_KEYS: Final[list[str]] = [
    "sel",
    "name",
    "state",
    "plan",
    "health",
    "eff",
    "gemini",
    "claude",
    "reset",
    "next",
    "error",
]

COMPACT_COL_KEYS: Final[list[str]] = [
    "sel",
    "name",
    "state",
    "plan",
    "health",
    "eff",
    "gemini",
    "claude",
    "next",
]

MOBILE_COL_KEYS: Final[list[str]] = [
    "sel",
    "name",
    "state",
    "health",
    "eff",
    "gemini",
    "next",
]


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
        self._current_col_keys: list[str] = []
        self._last_snapshot: StatusSnapshot | None = None
        self._last_verification: SnapshotVerification | None = None
        self._last_sort_mode: str = "usage-low"

    def _get_column_keys_for_width(self, width: int) -> list[str]:
        if 0 < width < 75:
            return MOBILE_COL_KEYS
        if 75 <= width < 105:
            return COMPACT_COL_KEYS
        return ALL_COL_KEYS

    def _setup_columns(self, col_keys: list[str]) -> None:
        self.clear(columns=True)
        for key in col_keys:
            label, width = COLUMN_CONFIG[key]
            self.add_column(label, key=key, width=width)
        self._current_col_keys = list(col_keys)

    def on_mount(self) -> None:
        target_keys = self._get_column_keys_for_width(self.size.width)
        self._setup_columns(target_keys)

    def on_resize(self, event: events.Resize) -> None:
        target_keys = self._get_column_keys_for_width(event.size.width)
        if (
            target_keys != self._current_col_keys
            and self._last_snapshot is not None
            and self._last_verification is not None
        ):
            self.update_accounts(self._last_snapshot, self._last_verification, self._last_sort_mode)

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
        is_zombie: bool = False,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text, Text, Text, Text]:
        if ver is not None:
            prob_status = ver.problem_status.value
        elif meta.health_status == HealthStatus.INELIGIBLE or meta.stored_health_status == HealthStatus.INELIGIBLE:
            prob_status = ProblemStatus.INELIGIBLE.value
        elif (
            meta.health_status == HealthStatus.OAUTH_ROTATED or meta.stored_health_status == HealthStatus.OAUTH_ROTATED
        ):
            prob_status = ProblemStatus.OAUTH_ROTATED.value
        else:
            prob_status = "ok"
        state_str = "active" if name == active_name else ("disabled" if not meta.enabled else "standby")

        marker = Text(" ★", style="bold green") if name == active_name else Text("")

        name_style = "bold green" if name == active_name else ("dim italic" if not meta.enabled else "bold cyan")
        name_text = Text(name, style=name_style)

        state_badge = format_state(state_str)
        plan_badge = format_plan_badge(meta.plan_type)
        if is_zombie and prob_status in ("ok", "ready", "unknown"):
            health_badge = Text("ZOMBIE", style="bold red")
        else:
            health_badge = format_health_badge(prob_status)

        if not meta.enabled:
            eff_str = Text("-", style="dim")
            gemini_str = Text("-", style="dim")
            claude_str = Text("-", style="dim")
            reset_str = Text("-", style="dim")
            next_str = Text("-", style="dim")
            err_str = Text("-", style="dim")
        else:
            score = calculate_account_usage_score(meta, now)
            if not score.has_known_quota:
                eff_str = Text("-", style="dim")
            else:
                eff_val = score.effective_quota
                eff_style = "bold green" if eff_val > 50 else ("bold yellow" if eff_val > 20 else "bold red")
                eff_str = Text(f"{round(eff_val, 1):g}%", style=eff_style)

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
            eff_str,
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
        sort_mode: str = "usage-low",
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
        target_keys = self._get_column_keys_for_width(self.size.width)

        # In-place cell update if account order, count, and column layout match
        if (
            self._current_col_keys == target_keys
            and self.account_order == new_account_order
            and self.row_count == len(new_account_order)
        ):
            for name, meta in items:
                ver = verification.accounts.get(name)
                is_z = (
                    bool(snapshot.fleet_utilization.accounts[name].is_zombie)
                    if name in snapshot.fleet_utilization.accounts
                    else False
                )
                vals = self._row_values(name, meta, ver, active_name, now, is_z)
                val_dict = dict(zip(ALL_COL_KEYS, vals, strict=True))
                for col_key in self._current_col_keys:
                    self.update_cell(name, col_key, val_dict[col_key], update_width=False)
            return

        if self._current_col_keys != target_keys:
            self._setup_columns(target_keys)

        saved_account = self.get_selected_account_name()
        self.clear()
        self.account_order = new_account_order

        new_cursor_row = 0
        for idx, (name, meta) in enumerate(items):
            if name == saved_account:
                new_cursor_row = idx

            ver = verification.accounts.get(name)
            is_z = (
                bool(snapshot.fleet_utilization.accounts[name].is_zombie)
                if name in snapshot.fleet_utilization.accounts
                else False
            )
            vals = self._row_values(name, meta, ver, active_name, now, is_z)
            val_dict = dict(zip(ALL_COL_KEYS, vals, strict=True))
            row_cells = [val_dict[k] for k in self._current_col_keys]
            self.add_row(*row_cells, key=name)

        if self.row_count > 0:
            self.cursor_coordinate = Coordinate(new_cursor_row, 0)

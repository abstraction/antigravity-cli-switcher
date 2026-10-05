"""Fleet utilization and financial rightsizing tab widget for ACS Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import DataTable, Static

from antigravity_cli_switcher.manager.fleet_analytics import compute_fleet_insight
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountUtilizationRecord,
    FleetArchetype,
    FleetInsight,
    StatusSnapshot,
)

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp


class FleetTab(Widget):
    """Tab widget displaying fleet capacity, rightsizing insights, and zombie account audits."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.account_order: list[str] = []
        self.snapshot: StatusSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Static("Fleet summary: loading...", id="fleet-summary")
        yield Static(
            "[bold #4897d4](i)[/]  Peak Burst: max concurrent active accounts ╎ Burnt %: 7d quota consumed ╎ Zombie: 0% 7d usage",
            id="fleet-legend",
        )
        table: DataTable[Text | str] = DataTable(
            id="fleet-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield table
        yield Static("Recommendations: loading...", id="fleet-recommendation")

    def on_mount(self) -> None:
        summary = self.query_one("#fleet-summary", Static)
        summary.tooltip = (
            "Fleet metrics:\n"
            "• Archetype: 7-day usage pattern\n"
            "• Spend: Total monthly account cost\n"
            "• Waste: Monthly cost of idle accounts\n"
            "• Peak Burst: Peak concurrent active accounts\n"
            "• Recommended Size: Account count needed for peak burst"
        )
        legend = self.query_one("#fleet-legend", Static)
        legend.tooltip = (
            "Metrics:\n"
            "• Peak Burst: Max concurrent accounts active during load\n"
            "• Burnt %: Total quota percentage consumed over 7 days\n"
            "• Min Headroom %: Lowest recorded quota percentage before reset\n"
            "• Zombie: Accounts with 0% duty cycle and 0% quota consumed"
        )
        rec = self.query_one("#fleet-recommendation", Static)
        rec.tooltip = "Fleet recommendation based on trailing utilization."
        table = self.query_one("#fleet-table", DataTable)
        table.tooltip = "Account duty cycles, quota consumption, and zombie status."
        table.add_column("Account", key="account", width=12)
        table.add_column("Status", key="status", width=7)
        table.add_column("7D Duty %", key="duty", width=9)
        table.add_column("Gemini Burnt %", key="gemini_burnt", width=14)
        table.add_column("Claude Burnt %", key="claude_burnt", width=14)
        table.add_column("Min Headroom %", key="min_headroom", width=14)
        table.add_column("Monthly Cost", key="cost", width=12)
        table.add_column("Zombie Flag", key="zombie", width=11)
        self._adjust_column_widths(self.size.width)

    def on_resize(self, event: events.Resize) -> None:
        self._adjust_column_widths(event.size.width)
        if self.snapshot is not None:
            self.update_fleet(self.snapshot)

    def _adjust_column_widths(self, width: int) -> None:
        try:
            table = self.query_one("#fleet-table", DataTable)
        except Exception:
            return
        if not table.columns:
            return
        if 0 < width < 112:
            widths = {
                "account": 10 if width < 98 else 11,
                "status": 6,
                "duty": 8,
                "gemini_burnt": 10 if width < 98 else 12,
                "claude_burnt": 10 if width < 98 else 12,
                "min_headroom": 10 if width < 98 else 12,
                "cost": 8 if width < 98 else 10,
                "zombie": 8 if width < 98 else 10,
            }
        else:
            widths = {
                "account": 12,
                "status": 7,
                "duty": 9,
                "gemini_burnt": 14,
                "claude_burnt": 14,
                "min_headroom": 14,
                "cost": 12,
                "zombie": 11,
            }
        for col_key, col in table.columns.items():
            key_val = str(col_key.value) if col_key and col_key.value is not None else ""
            if key_val in widths:
                col.width = widths[key_val]

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def get_selected_account_name(self) -> str | None:
        """Return the name of the currently selected account in the table."""
        table = self.query_one("#fleet-table", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self.account_order):
            return self.account_order[table.cursor_row]
        return None

    def _row_values(
        self,
        name: str,
        status: str,
        is_active: bool,
        duty_pct: float,
        gemini_burnt: float,
        claude_burnt: float,
        min_headroom: float,
        cost_usd: float,
        is_zombie: bool,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text]:
        marker = " ★" if is_active else ""
        name_style = "bold #77ca9b" if is_active else "bold #4897d4"
        name_text = Text(f"{name}{marker}", style=name_style)

        status_style = "#77ca9b" if status == "active" else ("dim" if status == "disabled" else "#cbc06c")
        status_text = Text(status, style=status_style)

        duty_style = "bold #77ca9b" if duty_pct > 15 else ("#77ca9b" if duty_pct > 0 else "dim")
        duty_text = Text(f"{duty_pct:>5.1f}%", style=duty_style)

        g_style = "bold #cbc06c" if gemini_burnt > 50 else ("#cccccc" if gemini_burnt > 0 else "dim")
        g_text = Text(f"{round(gemini_burnt):>3}%", style=g_style)

        c_style = "bold #cbc06c" if claude_burnt > 50 else ("#cccccc" if claude_burnt > 0 else "dim")
        c_text = Text(f"{round(claude_burnt):>3}%", style=c_style)

        h_style = "bold #dc4c4c" if min_headroom < 20 else ("bold #cbc06c" if min_headroom < 50 else "bold #77ca9b")
        h_text = Text(f"{round(min_headroom):>3}%", style=h_style)

        cost_text = Text(f"${cost_usd:.0f}/mo", style="#cccccc")

        z_text = Text("ZOMBIE", style="bold #dc4c4c") if is_zombie else Text("NO", style="dim")

        return (
            name_text,
            status_text,
            duty_text,
            g_text,
            c_text,
            h_text,
            cost_text,
            z_text,
        )

    def _extract_account_row_data(
        self,
        name: str,
        meta: AccountMeta | None,
        rec: AccountUtilizationRecord | None,
        active_name: str | None,
        insight: FleetInsight,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text]:
        is_active = name == active_name
        status = (
            "active"
            if is_active
            else ("disabled" if (meta and not meta.enabled) else (meta.status if meta else "standby"))
        )
        duty_pct = round((rec.rolling_7d_active_seconds / 604800.0) * 100.0, 1) if rec else 0.0
        g_burnt = rec.rolling_7d_gemini_consumed if rec else 0.0
        c_burnt = rec.rolling_7d_other_consumed if rec else 0.0
        min_hd = min(rec.rolling_7d_min_gemini_headroom, rec.rolling_7d_min_other_headroom) if rec else 100.0
        cost = rec.monthly_cost_usd if rec else 20.0
        is_z = (name in insight.zombie_accounts) or (rec.is_zombie if rec else False)

        return self._row_values(
            name=name,
            status=status,
            is_active=is_active,
            duty_pct=duty_pct,
            gemini_burnt=g_burnt,
            claude_burnt=c_burnt,
            min_headroom=min_hd,
            cost_usd=cost,
            is_zombie=is_z,
        )

    def update_fleet(self, snapshot: StatusSnapshot) -> None:
        """Update fleet metrics and accounts table from latest status snapshot."""
        self.snapshot = snapshot
        table = self.query_one("#fleet-table", DataTable)
        summary = self.query_one("#fleet-summary", Static)
        recommendation = self.query_one("#fleet-recommendation", Static)

        state_dict: dict[str, object] = {
            "accounts": {name: meta.model_dump() for name, meta in snapshot.accounts.items()},
            "fleet_utilization": snapshot.fleet_utilization.model_dump(),
        }
        insight: FleetInsight = compute_fleet_insight(state_dict)

        # 1. Header / Summary cards
        width = self.size.width
        s_text = Text()
        s_text.append("Archetype: ", style="#8b949e")
        s_text.append(f"{insight.archetype.value.upper()} ", style="bold #bc8cff")
        s_text.append("│ ", style="#30363d")
        s_text.append("Spend: ", style="#8b949e")
        s_text.append(f"${insight.estimated_monthly_spend_usd:.0f}/mo ", style="bold #3fb950")
        s_text.append("│ ", style="#30363d")
        s_text.append("Waste: ", style="#8b949e")
        waste_style = "bold #f85149" if insight.estimated_monthly_waste_usd > 0 else "bold #3fb950"
        s_text.append(f"${insight.estimated_monthly_waste_usd:.0f}/mo ", style=waste_style)
        s_text.append("│ ", style="#30363d")
        s_text.append("Peak Burst: ", style="#8b949e")
        s_text.append(f"{insight.peak_burst_depth} ", style="bold #d29922")
        s_text.append("│ ", style="#30363d")
        s_text.append("Recommended Size: ", style="#8b949e")
        s_text.append(f"{insight.recommended_fleet_size} ", style="bold #58a6ff")
        if width <= 0 or width >= 105:
            s_text.append(f"(current: {insight.total_accounts})", style="#8b949e")
        elif width >= 90:
            s_text.append(f"(cur: {insight.total_accounts})", style="#8b949e")
        summary.update(s_text)

        # 2. Recommendations panel
        r_text = Text()
        r_text.append("Recommendation: ", style="bold #d29922")
        r_text.append(
            insight.recommendation_summary or "Fleet capacity is well-matched to current workload.",
            style="#e6edf3",
        )
        if insight.workload_ramp_viable and insight.archetype != FleetArchetype.QUOTA_GRINDER:
            extra = " │ Safe to ramp off-peak subagent workloads"
            if width <= 0 or r_text.cell_len + len(extra) < width - 2:
                r_text.append(extra, style="bold #3fb950")
        if insight.potential_annual_savings_usd > 0:
            extra = f" │ Potential savings: ${insight.potential_annual_savings_usd:.0f}/yr"
            if width <= 0 or r_text.cell_len + len(extra) < width - 2:
                r_text.append(extra, style="bold #3fb950")
        recommendation.update(r_text)

        # 3. Accounts table
        self._adjust_column_widths(width)
        fleet_dict = state_dict.get("fleet_utilization")
        raw_accounts = fleet_dict.get("accounts") if isinstance(fleet_dict, dict) else {}
        recalculated_accounts: dict[str, AccountUtilizationRecord] = {}
        if isinstance(raw_accounts, dict):
            for acc_name, raw_rec in raw_accounts.items():
                if isinstance(raw_rec, dict):
                    recalculated_accounts[acc_name] = AccountUtilizationRecord.model_validate(raw_rec)

        all_names = sorted(set(list(snapshot.accounts.keys()) + list(recalculated_accounts.keys())))
        active_name = snapshot.active

        cols = ("account", "status", "duty", "gemini_burnt", "claude_burnt", "min_headroom", "cost", "zombie")

        if self.account_order == all_names and table.row_count == len(all_names):
            for name in all_names:
                meta = snapshot.accounts.get(name)
                rec = recalculated_accounts.get(name)
                vals = self._extract_account_row_data(name, meta, rec, active_name, insight)
                for col_key, val in zip(cols, vals, strict=True):
                    table.update_cell(name, col_key, val, update_width=False)
            return

        saved_account = self.get_selected_account_name()
        table.clear()
        self.account_order = all_names

        new_cursor_row = 0
        for idx, name in enumerate(all_names):
            if name == saved_account:
                new_cursor_row = idx

            meta = snapshot.accounts.get(name)
            rec = recalculated_accounts.get(name)
            vals = self._extract_account_row_data(name, meta, rec, active_name, insight)
            table.add_row(*vals, key=name)

        if table.row_count > 0:
            table.cursor_coordinate = Coordinate(new_cursor_row, 0)

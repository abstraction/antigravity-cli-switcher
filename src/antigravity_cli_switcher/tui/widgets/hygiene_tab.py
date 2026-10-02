"""Hygiene tab widget for ACS Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from antigravity_cli_switcher.manager import fix_hygiene
from antigravity_cli_switcher.models import AccountMeta, AccountVerification, SnapshotVerification, StatusSnapshot
from antigravity_cli_switcher.tui.messages import StatusMessage

if TYPE_CHECKING:
    from antigravity_cli_switcher.tui.app import ACSApp


class HygieneTab(Widget):
    """Tab widget for auditing account health, token integrity, and synthetic token detection."""

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

    def compose(self) -> ComposeResult:
        yield Static("Hygiene audit: loading...", id="hygiene-summary")
        table: DataTable[Text | str] = DataTable(
            id="hygiene-table",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield table
        with Horizontal(id="hygiene-actions"):
            yield Button("Run Fix", id="btn-hygiene-fix", variant="error")
            yield Button("Relogin Account", id="btn-hygiene-relogin", variant="warning")
            yield Button("Refresh", id="btn-hygiene-refresh", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#hygiene-table", DataTable)
        table.add_column("Account", key="account", width=22)
        table.add_column("Status", key="status", width=14)
        table.add_column("Action", key="action", width=14)
        table.add_column("Expected Email", key="expected_email", width=24)
        table.add_column("Token Email", key="token_email", width=24)
        table.add_column("Synthetic", key="synthetic", width=12)
        table.add_column("Expired", key="expired", width=10)
        table.add_column("Summary", key="summary", width=40)

    @property
    def acs_app(self) -> ACSApp:
        return self.app  # type: ignore[return-value]

    def get_selected_account_name(self) -> str | None:
        """Return the name of the currently selected account in the table."""
        table = self.query_one("#hygiene-table", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self.account_order):
            return self.account_order[table.cursor_row]
        return None

    def _row_values(
        self,
        name: str,
        ver: AccountVerification,
        meta: AccountMeta | None,
    ) -> tuple[Text, Text, Text, Text, Text, Text, Text, Text]:
        name_style = "bold cyan"
        if ver.is_synthetic:
            name_style = "bold red"
        elif ver.problem_status != "ok":
            name_style = "yellow"
        name_text = Text(name, style=name_style)

        if ver.is_synthetic:
            status_badge = Text(" CONTAM ", style="bold white on red")
        elif ver.problem_status == "ok":
            status_badge = Text(" OK ", style="bold black on green")
        elif ver.problem_status == "mismatch":
            status_badge = Text(" MISMATCH ", style="bold white on dark_red")
        else:
            status_badge = Text(f" {ver.problem_status.upper()} ", style="bold black on yellow")

        action_text = Text(ver.recommended_action, style="bold magenta" if ver.recommended_action != "none" else "dim")

        exp_email = ver.expected_email or (meta.expected_email if meta else None) or "-"
        tok_email = ver.token_email or "-"
        exp_text = Text(exp_email, style="white" if exp_email != "-" else "dim")
        tok_text = Text(tok_email, style="white" if tok_email != "-" else "dim")

        synth_text = Text("YES", style="bold red") if ver.is_synthetic else Text("No", style="dim")
        exp_flag_text = Text("YES", style="bold yellow") if ver.access_token_expired else Text("No", style="dim")

        issue_summary = ver.summary or "-"
        summary_color = "red" if ver.is_synthetic else ("yellow" if ver.problem_status != "ok" else "dim")
        sum_text = Text(issue_summary, style=summary_color)

        return (
            name_text,
            status_badge,
            action_text,
            exp_text,
            tok_text,
            synth_text,
            exp_flag_text,
            sum_text,
        )

    def update_hygiene(
        self,
        snapshot: StatusSnapshot,
        verification: SnapshotVerification,
    ) -> None:
        """Update hygiene table and summary from snapshot and verification data."""
        table = self.query_one("#hygiene-table", DataTable)
        summary = self.query_one("#hygiene-summary", Static)

        accounts = verification.accounts
        total = len(accounts)
        clean_count = 0
        warn_count = 0
        contam_count = 0

        for info in accounts.values():
            if info.problem_status == "ok":
                clean_count += 1
            elif info.is_synthetic or info.problem_status in ("contaminated", "corrupt"):
                contam_count += 1
            else:
                warn_count += 1

        summary_text = Text()
        summary_text.append("Hygiene: ", style="bold")
        summary_text.append(f"{total} total", style="white")
        summary_text.append(" │ ", style="dim")
        summary_text.append(f"{clean_count} clean", style="bold green")
        summary_text.append(" │ ", style="dim")
        summary_text.append(f"{warn_count} warnings", style="bold yellow")
        summary_text.append(" │ ", style="dim")
        summary_text.append(
            f"{contam_count} contaminated",
            style="bold red" if contam_count > 0 else "dim",
        )

        if contam_count > 0:
            summary_text.append(" │ Synthetic tokens found. Run fix.", style="bold red")

        summary.update(summary_text)

        sorted_names = sorted(accounts.keys(), key=lambda n: n.lower())
        cols = ("account", "status", "action", "expected_email", "token_email", "synthetic", "expired", "summary")

        # In-place cell updates if row structure matches
        if self.account_order == sorted_names and table.row_count == len(sorted_names):
            for name in sorted_names:
                ver = accounts[name]
                meta = snapshot.accounts.get(name)
                vals = self._row_values(name, ver, meta)
                for col_key, val in zip(cols, vals, strict=True):
                    table.update_cell(name, col_key, val, update_width=False)
            return

        saved_account = self.get_selected_account_name()
        table.clear()
        self.account_order = sorted_names

        new_cursor_row = 0
        for idx, name in enumerate(sorted_names):
            if name == saved_account:
                new_cursor_row = idx

            ver = accounts[name]
            meta = snapshot.accounts.get(name)
            vals = self._row_values(name, ver, meta)
            table.add_row(*vals, key=name)

        if table.row_count > 0:
            table.cursor_coordinate = Coordinate(new_cursor_row, 0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle hygiene action button clicks."""
        btn_id = event.button.id
        if btn_id == "btn-hygiene-fix":
            self.action_fix_hygiene()
        elif btn_id == "btn-hygiene-relogin":
            self.action_relogin_selected()
        elif btn_id == "btn-hygiene-refresh":
            self.acs_app.refresh_snapshot()

    def action_relogin_selected(self) -> None:
        """Trigger account relogin for the selected account in the hygiene table."""
        name = self.get_selected_account_name()
        if not name:
            self.post_message(StatusMessage("No account selected in hygiene table."))
            return
        action = getattr(self.screen, "action_relogin", None)
        if callable(action):
            action(name)

    @work(thread=True)
    def action_fix_hygiene(self) -> None:
        """Run hygiene fix in background worker and report status."""
        self.app.call_from_thread(self.post_message, StatusMessage("Running hygiene fix..."))
        try:
            actions = fix_hygiene(self.acs_app.paths)
            if actions:
                msg = f"Applied hygiene fix: {'; '.join(actions)}"
            else:
                msg = "No hygiene issues found."
            self.app.call_from_thread(self.post_message, StatusMessage(msg))
            self.acs_app.refresh_snapshot()
        except Exception as exc:
            self.app.call_from_thread(self.post_message, StatusMessage(f"Hygiene fix failed: {exc}"))

"""Detail panel widget showing rich overview for the highlighted account."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from antigravity_cli_switcher.models import AccountMeta, AccountVerification
from antigravity_cli_switcher.tui.formatters import (
    format_next_refresh,
    format_problem_summary,
    format_window_summary,
)


class DetailPanel(Widget):
    """Panel displaying status of the selected account."""

    def compose(self) -> ComposeResult:
        yield Static("Overview", id="detail-title")
        yield Static(id="detail-content")

    def update_detail(
        self,
        name: str | None,
        meta: AccountMeta | None,
        verification: AccountVerification | None,
    ) -> None:
        """Update the panel contents for the given account."""
        title_static = self.query_one("#detail-title", Static)
        content_static = self.query_one("#detail-content", Static)

        if not name or not meta:
            title_static.update(Text("No account selected", style="bold dim"))
            content_static.update(Text("Select an account to view details.", style="dim italic"))
            return

        now = datetime.now(timezone.utc)
        prob_str = format_problem_summary(
            verification.problem_status if verification else None,
            verification.recommended_action if verification else None,
        )
        rec = verification.recommended_action if verification else "none"
        summary = verification.summary if verification else "Ready for use."
        tok_email = meta.identity.email if meta.identity and meta.identity.email else "-"
        exp_email = meta.expected_email or "-"

        title_text = Text()
        title_text.append("Overview: ", style="bold")
        title_text.append(name, style="bold cyan")
        title_text.append(" │ Health: ", style="dim")
        health_style = (
            "bold green"
            if meta.health_status in ("healthy", "ok", "ready")
            else ("bold red" if meta.health_status in ("ineligible", "auth_missing", "auth_expired") else "bold yellow")
        )
        title_text.append(str(meta.health_status), style=health_style)
        title_static.update(title_text)

        # 2-column key-value text presentation
        gemini_5h = format_window_summary(meta, "short", now, "gemini")
        gemini_wk = format_window_summary(meta, "weekly", now, "gemini")
        other_5h = format_window_summary(meta, "short", now, "other")
        other_wk = format_window_summary(meta, "weekly", now, "other")
        next_ref = f"{format_next_refresh(meta, now)} ({meta.refresh_policy_seconds}s)"
        mode_str = f"{meta.status} │ {'enabled' if meta.enabled else 'disabled'}"
        status_str = str(meta.health_status.value if hasattr(meta.health_status, "value") else meta.health_status)

        lines = [
            ("Token Email", tok_email, "Gemini 5h", gemini_5h),
            ("Exp. Email", exp_email, "Gemini Wk", gemini_wk),
            ("Plan", meta.plan_type or "unknown", "Other 5h", other_5h),
            ("Mode", mode_str, "Other Wk", other_wk),
            ("Next Refresh", next_ref, "Problem", prob_str),
            ("Failures", str(meta.fail_count), "Status", status_str),
        ]

        text = Text()
        for k1, v1, k2, v2 in lines:
            text.append(f"{k1:<13}: ", style="dim bold")
            text.append(f"{v1:<28} ", style="white")
            text.append(f"│ {k2:<10}: ", style="dim bold")
            text.append(f"{v2}\n", style="white")

        if summary and summary != "Ready for use.":
            text.append("Note         : ", style="bold yellow")
            text.append(f"{summary}\n", style="yellow")

        if rec == "human_intervention":
            text.append("Action       : ", style="bold red")
            text.append("Requires human intervention. Verify in browser or switch account.\n", style="bold red")
        elif rec == "relogin":
            text.append("Action       : ", style="bold magenta")
            text.append("Press 'l' to relogin / fix this account.\n", style="bold magenta")

        content_static.update(text)

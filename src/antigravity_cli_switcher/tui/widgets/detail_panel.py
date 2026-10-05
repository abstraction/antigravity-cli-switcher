"""Detail panel widget showing rich overview for the highlighted account."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountUtilizationRecord,
    AccountVerification,
    HealthStatus,
)
from antigravity_cli_switcher.tui.formatters import (
    format_next_refresh,
    format_problem_summary,
    format_window_summary,
)


class DetailPanel(Widget):
    """Panel displaying status of the selected account."""

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._last_name: str | None = None
        self._last_meta: AccountMeta | None = None
        self._last_ver: AccountVerification | None = None
        self._last_util: AccountUtilizationRecord | None = None

    def compose(self) -> ComposeResult:
        yield Static("Overview", id="detail-title")
        yield Static(id="detail-content")

    def on_resize(self, event: events.Resize) -> None:
        if self._last_name and self._last_meta:
            self.update_detail(self._last_name, self._last_meta, self._last_ver, self._last_util)

    def update_detail(
        self,
        name: str | None,
        meta: AccountMeta | None,
        verification: AccountVerification | None,
        utilization: AccountUtilizationRecord | None = None,
    ) -> None:
        """Update the panel contents for the given account."""
        self._last_name = name
        self._last_meta = meta
        self._last_ver = verification
        self._last_util = utilization

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
        title_text.append("Overview: ", style="bold #8b949e")
        title_text.append(name, style="bold #58a6ff")
        title_text.append(" │ ", style="#30363d")
        title_text.append("Health: ", style="#8b949e")
        health_style = (
            "bold red reverse"
            if meta.health_status == HealthStatus.OAUTH_ROTATED
            else (
                "bold #3fb950"
                if meta.health_status in ("healthy", "ok", "ready")
                else (
                    "bold #f85149"
                    if meta.health_status in ("ineligible", "auth_missing", "auth_expired")
                    else "bold #d29922"
                )
            )
        )
        title_text.append(str(meta.health_status), style=health_style)
        title_static.update(title_text)

        gemini_5h = format_window_summary(meta, "short", now, "gemini")
        gemini_wk = format_window_summary(meta, "weekly", now, "gemini")
        other_5h = format_window_summary(meta, "short", now, "other")
        other_wk = format_window_summary(meta, "weekly", now, "other")
        next_ref = f"{format_next_refresh(meta, now)} ({meta.refresh_policy_seconds}s)"
        mode_str = f"{meta.status} │ {'enabled' if meta.enabled else 'disabled'}"
        status_str = str(meta.health_status.value if hasattr(meta.health_status, "value") else meta.health_status)

        text = Text()
        width = self.size.width

        duty_str = ""
        burnt_str = ""
        min_hd_str = ""
        if utilization is not None:
            act_sec = utilization.rolling_7d_active_seconds
            duty_pct = round((act_sec / 604800.0) * 100.0, 1)
            g_cons = round(utilization.rolling_7d_gemini_consumed)
            o_cons = round(utilization.rolling_7d_other_consumed)
            min_hd = round(min(utilization.rolling_7d_min_gemini_headroom, utilization.rolling_7d_min_other_headroom))
            duty_str = f"{duty_pct}%"
            burnt_str = f"G:{g_cons}% O:{o_cons}%"
            min_hd_str = f"{min_hd}%"

        if 0 < width < 75:
            # Single-column compact presentation for narrow screens
            compact_lines = [
                ("Token", tok_email),
                ("Expected", exp_email),
                ("Plan", meta.plan_type or "unknown"),
                ("Mode", mode_str),
                ("Backend", meta.last_quota_backend or "-"),
                ("Gemini 5h", gemini_5h),
                ("Gemini Wk", gemini_wk),
                ("Other 5h", other_5h),
                ("Next Ref", next_ref),
                ("Problem", prob_str),
                ("Status", f"{status_str} (fails: {meta.fail_count})"),
            ]
            if utilization is not None:
                compact_lines.append(("7D Duty", f"{duty_str} (burnt {burnt_str})"))
                compact_lines.append(("Min Head", min_hd_str))

            for k, v in compact_lines:
                text.append(f"{k:<10}: ", style="#8b949e")
                text.append(f"{v}\n", style="#e6edf3")
        else:
            # 2-column key-value presentation tightened to 71 cols
            lines = [
                ("Token Email", tok_email, "Gemini 5h", gemini_5h),
                ("Exp. Email", exp_email, "Gemini Wk", gemini_wk),
                ("Plan", meta.plan_type or "unknown", "Other 5h", other_5h),
                ("Mode", mode_str, "Other Wk", other_wk),
                ("Next Refresh", next_ref, "Backend", meta.last_quota_backend or "-"),
                ("Failures", str(meta.fail_count), "Problem", prob_str),
            ]
            for k1, v1, k2, v2 in lines:
                text.append(f"{k1:<12}: ", style="#8b949e")
                text.append(f"{v1:<20} ", style="#e6edf3")
                text.append("│ ", style="#30363d")
                text.append(f"{k2:<10}: ", style="#8b949e")
                text.append(f"{v2}\n", style="#e6edf3")

            if utilization is not None:
                text.append(f"{'7D Duty':<12}: ", style="#8b949e")
                text.append(f"{duty_str:<20} ", style="#e6edf3")
                text.append("│ ", style="#30363d")
                text.append(f"{'Burnt':<10}: ", style="#8b949e")
                text.append(f"{burnt_str} (Min Head: {min_hd_str})\n", style="#e6edf3")

        if summary and summary != "Ready for use.":
            text.append("Note        : ", style="bold #d29922")
            text.append(f"{summary}\n", style="#d29922")

        if rec == "human_intervention":
            text.append("Action      : ", style="bold #f85149")
            text.append("Requires human intervention. Verify in browser or switch account.\n", style="bold #f85149")
        elif rec == "relogin":
            text.append("Action      : ", style="bold #bc8cff")
            text.append("Press 'l' to relogin / fix this account.\n", style="bold #bc8cff")

        content_static.update(text)

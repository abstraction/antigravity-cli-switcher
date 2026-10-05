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
    format_natural_duration,
    format_next_refresh,
    format_problem_summary,
    parse_iso_timestamp,
)
from antigravity_cli_switcher.tui.theme import render_meter_bar


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

    def _render_quota_window(
        self,
        meta: AccountMeta,
        window_name: str,
        now: datetime,
        family: str = "gemini",
    ) -> Text:
        canonical = "other" if family in ("claude", "other") else family
        windows = meta.usage_families.get(canonical, {})
        if not windows and canonical == "gemini":
            windows = meta.usage_windows

        window = windows.get(window_name)
        if window is None or (window.value is None and window.status == "unknown"):
            return Text("-", style="dim")

        res = Text()
        if window.value is None:
            res.append(str(window.status), style="dim")
        else:
            val = float(window.value)
            res.append_text(render_meter_bar(val, width=5))
            res.append(" ")
            pct_style = "bold #77ca9b" if val > 50 else ("bold #cbc06c" if val > 20 else "bold #dc4c4c")
            res.append(f"{round(val):>3}%", style=pct_style)

        reset_at = parse_iso_timestamp(window.reset_at)
        if reset_at is not None:
            delta = int((reset_at - now).total_seconds())
            countdown = format_natural_duration(delta)
            if countdown != "-":
                res.append(f" (in {countdown})", style="dim #888888")

        return res

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
            title_text = Text()
            title_text.append("╭─┐", style="#444444")
            title_text.append("overview", style="bold #77ca9b")
            title_text.append("┌─ ", style="#444444")
            title_text.append("No account selected", style="dim")
            title_static.update(title_text)
            content_static.update(Text("Select an account to view details.", style="dim italic"))
            return

        now = datetime.now(timezone.utc)
        prob_str = format_problem_summary(
            verification.problem_status if verification else None,
            verification.recommended_action if verification else None,
        )
        rec = verification.recommended_action if verification else "none"
        summary = verification.summary if verification else "Ready for use."
        tok_email = (
            (meta.identity.email if meta.identity and meta.identity.email else None)
            or (verification.token_email if verification and verification.token_email else None)
            or "-"
        )
        exp_email = (
            meta.expected_email
            or (verification.expected_email if verification and verification.expected_email else None)
            or "-"
        )

        title_text = Text()
        title_text.append("╭─┐", style="#444444")
        title_text.append("overview", style="bold #77ca9b")
        title_text.append("┌─ ", style="#444444")
        title_text.append(name, style="bold #eeeeee")
        title_text.append(" ╎ ", style="#444444")
        title_text.append("Health: ", style="#888888")
        health_style = (
            "bold red reverse"
            if meta.health_status == HealthStatus.OAUTH_ROTATED
            else (
                "bold #77ca9b"
                if meta.health_status in ("healthy", "ok", "ready")
                else (
                    "bold #dc4c4c"
                    if meta.health_status in ("ineligible", "auth_missing", "auth_expired")
                    else "bold #cbc06c"
                )
            )
        )
        title_text.append(str(meta.health_status), style=health_style)
        title_text.append(" ╎ ", style="#444444")
        title_text.append("[o: toggle]", style="#555555")
        title_static.update(title_text)

        gemini_5h = self._render_quota_window(meta, "short", now, "gemini")
        gemini_wk = self._render_quota_window(meta, "weekly", now, "gemini")
        other_5h = self._render_quota_window(meta, "short", now, "other")
        other_wk = self._render_quota_window(meta, "weekly", now, "other")
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
            compact_lines: list[tuple[str, str | Text]] = [
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
                if isinstance(v, Text):
                    text.append_text(v)
                    text.append("\n")
                else:
                    text.append(f"{v}\n", style="#e6edf3")
        else:
            # 2-column key-value presentation tightened to 71 cols
            lines: list[tuple[str, str, str, str | Text]] = [
                ("Token Email", tok_email, "Gemini 5h", gemini_5h),
                ("Exp. Email", exp_email, "Gemini Wk", gemini_wk),
                ("Plan", meta.plan_type or "unknown", "Other 5h", other_5h),
                ("Mode", mode_str, "Other Wk", other_wk),
                ("Next Refresh", next_ref, "Backend", meta.last_quota_backend or "-"),
                ("Failures", str(meta.fail_count), "Problem", prob_str),
            ]
            for k1, v1, k2, v2 in lines:
                disp_v1 = v1 if len(v1) <= 20 else v1[:19] + "…"
                text.append(f"{k1:<12}: ", style="#8b949e")
                text.append(f"{disp_v1:<20} ", style="#e6edf3")
                text.append("╎ ", style="#333333")
                text.append(f"{k2:<10}: ", style="#8b949e")
                if isinstance(v2, Text):
                    text.append_text(v2)
                    text.append("\n")
                else:
                    text.append(f"{v2}\n", style="#e6edf3")

            if utilization is not None:
                disp_duty = duty_str if len(duty_str) <= 20 else duty_str[:19] + "…"
                text.append(f"{'7D Duty':<12}: ", style="#8b949e")
                text.append(f"{disp_duty:<20} ", style="#e6edf3")
                text.append("╎ ", style="#333333")
                text.append(f"{'Burnt':<10}: ", style="#8b949e")
                text.append(f"{burnt_str} (Min Head: {min_hd_str})\n", style="#e6edf3")

        if summary and summary != "Ready for use.":
            text.append("Note        : ", style="bold #cbc06c")
            text.append(f"{summary}\n", style="#cbc06c")

        if rec == "human_intervention":
            text.append("Action      : ", style="bold #dc4c4c")
            text.append("Requires human intervention. Verify in browser or switch accounts.\n", style="bold #dc4c4c")
        elif rec == "relogin":
            text.append("Action      : ", style="bold #9b82d4")
            text.append("Press [l] to relogin account.\n", style="bold #9b82d4")

        content_static.update(text)

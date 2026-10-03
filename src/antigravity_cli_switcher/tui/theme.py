"""Theme colors, badge helpers, and text styles for ACS Textual TUI."""

from __future__ import annotations

from rich.text import Text


def format_badge(label: str, style: str) -> Text:
    """Format a short badge with a specific rich style."""
    return Text(f"[{label}]", style=style)


def format_state(status: str) -> Text:
    """Format account state (active, standby, disabled)."""
    if status == "active":
        return Text("ACTIVE", style="bold green")
    if status == "disabled":
        return Text("DISABLED", style="dim italic")
    return Text("STANDBY", style="cyan")


def format_plan_badge(plan_type: str | None) -> Text:
    """Format a compact plan badge."""
    if not plan_type:
        return Text("?", style="dim")
    key = plan_type.lower()
    if "ultra" in key:
        return Text("Ultra", style="bold magenta")
    if "pro" in key:
        return Text("Pro", style="bold blue")
    if "plus" in key:
        return Text("Plus", style="bold green")
    if "free" in key:
        return Text("Free", style="dim")
    return Text(plan_type[:5].title(), style="cyan")


def format_health_badge(problem_status: str | None) -> Text:
    """Format health problem status into a colored tag."""
    if not problem_status or problem_status in ("ok", "healthy"):
        return Text("OK", style="bold green")
    if problem_status == "cooldown":
        return Text("COOLDN", style="bold yellow")
    if problem_status in ("disabled", "off"):
        return Text("DISABL", style="dim")
    if problem_status in ("missing_auth", "auth_expired", "logged_out"):
        return Text("AUTH", style="bold red")
    if problem_status == "oauth_rotated":
        return Text("OAUTH_ROTATED", style="bold red reverse")
    if "mismatch" in problem_status or "duplicate" in problem_status:
        return Text("MISMAT", style="bold red")
    if problem_status in ("stale", "quota_check_due"):
        return Text("STALE", style="yellow")
    if problem_status == "token_refresh_required":
        return Text("TKN_ST", style="yellow")
    if problem_status == "ineligible":
        return Text("INELIG", style="bold red")
    return Text(problem_status[:6].upper(), style="bold yellow")


def format_quota_ratio(used: float | None, total: float | None = 100.0) -> Text:
    """Format usage percentage with dynamic color thresholds."""
    if used is None:
        return Text("-", style="dim")
    pct = round(used, 1)
    if pct >= 90.0:
        return Text(f"{pct:.0f}%", style="bold red")
    if pct >= 75.0:
        return Text(f"{pct:.0f}%", style="bold yellow")
    return Text(f"{pct:.0f}%", style="bold green")

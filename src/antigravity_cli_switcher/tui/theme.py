"""Theme colors, badge helpers, and text styles for ACS Textual TUI."""

from __future__ import annotations

from rich.text import Text


def format_badge(label: str, style: str) -> Text:
    """Format a short badge with a specific rich style."""
    return Text(f"[{label}]", style=style)


def format_state(status: str) -> Text:
    """Format account state (active, standby, disabled) with btop jewel tones."""
    if status == "active":
        return Text("ACTIVE", style="bold #77ca9b")
    if status == "disabled":
        return Text("DISABLED", style="dim italic")
    return Text("STANDBY", style="#4897d4")


def format_plan_badge(plan_type: str | None) -> Text:
    """Format a compact plan badge."""
    if not plan_type:
        return Text("?", style="dim")
    key = plan_type.lower()
    if "ultra" in key:
        return Text("Ultra", style="bold #9b82d4")
    if "pro" in key:
        return Text("Pro", style="bold #4897d4")
    if "plus" in key:
        return Text("Plus", style="bold #77ca9b")
    if "free" in key:
        return Text("Free", style="dim")
    return Text(plan_type[:5].title(), style="#4897d4")


def format_health_badge(problem_status: str | None) -> Text:
    """Format health problem status into a colored tag."""
    if not problem_status or problem_status in ("ok", "healthy"):
        return Text("OK", style="bold #77ca9b")
    if problem_status == "cooldown":
        return Text("COOLDN", style="bold #cbc06c")
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


def format_quota_ratio(used: float | None, total: float = 100.0) -> Text:
    """Format usage percentage with dynamic color thresholds."""
    if used is None:
        return Text("-", style="dim")
    pct = round(used, 1)
    if pct >= 90.0:
        return Text(f"{pct:g}%", style="bold #dc4c4c")
    if pct >= 75.0:
        return Text(f"{pct:g}%", style="bold #cbc06c")
    return Text(f"{pct:g}%", style="bold #77ca9b")


def format_meter_bar(value: float | None, total: float = 100.0, width: int = 5) -> str:
    """Render a compact btop-style meter bar using filled and empty blocks."""
    if width <= 0:
        return ""
    if value is None:
        return "░" * width
    clamped = max(0.0, min(total, float(value)))
    if clamped <= 0.0:
        filled = 0
    elif clamped >= total:
        filled = width
    else:
        filled = round((clamped / total) * width) if total > 0 else 0
        if filled == 0 and clamped > 0.0:
            filled = 1
        elif filled == width and clamped < total:
            filled = width - 1
    return "■" * filled + "░" * (width - filled)


def render_meter_bar(value: float | None, total: float = 100.0, width: int = 5) -> Text:
    """Render a btop-style gradient meter bar with jewel-tone colors."""
    if width <= 0:
        return Text("")
    if value is None:
        return Text("░" * width, style="dim #444444")
    clamped = max(0.0, min(total, float(value)))
    if clamped <= 0.0:
        filled_count = 0
    elif clamped >= total:
        filled_count = width
    else:
        filled_count = round((clamped / total) * width) if total > 0 else 0
        if filled_count == 0 and clamped > 0.0:
            filled_count = 1
        elif filled_count == width and clamped < total:
            filled_count = width - 1
    empty_count = width - filled_count

    if clamped > 50.0:
        bar_style = "bold #77ca9b"
    elif clamped > 20.0:
        bar_style = "bold #cbc06c"
    else:
        bar_style = "bold #dc4c4c"

    bar = Text()
    if filled_count > 0:
        bar.append("■" * filled_count, style=bar_style)
    if empty_count > 0:
        bar.append("░" * empty_count, style="dim #444444")
    return bar


def format_colored_model_usage(raw_text: str) -> Text:
    """Format short/weekly model usage string with btop gradient jewel tones.

    Preserves exact plain-text representation (e.g. ' 95%/80% ' or '-') while
    applying gradient coloring to the numeric values:
      > 50%: #77ca9b (healthy green)
      20-50%: #cbc06c (medium yellow)
      <= 20%: #dc4c4c (critical red)
    """
    if not raw_text or raw_text.strip() in ("-", ""):
        return Text(raw_text or "-", style="dim")

    parts = raw_text.split("/")
    if len(parts) != 2:
        return Text(raw_text)

    res = Text()
    for idx, part in enumerate(parts):
        cleaned = part.strip().rstrip("%")
        try:
            val = float(cleaned)
            if val > 50.0:
                style = "bold #77ca9b"
            elif val > 20.0:
                style = "bold #cbc06c"
            else:
                style = "bold #dc4c4c"
        except ValueError:
            style = "dim" if cleaned == "-" else "#cccccc"

        res.append(part, style=style)
        if idx == 0:
            res.append("/", style="#444444")
    return res

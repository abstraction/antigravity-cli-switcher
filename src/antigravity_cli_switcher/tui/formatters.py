"""Formatting utilities for the ACS Textual dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from antigravity_cli_switcher.models import AccountMeta, ProblemStatus, UsageWindow


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse ISO timestamp safely with UTC awareness."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def format_natural_duration(delta_seconds: int) -> str:
    """Format duration in seconds into human-readable compact form."""
    if delta_seconds <= 0:
        return "due"
    days, rem = divmod(delta_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m" if minutes else (f"{days}d {hours}h" if hours else f"{days}d")
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {seconds:02}s" if seconds else f"{minutes}m"
    return f"{seconds}s"


def _get_group_window(meta: AccountMeta, window_key: str) -> UsageWindow | None:
    if meta.usage_families:
        if window_key.startswith("gemini_"):
            sub = window_key[len("gemini_") :]
            fam = meta.usage_families.get("gemini")
            if fam and sub in fam:
                return fam[sub]
        elif window_key.startswith("claude_") or window_key.startswith("other_"):
            sub = window_key.split("_", 1)[1]
            fam = meta.usage_families.get("other") or meta.usage_families.get("claude")
            if fam and sub in fam:
                return fam[sub]
    return meta.usage_windows.get(window_key)


def _format_usage_value(window: UsageWindow | None) -> str:
    if window is None:
        return "-"
    if window.value is not None:
        return f"{round(float(window.value), 1):g}%"
    if window.status == "unknown":
        return "-"
    return str(window.status)[:4]


def _format_reset_compact(window: UsageWindow | None, now: datetime) -> str:
    if window is None:
        return "-"
    reset_at = parse_iso_timestamp(window.reset_at)
    if not reset_at:
        return "-"
    delta = int((reset_at - now).total_seconds())
    if delta <= 0:
        return "0m"
    days, rem = divmod(delta, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d"
    if hours > 0:
        return f"{hours}h"
    return f"{minutes}m"


def format_model_usage(meta: AccountMeta, model_prefix: str) -> str:
    """Format short/weekly usage percentages for a model family."""
    short = _get_group_window(meta, f"{model_prefix}_short")
    weekly = _get_group_window(meta, f"{model_prefix}_weekly")

    has_model = (short is not None and short.status != "unknown") or (weekly is not None and weekly.status != "unknown")
    if not has_model and model_prefix == "gemini":
        g_short = _get_group_window(meta, "gemini_short")
        g_weekly = _get_group_window(meta, "gemini_weekly")
        c_short = _get_group_window(meta, "claude_short")
        c_weekly = _get_group_window(meta, "claude_weekly")
        has_any = (
            (g_short is not None and g_short.status != "unknown")
            or (g_weekly is not None and g_weekly.status != "unknown")
            or (c_short is not None and c_short.status != "unknown")
            or (c_weekly is not None and c_weekly.status != "unknown")
        )
        if not has_any:
            short = meta.usage_windows.get("short")
            weekly = meta.usage_windows.get("weekly")

    short_unknown = short is None or short.status == "unknown"
    weekly_unknown = weekly is None or weekly.status == "unknown"
    if short_unknown and weekly_unknown:
        return "-"

    s_val = _format_usage_value(short)
    w_val = _format_usage_value(weekly)
    return f"{s_val:>4}/{w_val:<4}"


def format_countdown(meta: AccountMeta, now: datetime) -> str:
    """Format reset countdown string for Gemini and Claude."""
    g_short = _get_group_window(meta, "gemini_short")
    g_weekly = _get_group_window(meta, "gemini_weekly")
    c_short = _get_group_window(meta, "claude_short")
    c_weekly = _get_group_window(meta, "claude_weekly")

    has_gemini = (g_short is not None and (g_short.status != "unknown" or g_short.reset_at is not None)) or (
        g_weekly is not None and (g_weekly.status != "unknown" or g_weekly.reset_at is not None)
    )
    has_claude = (c_short is not None and (c_short.status != "unknown" or c_short.reset_at is not None)) or (
        c_weekly is not None and (c_weekly.status != "unknown" or c_weekly.reset_at is not None)
    )

    g_str = f"{_format_reset_compact(g_short, now)}/{_format_reset_compact(g_weekly, now)}"
    c_str = f"{_format_reset_compact(c_short, now)}/{_format_reset_compact(c_weekly, now)}"

    if has_gemini and has_claude:
        return f"G:{g_str} C:{c_str}"
    if has_gemini:
        return f"G:{g_str}"
    if has_claude:
        return f"C:{c_str}"

    short_fallback = meta.usage_windows.get("short")
    weekly_fallback = meta.usage_windows.get("weekly")
    return f"{_format_reset_compact(short_fallback, now)}/{_format_reset_compact(weekly_fallback, now)}"


def format_next_refresh(meta: AccountMeta, now: datetime) -> str:
    """Format countdown until next scheduled background live check."""
    if not meta.enabled or meta.status in {"disabled", "cooldown"}:
        return "-"
    check_str = meta.next_live_check_at
    next_check = parse_iso_timestamp(check_str)
    if next_check is not None:
        delta = int((next_check - now).total_seconds())
        if delta <= 0:
            return "due"
        return format_natural_duration(delta)

    policy = meta.refresh_policy_seconds
    if policy <= 0:
        return "-"
    last_check = parse_iso_timestamp(meta.last_live_check_at)
    if last_check is None:
        return "due"
    due_at = last_check + timedelta(seconds=policy)
    delta = int((due_at - now).total_seconds())
    if delta <= 0:
        return "due"
    return format_natural_duration(delta)


def format_window_summary(meta: AccountMeta, window_name: str, now: datetime, family: str = "gemini") -> str:
    """Format full summary of a usage window including reset time."""
    canonical = "other" if family in ("claude", "other") else family
    windows = meta.usage_families.get(canonical, {})
    if not windows and canonical == "gemini":
        windows = meta.usage_windows

    window = windows.get(window_name)
    if window is None or (window.value is None and window.status == "unknown"):
        return "-"

    if window.value is None:
        usage = str(window.status)
    else:
        usage = f"{round(float(window.value))}%"

    reset_at = parse_iso_timestamp(window.reset_at)
    if reset_at is None:
        countdown = "-"
    else:
        delta = int((reset_at - now).total_seconds())
        countdown = format_natural_duration(delta)

    return f"{usage} (in {countdown})" if countdown != "-" else usage


def format_last_error(meta: AccountMeta) -> str:
    """Format last error string compactly."""
    if not meta.last_error:
        return "-"
    return str(meta.last_error)[:24]


def format_problem_summary(
    problem_status: ProblemStatus | str | None,
    recommended_action: str | None = None,
) -> str:
    """Format problem status and recommended action into clean, plain English."""
    if not problem_status:
        return "None"

    raw_status = (problem_status.value if isinstance(problem_status, ProblemStatus) else str(problem_status)).strip()

    if raw_status.lower() in ("ok", "healthy", "ready", "none"):
        return "None"

    status_map = {
        "disabled": "Disabled",
        "cooldown": "Active cooldown",
        "missing_auth": "Missing credentials",
        "logged_out": "Logged out (token expired)",
        "ineligible": "Ineligible",
        "refresh_failed": "Live check failed",
        "stale": "Stale",
        "token_refresh_required": "Token refresh due",
        "quota_check_due": "Quota check due",
        "token_mismatch": "Token email mismatch",
        "token_duplicate": "Duplicate token",
        "synthetic_token": "Synthetic token",
    }
    desc = status_map.get(raw_status.lower(), raw_status.replace("_", " ").title())

    action = (recommended_action or "").lower().strip()
    if not action or action == "none":
        return desc

    action_map = {
        "relogin": "relogin required",
        "wait": "waiting cooldown",
        "enable": "enable required",
        "human_intervention": "human intervention needed",
        "refresh": "refresh due",
        "fix": "fix needed",
    }
    action_text = action_map.get(action, action.replace("_", " "))
    return f"{desc} ({action_text})"

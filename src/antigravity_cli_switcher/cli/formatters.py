from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _problem_badge(problem_status: str | None) -> str:
    mapping = {
        "ok": "OK",
        "stale": "STALE",
        "refresh_failed": "FAIL",
        "cooldown": "COOL",
        "disabled": "OFF",
        "missing_auth": "MISS",
        "logged_out": "OUT",
        "token_mismatch": "MISMAT",
        "token_duplicate": "DUP",
        "duplicate_token": "DUP",
        "ineligible": "INELIG",
        "synthetic_token": "SYNTH",
        "token_synthetic": "SYNTH",
        "token_refresh_required": "TKN_ST",
        "quota_check_due": "STALE",
    }
    return mapping.get((problem_status or "").lower(), "?")


def _format_proxy_brief(proxy: dict | None) -> str:
    if not isinstance(proxy, dict):
        return "-"
    if not proxy.get("url"):
        return "-"
    label = proxy.get("label")
    state = "on" if proxy.get("enabled") else "off"
    return f"{label or proxy.get('url')} ({state})"


def _usage_window_values(meta: dict) -> tuple[float | None, float | None]:
    windows = meta.get("usage_windows")
    if not isinstance(windows, dict):
        return None, None
    short_raw = windows.get("short")
    weekly_raw = windows.get("weekly")
    short_val = (
        short_raw.get("value")
        if isinstance(short_raw, dict) and isinstance(short_raw.get("value"), (int, float))
        else None
    )
    weekly_val = (
        weekly_raw.get("value")
        if isinstance(weekly_raw, dict) and isinstance(weekly_raw.get("value"), (int, float))
        else None
    )
    return short_val, weekly_val


def _get_group_window(meta: dict, key: str) -> dict:
    families = meta.get("usage_families")
    if isinstance(families, dict):
        if key.startswith("gemini_"):
            sub = key[len("gemini_") :]
            fam_gemini = families.get("gemini")
            if isinstance(fam_gemini, dict):
                w = fam_gemini.get(sub)
                if isinstance(w, dict):
                    return w
        elif key.startswith("claude_") or key.startswith("other_"):
            sub = key.split("_", 1)[1]
            fam_other = families.get("other")
            if isinstance(fam_other, dict):
                w = fam_other.get(sub)
                if isinstance(w, dict):
                    return w
    windows = meta.get("usage_windows")
    if isinstance(windows, dict):
        w = windows.get(key)
        if isinstance(w, dict):
            return w
    return {}


SORT_MODES = [
    ("added-oldest", "Added Oldest", "created_at", False),
    ("added-newest", "Added Newest", "created_at", True),
    ("usage-high", "Usage High", "usage", True),
    ("usage-low", "Usage Low", "usage", False),
    ("countdown-short", "Countdown Short", "countdown", False),
    ("countdown-long", "Countdown Long", "countdown", True),
]
DEFAULT_SORT_MODE = "usage-low"


def _format_identity(meta: dict) -> str:
    identity = meta.get("identity")
    if isinstance(identity, dict):
        return identity.get("account_name") or "-"
    return "-"


def _format_last_error(meta: dict) -> str:
    err = meta.get("last_error")
    return str(err)[:20] if err else "-"


def _parse_iso_timestamp(timestamp_raw: str | None) -> datetime | None:
    if not timestamp_raw:
        return None
    try:
        normalized = timestamp_raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get_min_window(meta: dict, now: datetime) -> tuple[float | None, str | None]:
    candidates = []
    for key in ("short", "weekly"):
        w = _get_group_window(meta, key)
        if isinstance(w, dict) and w.get("value") is not None:
            candidates.append(w)
    if not candidates:
        return None, None
    c = min(candidates, key=lambda x: float(x.get("value", 100.0) or 100.0))
    return c.get("value"), c.get("reset_at")


def _format_usage(meta: dict) -> str:
    g_short = _get_group_window(meta, "gemini_short")
    g_weekly = _get_group_window(meta, "gemini_weekly")
    c_short = _get_group_window(meta, "claude_short")
    c_weekly = _get_group_window(meta, "claude_weekly")
    has_groups = any(w.get("value") is not None for w in (g_short, g_weekly, c_short, c_weekly))
    if has_groups:
        g_s = "-" if g_short.get("value") is None else f"{round(float(g_short['value']))}%"
        g_w = "-" if g_weekly.get("value") is None else f"{round(float(g_weekly['value']))}%"
        c_s = "-" if c_short.get("value") is None else f"{round(float(c_short['value']))}%"
        c_w = "-" if c_weekly.get("value") is None else f"{round(float(c_weekly['value']))}%"
        return f"G:{g_s}/{g_w} C:{c_s}/{c_w}"
    short_val, weekly_val = _usage_window_values(meta)
    s = "-" if short_val is None else f"{round(short_val)}%"
    w = "-" if weekly_val is None else f"{round(weekly_val)}%"
    return f"{s}/{w}"


def _format_model_usage(meta: dict, model_prefix: str) -> str:
    short = _get_group_window(meta, f"{model_prefix}_short")
    weekly = _get_group_window(meta, f"{model_prefix}_weekly")
    if short.get("value") is not None or weekly.get("value") is not None:
        s = "-" if short.get("value") is None else f"{round(float(short['value']))}%"
        w = "-" if weekly.get("value") is None else f"{round(float(weekly['value']))}%"
        return f"{s}/{w}"
    g_short = _get_group_window(meta, "gemini_short")
    g_weekly = _get_group_window(meta, "gemini_weekly")
    c_short = _get_group_window(meta, "claude_short")
    c_weekly = _get_group_window(meta, "claude_weekly")
    has_groups = any(w.get("value") is not None for w in (g_short, g_weekly, c_short, c_weekly))
    if has_groups:
        return "-/-"
    short_val, weekly_val = _usage_window_values(meta)
    s = "-" if short_val is None else f"{round(short_val)}%"
    w = "-" if weekly_val is None else f"{round(weekly_val)}%"
    return f"{s}/{w}"


def _get_nearest_reset(meta: dict, now: datetime) -> str | None:
    resets = []
    windows = meta.get("usage_windows")
    if isinstance(windows, dict):
        for key in ("short", "weekly"):
            w = windows.get(key)
            if isinstance(w, dict) and w.get("reset_at"):
                dt = _parse_iso_timestamp(w["reset_at"])
                if dt:
                    resets.append(dt)
    if not resets:
        return None
    future = [dt for dt in resets if dt > now]
    return min(future).isoformat() if future else min(resets).isoformat()


def _format_natural_duration(delta_seconds: int | float) -> str:
    secs = int(max(0, delta_seconds))
    if secs <= 0:
        return "due"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m" if minutes else (f"{days}d {hours}h" if hours else f"{days}d")
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {seconds:02}s" if seconds else f"{minutes}m"
    return f"{seconds}s"


def _format_reset_compact(window: dict, now: datetime) -> str:
    if not isinstance(window, dict):
        return "-"
    reset_at_raw = window.get("reset_at")
    if not reset_at_raw or not isinstance(reset_at_raw, str):
        return "-"
    dt = _parse_iso_timestamp(reset_at_raw)
    if dt is None:
        return "-"
    delta = int((dt - now).total_seconds())
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


def _format_countdown(meta: dict, now: datetime) -> str:
    g_short = _get_group_window(meta, "gemini_short")
    g_weekly = _get_group_window(meta, "gemini_weekly")
    c_short = _get_group_window(meta, "claude_short")
    c_weekly = _get_group_window(meta, "claude_weekly")

    has_gemini = (
        g_short.get("status", "unknown") != "unknown"
        or g_weekly.get("status", "unknown") != "unknown"
        or bool(g_short.get("reset_at"))
        or bool(g_weekly.get("reset_at"))
    )
    has_claude = (
        c_short.get("status", "unknown") != "unknown"
        or c_weekly.get("status", "unknown") != "unknown"
        or bool(c_short.get("reset_at"))
        or bool(c_weekly.get("reset_at"))
    )

    g_str = f"{_format_reset_compact(g_short, now)}/{_format_reset_compact(g_weekly, now)}"
    c_str = f"{_format_reset_compact(c_short, now)}/{_format_reset_compact(c_weekly, now)}"

    if has_gemini and has_claude:
        return f"G:{g_str} C:{c_str}"
    if has_gemini:
        return f"G:{g_str}"
    if has_claude:
        return f"C:{c_str}"

    raw_windows = meta.get("usage_windows")
    windows: dict = raw_windows if isinstance(raw_windows, dict) else {}
    short_raw = windows.get("short")
    weekly_raw = windows.get("weekly")
    short_w: dict = short_raw if isinstance(short_raw, dict) else {}
    weekly_w: dict = weekly_raw if isinstance(weekly_raw, dict) else {}
    s = _format_reset_compact(short_w, now)
    w = _format_reset_compact(weekly_w, now)
    if (s == "-" and w == "-") or (s == "0m" and w == "0m"):
        return "-"
    return f"{s}/{w}"


def _format_age(timestamp_raw: str | None, now: datetime) -> str:
    dt = _parse_iso_timestamp(timestamp_raw)
    if dt is None:
        return "-"
    delta = now - dt
    if delta.total_seconds() < 0:
        return "in the future"
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _format_next_refresh(meta: dict, now: datetime) -> str:
    status = meta.get("status") or "standby"
    if not meta.get("enabled", True) or status in {"disabled", "cooldown"}:
        return "-"
    next_check = _parse_iso_timestamp(meta.get("next_live_check_at"))
    if next_check is not None:
        delta = next_check - now
        if delta.total_seconds() <= 0:
            return "due"
        return _format_natural_duration(delta.total_seconds())
    policy = int(meta.get("refresh_policy_seconds", 0) or 0)
    if policy <= 0:
        return "-"
    last_check = _parse_iso_timestamp(meta.get("last_live_check_at"))
    if last_check is None:
        return "due"
    due_at = last_check + timedelta(seconds=policy)
    delta = due_at - now
    if delta.total_seconds() <= 0:
        return "due"
    return _format_natural_duration(delta.total_seconds())


def _family_usage_windows(meta: dict, family: str) -> dict:
    canonical = "other" if family in ("claude", "other") else family
    raw_families = meta.get("usage_families")
    families: dict = raw_families if isinstance(raw_families, dict) else {}
    raw_sub = families.get(canonical)
    windows: dict | None = raw_sub if isinstance(raw_sub, dict) else None
    if windows is None and canonical == "gemini":
        raw_win = meta.get("usage_windows")
        windows = raw_win if isinstance(raw_win, dict) else {}
    elif windows is None and canonical == "other":
        raw_win = meta.get("usage_windows")
        raw = raw_win if isinstance(raw_win, dict) else {}
        c_short = raw.get("claude_short")
        c_weekly = raw.get("claude_weekly")
        if isinstance(c_short, dict) or isinstance(c_weekly, dict):
            windows = {
                "short": c_short if isinstance(c_short, dict) else {},
                "weekly": c_weekly if isinstance(c_weekly, dict) else {},
            }
    return windows or {}


def _format_window_summary(meta: dict, window_name: str, now: datetime, family: str = "gemini") -> str:
    windows = _family_usage_windows(meta, family)
    raw_window = windows.get(window_name)
    window: dict = raw_window if isinstance(raw_window, dict) else {}
    if not window and isinstance(meta.get("usage_windows"), dict):
        raw_usage = meta.get("usage_windows")
        if isinstance(raw_usage, dict):
            w = raw_usage.get(window_name)
            if isinstance(w, dict):
                window = w
    value = window.get("value")
    status = window.get("status") or "unknown"
    reset_at = _parse_iso_timestamp(window.get("reset_at"))
    if value is None:
        usage = "-" if status == "unknown" else str(status)
    else:
        usage = str(value)
    if reset_at is None:
        countdown = "-"
    else:
        delta = int((reset_at - now).total_seconds())
        countdown = _format_natural_duration(delta)
    if isinstance(value, (int, float)):
        usage = f"{round(float(value))}%"
    return f"{usage} (in {countdown})" if countdown != "-" else usage


def _format_usage_value(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _format_reset_value(reset_at: str | None, now: datetime) -> str:
    if not reset_at:
        return "-"
    dt = _parse_iso_timestamp(reset_at)
    if dt is None:
        return reset_at[:16]
    delta = dt - now
    if delta.total_seconds() <= 0:
        return "reset due"
    return f"in {_format_natural_duration(delta.total_seconds())}"


def _format_live_state(meta: dict, now: datetime) -> str:
    health = str(meta.get("health_status") or meta.get("status") or "unknown")
    next_check = _parse_iso_timestamp(meta.get("next_live_check_at"))
    if next_check and next_check <= now:
        return f"stale/{health}"[:18]
    return health[:18]


def _format_switch_runtime_summary(snapshot: dict, now: datetime) -> str:
    raw = snapshot.get("switch_runtime")
    runtime = raw if isinstance(raw, dict) else {}
    status = str(runtime.get("status") or "idle")
    reason = str(runtime.get("reason") or "-")
    trigger = str(runtime.get("trigger") or "-")
    completed = _format_age(runtime.get("last_completed_at"), now)
    return f"{status} | {reason} | {trigger} | {completed}"[:46]


def _format_switch_runtime_policy(snapshot: dict, now: datetime) -> str:
    raw = snapshot.get("switch_runtime")
    runtime = raw if isinstance(raw, dict) else {}
    completed = _format_age(runtime.get("last_completed_at"), now)
    previous = str(runtime.get("previous_active") or "-")
    request_id = str(runtime.get("request_id") or "-")
    return f"prev={previous} | req={request_id[:12]} | done={completed}"[:52]


def _format_last_switch_event(snapshot: dict, now: datetime) -> str:
    history = snapshot.get("switch_history") if isinstance(snapshot.get("switch_history"), list) else []
    if not history:
        return "-"
    event = history[-1] if isinstance(history[-1], dict) else {}
    outcome = str(event.get("outcome") or "-")
    reason = str(event.get("reason") or "-")
    trigger = str(event.get("trigger") or "-")
    when = _format_age(event.get("at"), now)
    return f"{outcome} | {reason} | {trigger} | {when}"[:52]

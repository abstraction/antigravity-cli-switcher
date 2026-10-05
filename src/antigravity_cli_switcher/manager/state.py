from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, overload

from antigravity_cli_switcher.manager.history import (
    _normalize_switch_history,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    default_live_dir,
    ensure_layout,
)
from antigravity_cli_switcher.manager.policy import (
    _normalize_quota_backend,
    _normalize_switch_mode,
    _normalize_switch_policy,
)

DEFAULT_REFRESH_POLICY_SECONDS = 1800
USAGE_WINDOW_NAMES = ("short", "weekly")
USAGE_FAMILY_NAMES = ("gemini", "other")
DEFAULT_SWITCH_DEDUPE_SECONDS = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _normalize_timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        parsed = parse_timestamp(value)
        return parsed.isoformat() if parsed is not None else value
    return None


def _default_usage_window() -> dict[str, object]:
    return {
        "status": "unknown",
        "value": None,
        "reset_at": None,
    }


def _default_usage_windows() -> dict[str, dict[str, object]]:
    return {name: _default_usage_window() for name in USAGE_WINDOW_NAMES}


def _normalize_window_map(windows: object) -> dict[str, dict[str, object]]:
    normalized = _default_usage_windows()
    if isinstance(windows, dict):
        for name in USAGE_WINDOW_NAMES:
            raw_window = windows.get(name)
            if isinstance(raw_window, dict):
                normalized[name] = {
                    "status": str(raw_window.get("status") or "unknown"),
                    "value": raw_window.get("value"),
                    "reset_at": _normalize_timestamp(raw_window.get("reset_at")),
                }
    return normalized


def _normalize_usage_windows(meta: dict) -> dict[str, dict[str, object]]:
    windows = meta.get("usage_windows")
    normalized = _normalize_window_map(windows)

    if not isinstance(windows, dict):
        if meta.get("usage_status") is not None:
            normalized["short"]["status"] = str(meta.get("usage_status") or "unknown")
        if meta.get("usage_value") is not None:
            normalized["short"]["value"] = meta.get("usage_value")
        if meta.get("reset_at") is not None:
            normalized["short"]["reset_at"] = _normalize_timestamp(meta.get("reset_at"))

    return normalized


def _default_usage_families() -> dict[str, dict[str, dict[str, object]]]:
    return {family: _default_usage_windows() for family in USAGE_FAMILY_NAMES}


def _normalize_usage_families(meta: dict) -> dict[str, dict[str, dict[str, object]]]:
    families = _default_usage_families()
    raw_families = meta.get("usage_families")
    if isinstance(raw_families, dict):
        for family in USAGE_FAMILY_NAMES:
            raw_windows = raw_families.get(family)
            if isinstance(raw_windows, dict):
                families[family] = _normalize_window_map(raw_windows)
        return families

    gemini_windows = _normalize_usage_windows(meta)
    families["gemini"] = gemini_windows
    return families


def _sync_legacy_usage_fields(account: dict) -> None:
    families = _normalize_usage_families(account)
    account["usage_families"] = families
    gemini_short = families["gemini"]["short"]
    gemini_weekly = families["gemini"]["weekly"]
    account["usage_windows"] = {
        "short": dict(gemini_short),
        "weekly": dict(gemini_weekly),
    }
    account["usage_status"] = gemini_short.get("status", "unknown")
    account["usage_value"] = gemini_short.get("value")
    account["reset_at"] = gemini_short.get("reset_at")


@overload
def _normalize_usage_family(value: object, *, allow_none: Literal[True]) -> str | None: ...


@overload
def _normalize_usage_family(value: object, *, allow_none: Literal[False] = False) -> str: ...


def _normalize_usage_family(value: object, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if isinstance(value, str):
        family = value.strip().lower()
        if family == "claude":
            family = "other"
        if family in USAGE_FAMILY_NAMES:
            return family
    raise ValueError(f"Usage family must be one of: {', '.join(USAGE_FAMILY_NAMES)}")


def _default_proxy_config() -> dict[str, object]:
    return {
        "enabled": False,
        "url": None,
        "label": None,
    }


def _normalize_proxy_config(raw: object) -> dict[str, object]:
    proxy = _default_proxy_config()
    if isinstance(raw, dict):
        proxy["enabled"] = bool(raw.get("enabled", False))
        url = raw.get("url")
        label = raw.get("label")
        proxy["url"] = str(url).strip() or None if isinstance(url, str) else None
        proxy["label"] = str(label).strip() or None if isinstance(label, str) else None
    if not proxy["url"]:
        proxy["enabled"] = False
    return proxy


def _default_switch_runtime() -> dict[str, object]:
    return {
        "status": "idle",
        "reason": None,
        "trigger": None,
        "request_id": None,
        "required_family": None,
        "active": None,
        "previous_active": None,
        "last_started_at": None,
        "last_completed_at": None,
    }


def _normalize_switch_runtime(raw: object) -> dict[str, object]:
    runtime = _default_switch_runtime()
    if isinstance(raw, dict):
        for key in runtime:
            runtime[key] = raw.get(key)
    status = str(runtime.get("status") or "idle").strip().lower()
    if status not in {"idle", "switching", "ready", "no_account"}:
        status = "idle"
    runtime["status"] = status
    for key in ("reason", "trigger", "request_id", "active", "previous_active", "last_started_at", "last_completed_at"):
        value = runtime.get(key)
        runtime[key] = value if isinstance(value, str) or value is None else str(value)
    return runtime


def _mark_switch_runtime(
    state: dict,
    *,
    status: str,
    reason: str | None = None,
    trigger: str | None = None,
    request_id: str | None = None,
    required_family: str | None = None,
    active: str | None = None,
    previous_active: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    runtime = _normalize_switch_runtime(state.get("switch_runtime"))
    runtime["status"] = status
    runtime["reason"] = reason
    runtime["trigger"] = trigger
    runtime["request_id"] = request_id
    runtime["required_family"] = required_family
    runtime["active"] = active
    runtime["previous_active"] = previous_active
    if started_at is not None:
        runtime["last_started_at"] = started_at
    if completed_at is not None:
        runtime["last_completed_at"] = completed_at
    state["switch_runtime"] = runtime


def get_live_dir(state: dict | None = None) -> Path | None:
    if state is None:
        return default_live_dir()
    if not isinstance(state, dict):
        return None
    live_dir_raw = state.get("live_dir")
    if isinstance(live_dir_raw, str) and live_dir_raw.strip():
        return Path(live_dir_raw.strip()).expanduser()
    return None


def set_live_dir(paths: ManagerPaths, live_dir: Path | None) -> None:
    from antigravity_cli_switcher.manager.accounts import _sync_runtime_to_live_dir

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        state["live_dir"] = str(live_dir.resolve()) if live_dir else None
        if state.get("active"):
            _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)


def load_state(paths: ManagerPaths) -> dict:
    ensure_layout(paths)
    try:
        with paths.state_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        data = {}
    data.setdefault("active", None)
    data.setdefault("accounts", {})
    data.setdefault("live_dir", str(default_live_dir(paths.root)))
    data["quota_backend"] = _normalize_quota_backend(data.get("quota_backend"))
    data["switch_mode"] = _normalize_switch_mode(data.get("switch_mode"))
    data["switch_policy"] = _normalize_switch_policy(data.get("switch_policy"))
    data["switch_runtime"] = _normalize_switch_runtime(data.get("switch_runtime"))
    data["switch_history"] = _normalize_switch_history(data.get("switch_history"))
    data.setdefault("fleet_utilization", {})

    return data


def save_state(paths: ManagerPaths, state: dict) -> None:
    from antigravity_cli_switcher.manager.utilization import reconcile_active_duty

    reconcile_active_duty(state)
    fd, temp_path = tempfile.mkstemp(dir=paths.state_file.parent, prefix=paths.state_file.name + "_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(temp_path, paths.state_file)
    except Exception:
        os.remove(temp_path)
        raise


def sync_state_from_disk(paths: ManagerPaths, state: dict) -> dict:
    disk_accounts = {p.name for p in paths.accounts_dir.iterdir() if p.is_dir()}
    tracked = state["accounts"]

    for name in sorted(disk_accounts):
        account_path = paths.accounts_dir / name
        try:
            created_at = datetime.fromtimestamp(account_path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            created_at = utc_now().isoformat()
        tracked.setdefault(
            name,
            {
                "enabled": True,
                "status": "standby",
                "last_error": None,
                "cooldown_until": None,
                "fail_count": 0,
                "created_at": created_at,
                "usage_windows": _default_usage_windows(),
                "usage_status": "unknown",
                "usage_value": None,
                "reset_at": None,
                "health_status": "unknown",
                "last_live_check_at": None,
                "last_live_check_error": None,
                "refresh_fail_count": 0,
                "next_live_check_at": None,
                "refresh_policy_seconds": DEFAULT_REFRESH_POLICY_SECONDS,
                "proxy": _default_proxy_config(),
            },
        )
        meta = tracked[name]
        meta.setdefault("created_at", created_at)
        meta.setdefault("usage_windows", _default_usage_windows())
        meta.setdefault("health_status", "unknown")
        meta.setdefault("last_live_check_at", None)
        meta.setdefault("last_live_check_error", None)
        meta.setdefault("refresh_fail_count", 0)
        meta.setdefault("next_live_check_at", None)
        meta.setdefault("refresh_policy_seconds", DEFAULT_REFRESH_POLICY_SECONDS)
        meta["proxy"] = _normalize_proxy_config(meta.get("proxy"))
        _sync_legacy_usage_fields(meta)
    for name in list(tracked):
        if name not in disk_accounts:
            tracked.pop(name, None)
            if state.get("active") == name:
                state["active"] = None

    fleet_util = state.setdefault("fleet_utilization", {})
    if isinstance(fleet_util, dict):
        from antigravity_cli_switcher.manager.utilization import _get_or_create_account_record

        fleet_accounts = fleet_util.get("accounts")
        if isinstance(fleet_accounts, dict):
            for name in list(fleet_accounts):
                if name not in disk_accounts:
                    fleet_accounts.pop(name, None)
        for name in disk_accounts:
            _get_or_create_account_record(fleet_util, name)
        if fleet_util.get("last_active_account") not in disk_accounts:
            fleet_util["last_active_account"] = ""

    active = state.get("active")
    for name, meta in tracked.items():
        cooldown_until = parse_timestamp(meta.get("cooldown_until"))
        in_cooldown = bool(cooldown_until and cooldown_until > utc_now())
        if name == active:
            meta["status"] = "active"
        elif not meta.get("enabled", True):
            meta["status"] = "disabled"
        elif in_cooldown:
            meta["status"] = "cooldown"
        else:
            meta["status"] = "standby"
    return state

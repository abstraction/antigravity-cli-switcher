from __future__ import annotations

from datetime import datetime, timezone

from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.policy import (
    DEFAULT_SWITCH_HISTORY_LIMIT,
    USAGE_FAMILY_NAMES,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            return value
    return None


def _normalize_switch_history(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, object]] = []
    for item in raw[-DEFAULT_SWITCH_HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "at": _normalize_timestamp(item.get("at")),
                "reason": item.get("reason")
                if isinstance(item.get("reason"), str) or item.get("reason") is None
                else str(item.get("reason")),
                "trigger": item.get("trigger")
                if isinstance(item.get("trigger"), str) or item.get("trigger") is None
                else str(item.get("trigger")),
                "request_id": item.get("request_id")
                if isinstance(item.get("request_id"), str) or item.get("request_id") is None
                else str(item.get("request_id")),
                "required_family": item.get("required_family")
                if item.get("required_family") in USAGE_FAMILY_NAMES
                else None,
                "previous_active": item.get("previous_active")
                if isinstance(item.get("previous_active"), str) or item.get("previous_active") is None
                else str(item.get("previous_active")),
                "active": item.get("active")
                if isinstance(item.get("active"), str) or item.get("active") is None
                else str(item.get("active")),
                "switched_to": item.get("switched_to")
                if isinstance(item.get("switched_to"), str) or item.get("switched_to") is None
                else str(item.get("switched_to")),
                "outcome": item.get("outcome")
                if isinstance(item.get("outcome"), str) or item.get("outcome") is None
                else str(item.get("outcome")),
                "cooldown_minutes": int(item.get("cooldown_minutes", 0) or 0),
            }
        )
    return entries


def _append_switch_history(
    state: dict,
    *,
    reason: str | None,
    trigger: str | None,
    request_id: str | None,
    previous_active: str | None,
    active: str | None,
    switched_to: str | None,
    outcome: str | None,
    cooldown_minutes: int,
    required_family: str | None = None,
    at: str | None = None,
) -> None:
    history = _normalize_switch_history(state.get("switch_history"))
    history.append(
        {
            "at": at or _utc_now().isoformat(),
            "reason": reason,
            "trigger": trigger,
            "request_id": request_id,
            "required_family": required_family,
            "previous_active": previous_active,
            "active": active,
            "switched_to": switched_to,
            "outcome": outcome,
            "cooldown_minutes": int(cooldown_minutes or 0),
        }
    )
    state["switch_history"] = history[-DEFAULT_SWITCH_HISTORY_LIMIT:]


def get_switch_history(paths: ManagerPaths, limit: int = 10) -> list[dict[str, object]]:
    from antigravity_cli_switcher.manager.state import load_state, sync_state_from_disk

    state = sync_state_from_disk(paths, load_state(paths))
    raw_history = state.get("switch_history")
    history = _normalize_switch_history(raw_history)
    safe_limit = max(1, limit)
    return history[-safe_limit:]

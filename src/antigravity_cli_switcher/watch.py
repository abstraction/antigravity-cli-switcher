"""Watch Antigravity CLI logs and fail over when quota is exhausted.

`agy` never calls the manager. Cached Cloud Code usage is advisory. This module
tails the live CLI log files and treats a real `RESOURCE_EXHAUSTED` / Individual
quota banner as the failover signal.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from antigravity_cli_switcher.log import get_logger
from antigravity_cli_switcher.watch_tailer import (
    DEFAULT_WATCH_COOLDOWN_MINUTES,
    DEFAULT_WATCH_POLL_SECONDS,
    INDIVIDUAL_QUOTA_RE,
    LOG_WATCH_STATE_NAME,
    MAX_EVENT_LINE_CHARS,
    RESET_HINT_RE,
    RESOURCE_EXHAUSTED_RE,
    WEEKLY_QUOTA_RE,
    QuotaLogEvent,
    WatchPollResult,
    _arm_restart,
    _disarm_restart,
    _utc_now_iso,
    clear_restart_required,
    consume_log_events,
    get_log_watch_snapshot,
    initial_log_offset,
    iter_live_agy_log_files,
    load_log_watch_state,
    log_watch_state_path,
    parse_quota_log_line,
    read_new_complete_lines,
    resolve_antigravity_cli_dir,
    save_log_watch_state,
)

if TYPE_CHECKING:
    from antigravity_cli_switcher.manager.failover import RotationResult
    from antigravity_cli_switcher.manager.paths import ManagerPaths

__all__ = [
    "DEFAULT_WATCH_COOLDOWN_MINUTES",
    "DEFAULT_WATCH_POLL_SECONDS",
    "INDIVIDUAL_QUOTA_RE",
    "LOG_WATCH_STATE_NAME",
    "MAX_EVENT_LINE_CHARS",
    "RESET_HINT_RE",
    "RESOURCE_EXHAUSTED_RE",
    "WEEKLY_QUOTA_RE",
    "QuotaLogEvent",
    "WatchPollResult",
    "clear_restart_required",
    "consume_log_events",
    "format_watch_poll",
    "get_log_watch_snapshot",
    "initial_log_offset",
    "iter_live_agy_log_files",
    "load_log_watch_state",
    "log_watch_state_path",
    "parse_quota_log_line",
    "poll_quota_logs",
    "read_new_complete_lines",
    "resolve_antigravity_cli_dir",
    "save_log_watch_state",
    "watch_poll_payload",
    "watch_quota_logs",
]


def _rotation_payload(result: RotationResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "previous_active": result.previous_active,
        "active": result.active,
        "switched_to": result.switched_to,
        "marked_bad": result.marked_bad,
        "reason": result.reason,
        "cooldown_minutes": result.cooldown_minutes,
        "outcome": result.outcome,
    }


def _record_last_event(watch_state: dict, events: list[QuotaLogEvent]) -> None:
    if not events:
        return
    watch_state["last_event_at"] = _utc_now_iso()
    watch_state["last_kind"] = events[-1].kind
    watch_state["last_path"] = events[-1].path


def poll_quota_logs(
    paths: ManagerPaths,
    *,
    from_start: bool = False,
    started_at: float | None = None,
    rotate: bool = True,
    force_switch: bool = False,
    cooldown_minutes: int = DEFAULT_WATCH_COOLDOWN_MINUTES,
    on_rotate: str | None = None,
) -> WatchPollResult:
    from antigravity_cli_switcher.manager import (
        get_live_dir,
        get_switch_mode,
        load_state,
        manager_lock,
        rotate_after_failure_locked,
    )

    logger = get_logger(paths.root)
    on_rotate_cmd = None
    with manager_lock(paths):
        state = load_state(paths)
        live_dir = get_live_dir(state)
        switch_mode = get_switch_mode(state)
        watch_state = load_log_watch_state(paths.root)
        cursors = dict(watch_state.get("cursors") or {})
        initialized = bool(watch_state.get("initialized"))
        next_cursors, events = consume_log_events(
            live_dir,
            cursors,
            from_start=from_start,
            started_at=started_at,
            initialized=initialized,
        )
        watch_state["cursors"] = next_cursors
        watch_state["initialized"] = True
        rotation = None
        rotated = False
        message = "no quota errors"

        ignored_logs = set(watch_state.get("restart_source_logs") or [])
        new_session_logs = {key for key in next_cursors if key not in ignored_logs}
        if watch_state.get("restart_required") and new_session_logs:
            _disarm_restart(watch_state, keep_source_logs=True)

        events_for_rotate = [event for event in events if event.path not in ignored_logs]

        if events:
            _record_last_event(watch_state, events)

        if not events_for_rotate:
            if events and watch_state.get("restart_required"):
                message = "Hit quota limit. Restart your CLI process to continue."
            elif events and ignored_logs:
                message = "CLI restart detected. Ignoring old quota errors."
        elif events_for_rotate:
            should_rotate = rotate and (force_switch or switch_mode == "auto")
            if should_rotate:
                logger.info("Log-watch initiating rotate_after_failure due to quota limit")
                rotation = rotate_after_failure_locked(
                    paths,
                    reason="quota",
                    cooldown_minutes=cooldown_minutes,
                    force_switch=force_switch,
                    dedupe_seconds=0,
                    trigger="log-watch",
                )
                rotated = rotation.outcome == "switched"
                if rotated:
                    logger.info(f"Log-watch successfully rotated to {rotation.switched_to}")
                    _arm_restart(
                        watch_state,
                        account=rotation.switched_to,
                        source_logs=sorted(next_cursors),
                    )
                    message = (
                        f"Switched from {rotation.previous_active} to {rotation.switched_to}. "
                        "Restart your CLI process to use the new token."
                    )
                    if on_rotate:
                        on_rotate_cmd = on_rotate
                elif rotation.outcome == "already_switched":
                    logger.info(f"Log-watch skipped rotation: already switched to {rotation.active}")
                    message = f"Already switched to {rotation.active or '-'}"
                elif switch_mode == "manual" and not force_switch:
                    logger.info("Log-watch skipped rotation: manual mode")
                    message = "Hit quota limit, but auto-switch is disabled."
                else:
                    logger.info(f"Log-watch rotation failed or skipped: outcome={rotation.outcome}")
                    message = f"Hit quota limit. Switch result: {rotation.outcome}"
            else:
                logger.info(f"Log-watch skipped rotation: switch_mode={switch_mode}, force_switch={force_switch}")
                message = f"Hit quota limit ({events_for_rotate[-1].kind}). Auto-switch skipped."
                if switch_mode == "manual" and not force_switch:
                    message = "Hit quota limit, but auto-switch is disabled."

        save_log_watch_state(paths.root, watch_state)
        result = WatchPollResult(
            events=events,
            rotated=rotated,
            rotation=rotation,
            switch_mode=switch_mode,
            restart_required=bool(watch_state.get("restart_required")),
            message=message,
            files_tracked=len(next_cursors),
        )
    if on_rotate_cmd:
        subprocess.run(on_rotate_cmd, shell=True, check=False)
    return result


def watch_poll_payload(result: WatchPollResult) -> dict:
    return {
        "events": [asdict(event) for event in result.events],
        "rotated": result.rotated,
        "rotation": _rotation_payload(result.rotation),
        "switch_mode": result.switch_mode,
        "restart_required": result.restart_required,
        "message": result.message,
        "files_tracked": result.files_tracked,
    }


def format_watch_poll(result: WatchPollResult) -> str:
    return f"Watch: {result.message}"


def watch_quota_logs(
    paths: ManagerPaths,
    *,
    follow: bool = True,
    once: bool = False,
    from_start: bool = False,
    poll_seconds: float = DEFAULT_WATCH_POLL_SECONDS,
    rotate: bool = True,
    force_switch: bool = False,
    cooldown_minutes: int = DEFAULT_WATCH_COOLDOWN_MINUTES,
    on_rotate: str | None = None,
    as_json: bool = False,
    printer=print,
) -> int:
    started_at = time.time()
    interval = poll_seconds if poll_seconds > 0 else DEFAULT_WATCH_POLL_SECONDS
    first = True
    while True:
        result = poll_quota_logs(
            paths,
            from_start=from_start and first,
            started_at=started_at,
            rotate=rotate,
            force_switch=force_switch,
            cooldown_minutes=cooldown_minutes,
            on_rotate=on_rotate,
        )
        first = False
        if result.events or result.rotated or once or as_json:
            if as_json:
                printer(json.dumps(watch_poll_payload(result), indent=2, sort_keys=True))
            elif result.events or result.rotated or once:
                printer(format_watch_poll(result))
        if once or not follow:
            return 0
        time.sleep(interval)

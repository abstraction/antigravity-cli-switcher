from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING

from antigravity_cli_switcher.log import get_logger
from antigravity_cli_switcher.manager.candidates import _best_switch_candidate
from antigravity_cli_switcher.manager.history import _append_switch_history
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.policy import get_switch_mode
from antigravity_cli_switcher.manager.state import (
    DEFAULT_SWITCH_DEDUPE_SECONDS,
    _mark_switch_runtime,
    _normalize_switch_runtime,
    _normalize_usage_family,
    get_live_dir,
    load_state,
    parse_timestamp,
    save_state,
    sync_state_from_disk,
    utc_now,
)

if TYPE_CHECKING:
    pass


@dataclass
class RotationResult:
    previous_active: str | None
    active: str | None
    switched_to: str | None
    marked_bad: bool
    reason: str | None
    cooldown_minutes: int
    outcome: str = "unknown"


def rotate_after_failure(
    paths: ManagerPaths,
    reason: str,
    cooldown_minutes: int = 60,
    live_dir: Path | None = None,
    force_switch: bool = False,
    dedupe_seconds: int = DEFAULT_SWITCH_DEDUPE_SECONDS,
    trigger: str = "unknown",
    request_id: str | None = None,
    required_family: str | None = None,
) -> RotationResult:
    logger = get_logger(paths.root)
    if cooldown_minutes < 0:
        raise ValueError("Cooldown minutes must be non-negative.")

    with manager_lock(paths):
        result = rotate_after_failure_locked(
            paths,
            reason,
            cooldown_minutes=cooldown_minutes,
            live_dir=live_dir,
            force_switch=force_switch,
            dedupe_seconds=dedupe_seconds,
            trigger=trigger,
            request_id=request_id,
            required_family=required_family,
        )
        logger.info(f"rotate_after_failure finished: outcome={result.outcome}, switched_to={result.switched_to}")
        return result


def rotate_after_failure_locked(
    paths: ManagerPaths,
    reason: str,
    cooldown_minutes: int = 60,
    live_dir: Path | None = None,
    force_switch: bool = False,
    dedupe_seconds: int = DEFAULT_SWITCH_DEDUPE_SECONDS,
    trigger: str = "unknown",
    request_id: str | None = None,
    required_family: str | None = None,
) -> RotationResult:
    from antigravity_cli_switcher.manager.accounts import (
        _copy_active_runtime,
        _sync_runtime_to_live_dir,
    )

    logger = get_logger(paths.root)
    logger.info(f"rotate_after_failure_locked called: reason={reason}, trigger={trigger}, force_switch={force_switch}")
    if cooldown_minutes < 0:
        raise ValueError("Cooldown minutes must be non-negative.")

    family = _normalize_usage_family(required_family, allow_none=True)
    state = sync_state_from_disk(paths, load_state(paths))
    if live_dir is not None:
        state["live_dir"] = str(live_dir.resolve())
    switch_mode = get_switch_mode(state)
    runtime = _normalize_switch_runtime(state.get("switch_runtime"))
    now = utc_now()
    now_iso = now.isoformat()

    raw_last_completed = runtime.get("last_completed_at")
    completed_at = str(raw_last_completed) if isinstance(raw_last_completed, str) else None
    last_completed_at = parse_timestamp(completed_at)

    raw_prev = runtime.get("previous_active")
    previous_active = str(raw_prev) if isinstance(raw_prev, str) else None
    raw_started = runtime.get("last_started_at")
    started_at = str(raw_started) if isinstance(raw_started, str) else None

    if (
        dedupe_seconds > 0
        and runtime.get("status") == "ready"
        and runtime.get("reason") == reason
        and runtime.get("required_family") == family
        and last_completed_at is not None
        and (now - last_completed_at).total_seconds() <= dedupe_seconds
        and state.get("active")
    ):
        _mark_switch_runtime(
            state,
            status="ready",
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            required_family=family,
            active=state.get("active"),
            previous_active=previous_active,
            started_at=started_at,
            completed_at=completed_at,
        )
        _append_switch_history(
            state,
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            previous_active=previous_active,
            active=state.get("active"),
            switched_to=None,
            outcome="already_switched",
            cooldown_minutes=0,
            required_family=family,
            at=completed_at,
        )
        save_state(paths, state)
        return RotationResult(
            previous_active=previous_active,
            active=state.get("active"),
            switched_to=None,
            marked_bad=False,
            reason=reason,
            cooldown_minutes=0,
            outcome="already_switched",
        )

    previous = state.get("active")
    _mark_switch_runtime(
        state,
        status="switching",
        reason=reason,
        trigger=trigger,
        request_id=request_id,
        required_family=family,
        active=previous,
        previous_active=previous,
        started_at=now_iso,
        completed_at=None,
    )
    save_state(paths, state)
    if not previous:
        _mark_switch_runtime(
            state,
            status="no_account",
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            required_family=family,
            active=None,
            previous_active=None,
            completed_at=utc_now().isoformat(),
        )
        _append_switch_history(
            state,
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            previous_active=None,
            active=None,
            switched_to=None,
            outcome="no_active",
            cooldown_minutes=cooldown_minutes,
            required_family=family,
        )
        save_state(paths, state)
        return RotationResult(
            previous_active=None,
            active=None,
            switched_to=None,
            marked_bad=False,
            reason=reason,
            cooldown_minutes=cooldown_minutes,
            outcome="no_active",
        )

    meta = state["accounts"].get(previous)
    if meta is None:
        state["active"] = None
        _mark_switch_runtime(
            state,
            status="no_account",
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            required_family=family,
            active=None,
            previous_active=previous,
            completed_at=utc_now().isoformat(),
        )
        _append_switch_history(
            state,
            reason=reason,
            trigger=trigger,
            request_id=request_id,
            previous_active=previous,
            active=None,
            switched_to=None,
            outcome="active_missing",
            cooldown_minutes=cooldown_minutes,
            required_family=family,
        )
        save_state(paths, state)
        return RotationResult(
            previous_active=previous,
            active=None,
            switched_to=None,
            marked_bad=False,
            reason=reason,
            cooldown_minutes=cooldown_minutes,
            outcome="active_missing",
        )

    meta["last_error"] = reason
    meta["fail_count"] = int(meta.get("fail_count", 0)) + 1
    if family is not None:
        family_cooldowns = meta.get("family_cooldowns")
        if not isinstance(family_cooldowns, dict):
            family_cooldowns = {}
        family_cooldowns[family] = (
            (utc_now() + timedelta(minutes=cooldown_minutes)).isoformat() if cooldown_minutes > 0 else None
        )
        meta["family_cooldowns"] = family_cooldowns
        meta["cooldown_until"] = None
    elif cooldown_minutes > 0:
        meta["cooldown_until"] = (utc_now() + timedelta(minutes=cooldown_minutes)).isoformat()
    else:
        meta["cooldown_until"] = None
    state["active"] = None
    state = sync_state_from_disk(paths, state)

    switched_to = None
    from antigravity_cli_switcher.manager.keyring import _is_test_environment
    from antigravity_cli_switcher.manager.stealth import (
        get_agy_quiescence_remaining,
        get_switch_cooldown_remaining,
    )

    should_enforce_cooldown = not _is_test_environment() or bool(os.getenv("ACS_ENFORCE_COOLDOWN"))
    cooldown_rem = get_switch_cooldown_remaining(state)
    quiescence_rem = get_agy_quiescence_remaining(
        state=state, live_dir=get_live_dir(state), paths=paths, running_pids=[]
    )
    effective_cooldown = max(cooldown_rem, quiescence_rem)
    cooldown_blocked = False

    if force_switch or switch_mode == "auto":
        if not force_switch and should_enforce_cooldown and effective_cooldown > 0:
            cooldown_blocked = True
            rem_int = max(1, ceil(effective_cooldown))
            logger.warning(f"Automated failover switch blocked by stealth cooldown: {rem_int}s remaining")
        else:
            switched_to = _best_switch_candidate(paths, state, exclude=previous, required_family=family)
            if switched_to:
                from antigravity_cli_switcher.manager.login import _ensure_safe_account_switch

                terminated = _ensure_safe_account_switch()
                if terminated:
                    runtime["last_process_kill_at"] = utc_now().isoformat()
                _copy_active_runtime(paths, switched_to)
                state["active"] = switched_to
                state = sync_state_from_disk(paths, state)
                _sync_runtime_to_live_dir(paths, state)
                try:
                    from antigravity_cli_switcher.manager.notify import notify_account_armed

                    notify_account_armed(switched_to)
                except Exception:
                    pass

    kill_at = runtime.get("last_process_kill_at")
    _mark_switch_runtime(
        state,
        status="ready" if state.get("active") else "no_account",
        reason=reason,
        trigger=trigger,
        request_id=request_id,
        required_family=family,
        active=state.get("active"),
        previous_active=previous,
        completed_at=utc_now().isoformat(),
        last_account_switch_at=now_iso if switched_to else None,
        last_process_kill_at=str(kill_at) if kill_at else None,
    )
    if state.get("active"):
        outcome = "switched"
    elif cooldown_blocked:
        outcome = "cooldown_blocked"
    elif switch_mode != "auto":
        outcome = "switch_disabled"
    else:
        outcome = "no_candidate"
    _append_switch_history(
        state,
        reason=reason,
        trigger=trigger,
        request_id=request_id,
        previous_active=previous,
        active=state.get("active"),
        switched_to=switched_to,
        outcome=outcome,
        cooldown_minutes=cooldown_minutes,
        required_family=family,
    )
    save_state(paths, state)
    return RotationResult(
        previous_active=previous,
        active=state.get("active"),
        switched_to=switched_to,
        marked_bad=True,
        reason=reason,
        cooldown_minutes=cooldown_minutes,
        outcome=outcome,
    )

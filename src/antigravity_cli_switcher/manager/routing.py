from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from antigravity_cli_switcher.manager.candidates import (
    _account_can_serve_family,
    _best_switch_candidate,
    _cooldown_minutes_from_family_quota,
    _is_family_quota_exhausted,
    _refresh_failure_threshold_reached,
)
from antigravity_cli_switcher.manager.failover import (
    rotate_after_failure,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.policy import (
    DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD,
    DEFAULT_SWITCH_MODE,
    _default_switch_policy,
    _normalize_family_fallback_strategy,
    _state_switch_policy,
    get_switch_mode,
)
from antigravity_cli_switcher.manager.state import (
    DEFAULT_REFRESH_POLICY_SECONDS,
    _normalize_usage_family,
    load_state,
    parse_timestamp,
    save_state,
    sync_state_from_disk,
    utc_now,
)

if TYPE_CHECKING:
    from antigravity_cli_switcher.manager.quota import UsageRefreshResult


@dataclass
class RouteResult:
    preferred_family: str
    selected_family: str | None
    previous_active: str | None
    active: str | None
    switched_to: str | None
    fallback_strategy: str
    outcome: str
    recommended_account: str | None = None


@dataclass
class EnsureActiveResult:
    triggered: bool
    switch_mode: str
    previous_active: str | None
    active: str | None
    switched_to: str | None
    reason: str | None
    cooldown_minutes: int
    required_family: str | None = None


def _account_due_for_refresh(meta: dict, now: datetime | None = None) -> bool:
    current = now or utc_now()
    if not isinstance(meta, dict):
        return False
    if not meta.get("enabled", True):
        return False
    status = meta.get("status") or "standby"
    if status in {"disabled", "cooldown"}:
        return False
    health_status = meta.get("health_status")
    if health_status in {"token_stale", "quota_stale"}:
        return True
    next_check = parse_timestamp(meta.get("next_live_check_at"))
    if next_check is not None:
        return next_check <= current
    policy = int(meta.get("refresh_policy_seconds", DEFAULT_REFRESH_POLICY_SECONDS) or DEFAULT_REFRESH_POLICY_SECONDS)
    if policy <= 0:
        return False
    last_check = parse_timestamp(meta.get("last_live_check_at"))
    if last_check is None:
        return True
    return last_check + timedelta(seconds=policy) <= current


def resolve_route(
    paths: ManagerPaths,
    preferred_family: str,
    *,
    fallback_strategy: str | None = None,
    force_switch: bool = False,
) -> RouteResult:
    from antigravity_cli_switcher.manager.accounts import (
        _copy_active_runtime,
        _sync_runtime_to_live_dir,
    )

    family = _normalize_usage_family(preferred_family)
    if not family:
        raise ValueError(f"Invalid preferred family: {preferred_family}")
    alternate = "other" if family == "gemini" else "gemini"
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        policy = _state_switch_policy(state)
        strategy = (
            _normalize_family_fallback_strategy(fallback_strategy)
            if fallback_strategy is not None
            else policy["family_fallback_strategy"]
        )
        if fallback_strategy is not None and strategy != fallback_strategy.strip().lower().replace("_", "-"):
            raise ValueError(f"Unsupported family fallback strategy: {fallback_strategy}")

        previous: str | None = state.get("active")
        current_meta = state["accounts"].get(previous) if previous else None
        now = utc_now()
        if (
            isinstance(previous, str)
            and isinstance(current_meta, dict)
            and _account_can_serve_family(paths, previous, current_meta, family, policy, now)
        ):
            return RouteResult(family, family, previous, previous, None, str(strategy), "active_ready")

        same_family_account = _best_switch_candidate(paths, state, exclude=previous, required_family=family)
        alternate_current = (
            previous
            if isinstance(previous, str)
            and isinstance(current_meta, dict)
            and strategy != "strict-family"
            and _account_can_serve_family(paths, previous, current_meta, alternate, policy, now)
            else None
        )
        alternate_account = (
            _best_switch_candidate(paths, state, exclude=previous, required_family=alternate)
            if strategy != "strict-family"
            else None
        )

        choices: tuple[tuple[str | None, str], ...]
        if strategy == "same-account-first":
            choices = ((alternate_current, alternate), (same_family_account, family), (alternate_account, alternate))
        elif strategy == "strict-family":
            choices = ((same_family_account, family),)
        else:
            choices = ((same_family_account, family), (alternate_current, alternate), (alternate_account, alternate))

        selected_account = None
        selected_family = None
        for account_name, candidate_family in choices:
            if account_name:
                selected_account = account_name
                selected_family = candidate_family
                break
        if selected_account is None or selected_family is None:
            return RouteResult(family, None, previous, previous, None, str(strategy), "no_route")

        if selected_account == previous:
            return RouteResult(family, selected_family, previous, previous, None, str(strategy), "family_fallback")
        if get_switch_mode(state) != "auto" and not force_switch:
            return RouteResult(
                family, selected_family, previous, previous, None, str(strategy), "switch_required", selected_account
            )

        _copy_active_runtime(paths, selected_account)
        state["active"] = selected_account
        state = sync_state_from_disk(paths, state)
        _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)
        outcome = "account_switch" if selected_family == family else "account_and_family_fallback"
        return RouteResult(
            family, selected_family, previous, selected_account, selected_account, str(strategy), outcome
        )


def pick_due_refresh_account(paths: ManagerPaths, exclude: set[str] | None = None) -> str | None:
    exclude_set = exclude or set()
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        now = utc_now()
        active_name = state.get("active")
        if active_name and active_name not in exclude_set:
            active_meta = state["accounts"].get(active_name)
            if isinstance(active_meta, dict) and _account_due_for_refresh(active_meta, now):
                return active_name
        for name, meta in sorted(state["accounts"].items()):
            if name == active_name or name in exclude_set:
                continue
            if _account_due_for_refresh(meta, now):
                return name
    return None


def ensure_active_account(
    paths: ManagerPaths,
    *,
    force: bool = False,
    required_family: str | None = None,
) -> EnsureActiveResult:
    from antigravity_cli_switcher.manager.accounts import get_status_snapshot

    family = _normalize_usage_family(required_family, allow_none=True)
    snapshot = get_status_snapshot(paths)
    switch_mode = snapshot.get("switch_mode", DEFAULT_SWITCH_MODE)
    switch_policy = snapshot.get("switch_policy") or _default_switch_policy()
    active_name = snapshot.get("active")
    accounts = snapshot.get("accounts", {})
    now = utc_now()

    if switch_mode != "auto" and not force:
        return EnsureActiveResult(
            triggered=False,
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=active_name,
            switched_to=None,
            reason=None,
            cooldown_minutes=0,
            required_family=family,
        )

    if not active_name:
        rotation = rotate_after_failure(
            paths,
            "no_active_account",
            cooldown_minutes=0,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=None,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    active_meta = accounts.get(active_name)
    if not isinstance(active_meta, dict):
        rotation = rotate_after_failure(
            paths,
            "active_account_missing",
            cooldown_minutes=0,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    health_status = str(active_meta.get("health_status") or "unknown")
    if not active_meta.get("enabled", True):
        rotation = rotate_after_failure(
            paths,
            "active_account_disabled",
            cooldown_minutes=0,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    if health_status in {"auth_missing", "auth_expired", "ineligible"}:
        rotation = rotate_after_failure(
            paths,
            f"health_{health_status}",
            cooldown_minutes=60,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    raw_threshold = switch_policy.get("refresh_failure_threshold") if isinstance(switch_policy, dict) else None
    threshold = (
        int(raw_threshold)
        if isinstance(raw_threshold, (int, str)) and raw_threshold
        else DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD
    )
    if _refresh_failure_threshold_reached(active_meta, threshold):
        rotation = rotate_after_failure(
            paths,
            "refresh_failure_threshold_reached",
            cooldown_minutes=30,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    target_family = family or "gemini"
    raw_family_thresholds = switch_policy.get("family_thresholds") if isinstance(switch_policy, dict) else None
    family_thresholds = raw_family_thresholds if isinstance(raw_family_thresholds, dict) else {}
    fallback_threshold = (
        float(str(switch_policy.get("short_usage_threshold_percent", 10.0)))
        if isinstance(switch_policy, dict)
        else 10.0
    )
    raw_thresh = family_thresholds.get(target_family)
    threshold_percent = float(str(raw_thresh)) if raw_thresh is not None else fallback_threshold
    if _is_family_quota_exhausted(active_meta, now, threshold_percent=threshold_percent, family=target_family):
        cooldown_minutes = _cooldown_minutes_from_family_quota(
            active_meta,
            now,
            threshold_percent=threshold_percent,
            family=target_family,
        )
        rotation = rotate_after_failure(
            paths,
            f"{target_family}_quota_exhausted",
            cooldown_minutes=cooldown_minutes,
            force_switch=True,
            trigger="ensure_active",
            required_family=family,
        )
        return EnsureActiveResult(
            triggered=rotation.outcome in {"switched", "already_switched"},
            switch_mode=str(switch_mode),
            previous_active=active_name,
            active=rotation.active,
            switched_to=rotation.switched_to,
            reason=rotation.reason,
            cooldown_minutes=rotation.cooldown_minutes,
            required_family=family,
        )

    return EnsureActiveResult(
        triggered=False,
        switch_mode=str(switch_mode),
        previous_active=active_name,
        active=active_name,
        switched_to=None,
        reason=None,
        cooldown_minutes=0,
        required_family=family,
    )


def refresh_due_account(
    paths: ManagerPaths,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> UsageRefreshResult | None:
    from antigravity_cli_switcher.manager.quota import refresh_account_usage

    target = pick_due_refresh_account(paths)
    if not target:
        return None
    return refresh_account_usage(
        paths,
        name=target,
        agy_binary=agy_binary,
        timeout_seconds=timeout_seconds,
    )

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING

from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.policy import (
    DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD,
    DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
    _state_switch_policy,
)
from antigravity_cli_switcher.manager.state import (
    USAGE_WINDOW_NAMES,
    _normalize_usage_families,
    _normalize_usage_family,
    parse_timestamp,
    utc_now,
)

if TYPE_CHECKING:
    pass

from antigravity_cli_switcher.models import HealthStatus


def _eligible_switch_candidates(state: dict, exclude: str | None = None) -> list[str]:
    return [
        name
        for name, meta in sorted(state["accounts"].items())
        if name != exclude and meta.get("enabled", True) and meta.get("status") != "cooldown"
    ]


def _usage_windows_for_family(meta: dict, family: str) -> dict:
    normalized_family = _normalize_usage_family(family)
    families = _normalize_usage_families(meta)
    return families[normalized_family]


def _is_short_window_exhausted(
    meta: dict,
    now: datetime | None = None,
    *,
    threshold_percent: float = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
    family: str = "gemini",
) -> bool:
    return _is_usage_window_exhausted(
        meta,
        "short",
        now,
        threshold_percent=threshold_percent,
        family=family,
    )


def _is_usage_window_exhausted(
    meta: dict,
    window_name: str,
    now: datetime | None = None,
    *,
    threshold_percent: float = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
    family: str = "gemini",
) -> bool:
    current = now or utc_now()
    windows = _usage_windows_for_family(meta, family)
    window = windows.get(window_name, {})
    if window.get("status") != "known":
        return False
    value = _coerce_usage_value(window.get("value"))
    if value is None or value > threshold_percent:
        return False
    reset_at = parse_timestamp(window.get("reset_at"))
    if reset_at is not None and reset_at <= current:
        return False
    return True


def _is_family_quota_exhausted(
    meta: dict,
    now: datetime | None = None,
    *,
    threshold_percent: float = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
    family: str = "gemini",
) -> bool:
    return any(
        _is_usage_window_exhausted(
            meta,
            window_name,
            now,
            threshold_percent=threshold_percent,
            family=family,
        )
        for window_name in USAGE_WINDOW_NAMES
    )


def _cooldown_minutes_from_family_quota(
    meta: dict,
    now: datetime | None = None,
    *,
    threshold_percent: float = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
    family: str = "gemini",
) -> int:
    current = now or utc_now()
    windows = _usage_windows_for_family(meta, family)
    blocking_resets = [
        reset_at
        for window_name in USAGE_WINDOW_NAMES
        if _is_usage_window_exhausted(
            meta,
            window_name,
            current,
            threshold_percent=threshold_percent,
            family=family,
        )
        for reset_at in [parse_timestamp(windows.get(window_name, {}).get("reset_at"))]
        if reset_at is not None and reset_at > current
    ]
    if not blocking_resets:
        return 60
    delta_seconds = max(60.0, (max(blocking_resets) - current).total_seconds())
    return max(1, math.ceil(delta_seconds / 60.0))


def _refresh_failure_threshold_reached(meta: dict, threshold: int = DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD) -> bool:
    return int(meta.get("refresh_fail_count", 0) or 0) >= threshold


def _coerce_usage_value(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _candidate_usage_value(meta: dict, window_name: str, *, family: str = "gemini") -> float | None:
    windows = _usage_windows_for_family(meta, family)
    window = windows.get(window_name, {})
    if not isinstance(window, dict):
        return None
    return _coerce_usage_value(window.get("value"))


def _candidate_health_priority(health: HealthStatus) -> int:
    order = {
        HealthStatus.HEALTHY: 0,
        HealthStatus.READY: 1,
        HealthStatus.STALE: 2,
        HealthStatus.TOKEN_STALE: 2,
        HealthStatus.QUOTA_STALE: 2,
        HealthStatus.REFRESH_FAILED: 3,
        HealthStatus.AUTH_EXPIRED: 4,
        HealthStatus.AUTH_MISSING: 5,
        HealthStatus.COOLDOWN: 6,
        HealthStatus.DISABLED: 7,
        HealthStatus.INELIGIBLE: 8,
    }
    return order.get(health, 9)


def _family_cooldown_active(meta: dict, family: str, now: datetime | None = None) -> bool:
    cooldowns = meta.get("family_cooldowns")
    if not isinstance(cooldowns, dict):
        return False
    until = parse_timestamp(cooldowns.get(family))
    return until is not None and until > (now or utc_now())


def _best_switch_candidate(
    paths: ManagerPaths,
    state: dict,
    *,
    exclude: str | None = None,
    required_family: str | None = None,
) -> str | None:
    from antigravity_cli_switcher.manager.verification import _derive_health_status
    from antigravity_cli_switcher.models import AccountMeta, HealthStatus

    policy = _state_switch_policy(state)
    strategy = policy["candidate_strategy"]
    family = _normalize_usage_family(required_family, allow_none=True)
    family_thresholds = policy.get("family_thresholds")
    fallback_threshold = float(str(policy["short_usage_threshold_percent"]))
    if isinstance(family_thresholds, dict) and family in family_thresholds:
        threshold_percent = float(family_thresholds[family])
    else:
        threshold_percent = fallback_threshold
    candidates = _eligible_switch_candidates(state, exclude=exclude)
    if not candidates:
        return None

    ranked: list[tuple[tuple[object, ...], str]] = []
    current = utc_now()
    for name in candidates:
        meta_dict = state["accounts"].get(name)
        if not isinstance(meta_dict, dict):
            continue
        meta = AccountMeta(**meta_dict)
        health = _derive_health_status(paths, name, meta)
        if health in {
            HealthStatus.AUTH_MISSING,
            HealthStatus.AUTH_EXPIRED,
            HealthStatus.DISABLED,
            HealthStatus.COOLDOWN,
            HealthStatus.INELIGIBLE,
        }:
            continue
        if family is not None and _family_cooldown_active(meta_dict, family, current):
            continue

        short_value = _candidate_usage_value(meta_dict, "short", family=family or "gemini")
        weekly_value = _candidate_usage_value(meta_dict, "weekly", family=family or "gemini")
        short_known = short_value is not None
        quota_low = _is_family_quota_exhausted(
            meta_dict,
            current,
            threshold_percent=threshold_percent,
            family=family or "gemini",
        )
        weekly_known = weekly_value is not None

        if family is not None and quota_low:
            continue

        score: tuple[object, ...]
        if strategy == "highest-short":
            score = (
                0 if short_known else 1,
                -(short_value if short_value is not None else -1.0),
                _candidate_health_priority(health),
                int(meta_dict.get("refresh_fail_count", 0) or 0),
                int(meta_dict.get("fail_count", 0) or 0),
                str(meta_dict.get("created_at") or ""),
                name.lower(),
            )
        elif strategy == "round-robin":
            score = (
                _candidate_health_priority(health),
                0 if short_known and not quota_low else 1,
                str(meta_dict.get("created_at") or ""),
                name.lower(),
            )
        else:
            score = (
                _candidate_health_priority(health),
                0 if short_known and not quota_low else 1,
                0 if short_known else 1,
                -(short_value if short_value is not None else -1.0),
                0 if weekly_known else 1,
                -(weekly_value if weekly_value is not None else -1.0),
                int(meta_dict.get("refresh_fail_count", 0) or 0),
                int(meta_dict.get("fail_count", 0) or 0),
                str(meta_dict.get("created_at") or ""),
                name.lower(),
            )
        ranked.append((score, name))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _account_can_serve_family(
    paths: ManagerPaths,
    name: str,
    meta_dict: dict,
    family: str,
    policy: dict,
    now: datetime,
) -> bool:
    from antigravity_cli_switcher.manager.verification import _derive_health_status
    from antigravity_cli_switcher.models import AccountMeta, HealthStatus

    meta = AccountMeta(**meta_dict)
    health = _derive_health_status(paths, name, meta)
    if health in {
        HealthStatus.AUTH_MISSING,
        HealthStatus.AUTH_EXPIRED,
        HealthStatus.DISABLED,
        HealthStatus.COOLDOWN,
        HealthStatus.INELIGIBLE,
    }:
        return False
    if _family_cooldown_active(meta_dict, family, now):
        return False
    family_thresholds = policy.get("family_thresholds")
    threshold = (
        float(family_thresholds[family])
        if isinstance(family_thresholds, dict) and family in family_thresholds
        else float(policy.get("short_usage_threshold_percent", 10.0))
    )
    return not _is_family_quota_exhausted(meta_dict, now, threshold_percent=threshold, family=family)

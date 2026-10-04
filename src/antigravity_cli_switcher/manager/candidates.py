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


_FAR_FUTURE_SECONDS: int = 86400 * 365


def _window_reset_seconds(
    meta: dict,
    window_name: str,
    *,
    family: str = "gemini",
    now: datetime,
) -> int:
    """Seconds until a specific usage window resets. Returns far-future if unknown."""
    windows = _usage_windows_for_family(meta, family)
    window = windows.get(window_name, {})
    if not isinstance(window, dict):
        return _FAR_FUTURE_SECONDS
    reset_at = parse_timestamp(window.get("reset_at"))
    if reset_at is None or reset_at <= now:
        return _FAR_FUTURE_SECONDS
    return max(0, int((reset_at - now).total_seconds()))


def _candidate_health_priority(health: HealthStatus) -> int:
    order = {
        HealthStatus.HEALTHY: 0,
        HealthStatus.READY: 1,
        HealthStatus.OAUTH_ROTATED: 1,
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

        if family is None:
            g_short = _candidate_usage_value(meta_dict, "short", family="gemini")
            c_short = _candidate_usage_value(meta_dict, "short", family="claude")
            g_weekly = _candidate_usage_value(meta_dict, "weekly", family="gemini")
            c_weekly = _candidate_usage_value(meta_dict, "weekly", family="claude")

            known_shorts = [v for v in (g_short, c_short) if v is not None]
            known_weeklies = [v for v in (g_weekly, c_weekly) if v is not None]

            short_value = sum(known_shorts) / len(known_shorts) if known_shorts else None
            weekly_value = sum(known_weeklies) / len(known_weeklies) if known_weeklies else None

            squeeze_bottleneck_short = max(known_shorts) if known_shorts else None
            squeeze_bottleneck_weekly = max(known_weeklies) if known_weeklies else None

            highest_short_value = min(known_shorts) if known_shorts else None

            short_known = short_value is not None
            weekly_known = weekly_value is not None

            g_exhausted = _is_family_quota_exhausted(
                meta_dict, current, threshold_percent=threshold_percent, family="gemini"
            )
            c_exhausted = _is_family_quota_exhausted(
                meta_dict, current, threshold_percent=threshold_percent, family="claude"
            )
            g_known = g_short is not None or g_weekly is not None
            c_known = c_short is not None or c_weekly is not None

            all_known_exhausted = ((g_known and g_exhausted) or not g_known) and (
                (c_known and c_exhausted) or not c_known
            )

            quota_low = all_known_exhausted

            if all_known_exhausted and (g_known or c_known):
                continue
        else:
            short_value = _candidate_usage_value(meta_dict, "short", family=family)
            weekly_value = _candidate_usage_value(meta_dict, "weekly", family=family)
            short_known = short_value is not None
            weekly_known = weekly_value is not None

            squeeze_bottleneck_short = short_value
            squeeze_bottleneck_weekly = weekly_value
            highest_short_value = short_value

            quota_low = _is_family_quota_exhausted(
                meta_dict,
                current,
                threshold_percent=threshold_percent,
                family=family,
            )

            if quota_low:
                continue

        score: tuple[object, ...]
        if strategy == "highest-short":
            score = (
                0 if short_known else 1,
                -(highest_short_value if highest_short_value is not None else -1.0),
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
        elif strategy == "squeeze":
            short_val = squeeze_bottleneck_short if squeeze_bottleneck_short is not None else 0.0
            weekly_val = squeeze_bottleneck_weekly if squeeze_bottleneck_weekly is not None else 0.0
            bottleneck = (
                min(short_val, weekly_val)
                if short_known and weekly_known
                else (short_val if short_known else weekly_val)
            )

            if family is None:
                g_short_r = _window_reset_seconds(meta_dict, "short", family="gemini", now=current)
                c_short_r = _window_reset_seconds(meta_dict, "short", family="claude", now=current)
                short_reset_secs = min(g_short_r, c_short_r)

                g_weekly_r = _window_reset_seconds(meta_dict, "weekly", family="gemini", now=current)
                c_weekly_r = _window_reset_seconds(meta_dict, "weekly", family="claude", now=current)
                weekly_reset_secs = min(g_weekly_r, c_weekly_r)
            else:
                short_reset_secs = _window_reset_seconds(meta_dict, "short", family=family, now=current)
                weekly_reset_secs = _window_reset_seconds(meta_dict, "weekly", family=family, now=current)
            nearest_reset = min(short_reset_secs, weekly_reset_secs)

            ww_deadline = float(str(policy.get("weekly_waste_deadline_hours", 48.0))) * 3600
            ww_threshold = float(str(policy.get("weekly_waste_threshold_percent", 70.0)))

            is_imminent = not quota_low and nearest_reset <= 7200
            is_weekly_waste = not quota_low and weekly_reset_secs <= ww_deadline and weekly_val >= ww_threshold

            if quota_low:
                urgency = 3
                primary = float(nearest_reset)
                secondary = -bottleneck
            elif is_imminent:
                urgency = 0
                primary = float(nearest_reset)
                secondary = bottleneck
            elif is_weekly_waste:
                urgency = 1
                primary = float(weekly_reset_secs)
                secondary = -weekly_val
            else:
                urgency = 2
                primary = bottleneck
                secondary = float(nearest_reset)

            score = (
                _candidate_health_priority(health),
                0 if not quota_low else 1,
                0 if short_known else 1,
                urgency,
                primary,
                secondary,
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

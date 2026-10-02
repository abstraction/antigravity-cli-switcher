from __future__ import annotations

from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths

DEFAULT_SWITCH_MODE = "auto"
VALID_SWITCH_MODES = ("auto", "manual")
DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD = 2
DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT = 10.0
DEFAULT_GEMINI_SWITCH_THRESHOLD_PERCENT = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT
DEFAULT_OTHER_SWITCH_THRESHOLD_PERCENT = DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT
DEFAULT_CANDIDATE_STRATEGY = "balanced"
VALID_CANDIDATE_STRATEGIES = ("balanced", "highest-short", "round-robin")
DEFAULT_FAMILY_FALLBACK_STRATEGY = "same-family-first"
VALID_FAMILY_FALLBACK_STRATEGIES = ("same-family-first", "same-account-first", "strict-family")
DEFAULT_SWITCH_HISTORY_LIMIT = 50
USAGE_FAMILY_NAMES = ("gemini", "other")


def _normalize_switch_mode(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in VALID_SWITCH_MODES:
            return normalized
    return DEFAULT_SWITCH_MODE


def get_switch_mode(state: dict | None) -> str:
    if not isinstance(state, dict):
        return DEFAULT_SWITCH_MODE
    return _normalize_switch_mode(state.get("switch_mode"))


def set_switch_mode(paths: ManagerPaths, mode: str) -> str:
    from antigravity_cli_switcher.manager.state import load_state, save_state, sync_state_from_disk

    normalized = _normalize_switch_mode(mode)
    if normalized != mode.strip().lower():
        raise ValueError(f"Unsupported switch mode: {mode}")
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        state["switch_mode"] = normalized
        save_state(paths, state)
        return normalized


def _default_switch_policy() -> dict[str, object]:
    return {
        "short_usage_threshold_percent": DEFAULT_SHORT_SWITCH_THRESHOLD_PERCENT,
        "family_thresholds": {
            "gemini": DEFAULT_GEMINI_SWITCH_THRESHOLD_PERCENT,
            "other": DEFAULT_OTHER_SWITCH_THRESHOLD_PERCENT,
        },
        "refresh_failure_threshold": DEFAULT_REFRESH_FAILURE_SWITCH_THRESHOLD,
        "candidate_strategy": DEFAULT_CANDIDATE_STRATEGY,
        "family_fallback_strategy": DEFAULT_FAMILY_FALLBACK_STRATEGY,
    }


def _normalize_candidate_strategy(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        if normalized in VALID_CANDIDATE_STRATEGIES:
            return normalized
    return DEFAULT_CANDIDATE_STRATEGY


def _normalize_family_fallback_strategy(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        if normalized in VALID_FAMILY_FALLBACK_STRATEGIES:
            return normalized
    return DEFAULT_FAMILY_FALLBACK_STRATEGY


def _normalize_switch_policy(raw: object) -> dict[str, object]:
    from antigravity_cli_switcher.manager.state import USAGE_FAMILY_NAMES

    defaults = _default_switch_policy()
    policy = dict(defaults)
    if isinstance(raw, dict):
        threshold = raw.get("short_usage_threshold_percent")
        try:
            if threshold is not None:
                threshold_value = float(threshold)
                if 0.0 <= threshold_value <= 100.0:
                    policy["short_usage_threshold_percent"] = threshold_value
        except (TypeError, ValueError):
            pass
        shared_threshold = policy["short_usage_threshold_percent"]
        family_thresholds = raw.get("family_thresholds")
        normalized_family_thresholds = {
            "gemini": shared_threshold,
            "other": shared_threshold,
        }
        if isinstance(family_thresholds, dict):
            for family in USAGE_FAMILY_NAMES:
                val = family_thresholds.get(family)
                if val is not None:
                    try:
                        family_value = float(val)
                        if 0.0 <= family_value <= 100.0:
                            normalized_family_thresholds[family] = family_value
                    except (TypeError, ValueError):
                        pass
        policy["family_thresholds"] = normalized_family_thresholds
        failure_threshold = raw.get("refresh_failure_threshold")
        try:
            if failure_threshold is not None:
                failure_value = int(failure_threshold)
                if failure_value >= 1:
                    policy["refresh_failure_threshold"] = failure_value
        except (TypeError, ValueError):
            pass
        policy["candidate_strategy"] = _normalize_candidate_strategy(raw.get("candidate_strategy"))
        policy["family_fallback_strategy"] = _normalize_family_fallback_strategy(raw.get("family_fallback_strategy"))
    return policy


def _state_switch_policy(state: dict | None) -> dict[str, object]:
    if not isinstance(state, dict):
        return _default_switch_policy()
    return _normalize_switch_policy(state.get("switch_policy"))


def get_switch_policy(paths: ManagerPaths) -> dict[str, object]:
    from antigravity_cli_switcher.manager.state import load_state, sync_state_from_disk

    state = sync_state_from_disk(paths, load_state(paths))
    return dict(_state_switch_policy(state))


def update_switch_policy(
    paths: ManagerPaths,
    *,
    short_usage_threshold_percent: float | None = None,
    gemini_usage_threshold_percent: float | None = None,
    other_usage_threshold_percent: float | None = None,
    refresh_failure_threshold: int | None = None,
    candidate_strategy: str | None = None,
    family_fallback_strategy: str | None = None,
) -> dict[str, object]:
    from antigravity_cli_switcher.manager.state import USAGE_FAMILY_NAMES, load_state, save_state, sync_state_from_disk

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        policy = _state_switch_policy(state)
        if short_usage_threshold_percent is not None:
            value = float(short_usage_threshold_percent)
            if value < 0.0 or value > 100.0:
                raise ValueError("short_usage_threshold_percent must be between 0 and 100.")
            policy["short_usage_threshold_percent"] = value
            policy["family_thresholds"] = {family: value for family in USAGE_FAMILY_NAMES}
        for family, requested_value in (
            ("gemini", gemini_usage_threshold_percent),
            ("other", other_usage_threshold_percent),
        ):
            if requested_value is None:
                continue
            value = float(requested_value)
            if value < 0.0 or value > 100.0:
                raise ValueError(f"{family}_usage_threshold_percent must be between 0 and 100.")
            raw_thresholds = policy.get("family_thresholds")
            family_thresholds = dict(raw_thresholds) if isinstance(raw_thresholds, dict) else {}
            family_thresholds[family] = value
            policy["family_thresholds"] = family_thresholds
        if refresh_failure_threshold is not None:
            value = int(refresh_failure_threshold)
            if value < 1:
                raise ValueError("refresh_failure_threshold must be at least 1.")
            policy["refresh_failure_threshold"] = value
        if candidate_strategy is not None:
            normalized_strategy = _normalize_candidate_strategy(candidate_strategy)
            if normalized_strategy != candidate_strategy.strip().lower().replace("_", "-"):
                raise ValueError(f"Unsupported candidate strategy: {candidate_strategy}")
            policy["candidate_strategy"] = normalized_strategy
        if family_fallback_strategy is not None:
            normalized_fallback = _normalize_family_fallback_strategy(family_fallback_strategy)
            if normalized_fallback != family_fallback_strategy.strip().lower().replace("_", "-"):
                raise ValueError(f"Unsupported family fallback strategy: {family_fallback_strategy}")
            policy["family_fallback_strategy"] = normalized_fallback
        state["switch_policy"] = policy
        save_state(paths, state)
        return dict(policy)

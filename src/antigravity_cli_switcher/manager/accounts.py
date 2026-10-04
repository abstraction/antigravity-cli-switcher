from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from antigravity_cli_switcher.log import get_logger
from antigravity_cli_switcher.manager.identity import (
    check_token_account_match,
    profile_has_login_artifacts,
)
from antigravity_cli_switcher.manager.keyring import (
    _sync_home_to_keyring,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.login import (
    _ensure_safe_account_switch,
    _get_running_agy_processes,
    login_account,
)
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _copy_account_profile,
    _resolve_profile_source,
    account_dir,
    default_root,
)
from antigravity_cli_switcher.manager.policy import (
    _state_switch_policy,
    get_quota_backend,
    get_switch_mode,
)
from antigravity_cli_switcher.manager.profiles import (
    add_account,
    delete_account,
    import_current,
    rename_account,
    resolve_account_name,
    save_account_profile,
)
from antigravity_cli_switcher.manager.proxies import (
    clear_account_proxy,
    get_account_proxy,
    list_account_proxies,
    set_account_proxy,
)
from antigravity_cli_switcher.manager.state import (
    DEFAULT_REFRESH_POLICY_SECONDS,
    _default_proxy_config,
    _normalize_proxy_config,
    _normalize_switch_history,
    _normalize_switch_runtime,
    _normalize_timestamp,
    _normalize_usage_families,
    _normalize_usage_windows,
    _sync_legacy_usage_fields,
    get_live_dir,
    load_state,
    save_state,
    sync_state_from_disk,
    utc_now,
)
from antigravity_cli_switcher.manager.verification import (
    _derive_health_status,
    verify_account,
    verify_accounts,
)
from antigravity_cli_switcher.watch import get_log_watch_snapshot


def _copy_active_runtime(paths: ManagerPaths, name: str) -> None:
    src = account_dir(paths, name)
    if not src.exists():
        raise ValueError(f"Account not found: {name}")
    if not profile_has_login_artifacts(_resolve_profile_source(src)):
        raise ValueError(f"Account {name} is missing required auth files")

    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    _copy_account_profile(src, paths.runtime_dir)


def _sync_runtime_to_live_dir(paths: ManagerPaths, state: dict) -> None:
    live_dir = get_live_dir(state)
    if live_dir is None:
        return
    # Guardrail: Never let an isolated/test root write to the real user's live directory
    if paths.root.resolve() != default_root().resolve() and live_dir.resolve() == (Path.home() / ".gemini").resolve():
        return
    _copy_account_profile(paths.runtime_dir, live_dir.parent)
    _sync_home_to_keyring(paths.runtime_dir)


def switch_account(paths: ManagerPaths, name: str) -> str:
    logger = get_logger(paths.root)
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(state, name)
        meta = state["accounts"].get(resolved_name)
        if meta is None:
            logger.error(f"switch_account failed: Account not found: {name}")
            raise ValueError(f"Account not found: {name}")
        match_result = check_token_account_match(paths, resolved_name, account_dir(paths, resolved_name), state=state)
        if match_result.status == "mismatch":
            raise ValueError(
                f"Token mismatch for '{resolved_name}': expected {match_result.expected_email}, "
                f"got {match_result.token_email}. Run 'acs login {resolved_name}' to fix."
            )
        previous = state.get("active")
        _copy_active_runtime(paths, resolved_name)
        state["active"] = resolved_name
        state = sync_state_from_disk(paths, state)
        _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)
        logger.info(f"Switched active account to: {resolved_name}")
        return previous or ""


def switch_next(paths: ManagerPaths) -> str:
    from antigravity_cli_switcher.manager.candidates import _eligible_switch_candidates
    from antigravity_cli_switcher.manager.routing import _best_switch_candidate

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        candidates = _eligible_switch_candidates(state)
        if not candidates:
            raise ValueError("No enabled non-cooldown accounts available.")

        current = state.get("active")
        target = _best_switch_candidate(paths, state, exclude=current)
        if target is None and current in candidates and len(candidates) == 1:
            target = current
        if target is None:
            raise ValueError("No eligible standby account is available.")
        if len(candidates) == 1 and current == target:
            raise ValueError("Only one eligible account is available.")
        _copy_active_runtime(paths, target)
        state["active"] = target
        state = sync_state_from_disk(paths, state)
        _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)
        logger = get_logger(paths.root)
        logger.info(f"Switched active account to: {target}")
        return target


def apply_active(paths: ManagerPaths) -> str:
    logger = get_logger(paths.root)
    _ensure_safe_account_switch()
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        active = state.get("active")
        if not active:
            logger.error("apply_active failed: No active account is set")
            raise ValueError("No active account is set.")
        match_result = check_token_account_match(paths, active, account_dir(paths, active), state=state)
        if match_result.status == "mismatch":
            raise ValueError(
                f"Token mismatch for '{active}': expected {match_result.expected_email}, "
                f"got {match_result.token_email}. Run 'acs login {active}' to fix."
            )
        logger.info(f"Applying active account: {active}")
        _copy_active_runtime(paths, active)
        _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)
        logger.info(f"Successfully applied active account: {active}")
        return active


def get_status_snapshot(paths: ManagerPaths) -> dict:
    state = sync_state_from_disk(paths, load_state(paths))
    snapshot_accounts = {}
    for name, meta in sorted(state["accounts"].items()):
        from antigravity_cli_switcher.models import AccountMeta

        derived_health_status = _derive_health_status(paths, name, AccountMeta(**meta))
        snapshot_accounts[name] = {
            "enabled": bool(meta.get("enabled", True)),
            "status": meta.get("status", "standby"),
            "plan_type": meta.get("plan_type"),
            "expected_email": meta.get("expected_email"),
            "last_error": meta.get("last_error"),
            "cooldown_until": meta.get("cooldown_until"),
            "fail_count": int(meta.get("fail_count", 0) or 0),
            "refresh_fail_count": int(meta.get("refresh_fail_count", 0) or 0),
            "created_at": meta.get("created_at"),
            "usage_families": _normalize_usage_families(meta),
            "usage_windows": _normalize_usage_windows(meta),
            "usage_status": meta.get("usage_status", "unknown"),
            "usage_value": meta.get("usage_value"),
            "reset_at": meta.get("reset_at"),
            "health_status": derived_health_status,
            "stored_health_status": meta.get("health_status", "unknown"),
            "last_live_check_at": meta.get("last_live_check_at"),
            "last_live_check_error": meta.get("last_live_check_error"),
            "next_live_check_at": meta.get("next_live_check_at"),
            "refresh_policy_seconds": int(
                meta.get("refresh_policy_seconds", DEFAULT_REFRESH_POLICY_SECONDS) or DEFAULT_REFRESH_POLICY_SECONDS
            ),
            "identity": meta.get("identity") if isinstance(meta.get("identity"), dict) else None,
            "proxy": _normalize_proxy_config(meta.get("proxy")),
            "family_cooldowns": dict(meta.get("family_cooldowns", {}))
            if isinstance(meta.get("family_cooldowns"), dict)
            else {},
        }
    active_name = state.get("active")
    active_meta = state["accounts"].get(active_name) if active_name else None
    return {
        "root": str(paths.root),
        "runtime_dir": str(paths.runtime_dir),
        "lock_file": str(paths.lock_file),
        "live_dir": state.get("live_dir"),
        "active": active_name,
        "quota_backend": get_quota_backend(state),
        "active_proxy": _normalize_proxy_config(active_meta.get("proxy"))
        if isinstance(active_meta, dict)
        else _default_proxy_config(),
        "switch_mode": get_switch_mode(state),
        "switch_policy": _state_switch_policy(state),
        "switch_runtime": _normalize_switch_runtime(state.get("switch_runtime")),
        "switch_history": _normalize_switch_history(state.get("switch_history")),
        "log_watch": get_log_watch_snapshot(paths),
        "accounts": snapshot_accounts,
    }


def set_enabled(paths: ManagerPaths, name: str, enabled: bool) -> None:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(state, name)
        meta = state["accounts"].get(resolved_name)
        if meta is None:
            raise ValueError(f"Unknown account: {name}")
        meta["enabled"] = bool(enabled)
        state = sync_state_from_disk(paths, state)
        save_state(paths, state)


def mark_bad(paths: ManagerPaths, name: str, reason: str, cooldown_minutes: int) -> None:
    if cooldown_minutes < 0:
        raise ValueError("Cooldown minutes must be non-negative.")
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(state, name)
        meta = state["accounts"].get(resolved_name)
        if meta is None:
            raise ValueError(f"Unknown account: {name}")
        meta["last_error"] = reason
        meta["fail_count"] = int(meta.get("fail_count", 0)) + 1
        if cooldown_minutes > 0:
            until = utc_now() + timedelta(minutes=cooldown_minutes)
            meta["cooldown_until"] = until.isoformat()
        else:
            meta["cooldown_until"] = None
        state = sync_state_from_disk(paths, state)
        save_state(paths, state)


def clear_bad(paths: ManagerPaths, name: str) -> None:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(state, name)
        meta = state["accounts"].get(resolved_name)
        if meta is None:
            raise ValueError(f"Unknown account: {name}")
        meta["last_error"] = None
        meta["last_live_check_error"] = None
        meta["cooldown_until"] = None
        meta["fail_count"] = 0
        meta["refresh_fail_count"] = 0
        meta["family_cooldowns"] = {}
        state = sync_state_from_disk(paths, state)
        save_state(paths, state)


def update_account_runtime_metadata(
    paths: ManagerPaths,
    name: str,
    *,
    usage_status: str | None = None,
    usage_value: float | None = None,
    reset_at: str | None = None,
    short_usage_status: str | None = None,
    short_usage_value: float | None = None,
    short_reset_at: str | None = None,
    weekly_usage_status: str | None = None,
    weekly_usage_value: float | None = None,
    weekly_reset_at: str | None = None,
    health_status: str | None = None,
    last_live_check_at: str | None = None,
    last_live_check_error: str | None = None,
    next_live_check_at: str | None = None,
    refresh_policy_seconds: int | None = None,
) -> dict:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        meta = state["accounts"].get(name)
        if meta is None:
            raise ValueError(f"Account not found: {name}")
        windows = _normalize_usage_windows(meta)
        if short_usage_status is not None:
            windows["short"]["status"] = short_usage_status
        elif usage_status is not None:
            windows["short"]["status"] = usage_status
        if short_usage_value is not None:
            windows["short"]["value"] = short_usage_value
        elif usage_value is not None:
            windows["short"]["value"] = usage_value
        if short_reset_at is not None:
            windows["short"]["reset_at"] = _normalize_timestamp(short_reset_at)
        elif reset_at is not None:
            windows["short"]["reset_at"] = _normalize_timestamp(reset_at)
        if weekly_usage_status is not None:
            windows["weekly"]["status"] = weekly_usage_status
        if weekly_usage_value is not None:
            windows["weekly"]["value"] = weekly_usage_value
        if weekly_reset_at is not None:
            windows["weekly"]["reset_at"] = _normalize_timestamp(weekly_reset_at)
        meta["usage_windows"] = windows
        families = _normalize_usage_families(meta)
        families["gemini"] = windows
        meta["usage_families"] = families
        _sync_legacy_usage_fields(meta)
        if health_status is not None:
            meta["health_status"] = health_status
        if last_live_check_at is not None:
            meta["last_live_check_at"] = _normalize_timestamp(last_live_check_at)
        if last_live_check_error is not None:
            meta["last_live_check_error"] = last_live_check_error
        if next_live_check_at is not None:
            meta["next_live_check_at"] = _normalize_timestamp(next_live_check_at)
        if refresh_policy_seconds is not None:
            meta["refresh_policy_seconds"] = max(30, int(refresh_policy_seconds))
        save_state(paths, state)
        return dict(meta)


def format_status(paths: ManagerPaths) -> str:
    snapshot = get_status_snapshot(paths)
    lines = [
        f"root: {snapshot['root']}",
        f"runtime: {snapshot['runtime_dir']}",
        f"lock: {snapshot['lock_file']}",
        f"live_dir: {snapshot['live_dir'] or '-'}",
        f"active: {snapshot['active'] or '-'}",
        f"switch_mode: {snapshot['switch_mode']}",
        "accounts:",
    ]
    if not snapshot["accounts"]:
        lines.append("  (none)")
        return "\n".join(lines)

    for name, meta in snapshot["accounts"].items():
        cooldown_str = f" cooldown_until={meta['cooldown_until']}" if meta["cooldown_until"] else ""
        error_str = f" last_error={meta['last_error']}" if meta["last_error"] else ""
        live_str = f" live={meta['last_live_check_at']}" if meta["last_live_check_at"] else ""
        lines.append(
            f"  - {name}: status={meta['status']} health={meta['health_status']} enabled={meta['enabled']} "
            f"fail_count={meta['fail_count']} refresh_fails={meta['refresh_fail_count']}"
            f"{cooldown_str}{error_str}{live_str}"
        )
    return "\n".join(lines)


__all__ = [
    "_copy_active_runtime",
    "_derive_health_status",
    "_ensure_safe_account_switch",
    "_get_running_agy_processes",
    "_sync_runtime_to_live_dir",
    "add_account",
    "apply_active",
    "clear_account_proxy",
    "clear_bad",
    "delete_account",
    "format_status",
    "get_account_proxy",
    "get_status_snapshot",
    "import_current",
    "list_account_proxies",
    "login_account",
    "mark_bad",
    "rename_account",
    "save_account_profile",
    "set_account_proxy",
    "set_enabled",
    "switch_account",
    "switch_next",
    "update_account_runtime_metadata",
    "verify_account",
    "verify_accounts",
]

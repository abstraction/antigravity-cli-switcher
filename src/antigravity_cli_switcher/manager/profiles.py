from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from antigravity_cli_switcher.manager.identity import (
    _best_effort_saved_profile_identity,
    profile_has_login_artifacts,
)
from antigravity_cli_switcher.manager.keyring import (
    _sync_keyring_to_home,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _clear_directory,
    _copy_account_profile,
    _resolve_home_source,
    _resolve_profile_source,
    account_dir,
)
from antigravity_cli_switcher.manager.state import (
    DEFAULT_REFRESH_POLICY_SECONDS,
    _normalize_proxy_config,
    _normalize_usage_families,
    _normalize_usage_windows,
    _sync_legacy_usage_fields,
    get_live_dir,
    load_state,
    save_state,
    sync_state_from_disk,
    utc_now,
)


def resolve_account_name(state: dict, query: str) -> str:
    """Resolve an account name, partial name, or index to the exact account key."""
    cleaned = query.strip()
    accounts = state.get("accounts", {})
    if not accounts or not cleaned:
        return cleaned

    # 1. Exact match
    if cleaned in accounts:
        return cleaned

    # 2. Case-insensitive exact match
    for acc in accounts:
        if acc.lower() == cleaned.lower():
            return acc

    # 3. Numeric index match
    if cleaned.isdigit():
        target_idx = int(cleaned)
        # Check for bracket prefix e.g. "[1] foo" or "[01] foo"
        for acc in accounts:
            match = re.match(r"^\[(\d+)\]", acc)
            if match and int(match.group(1)) == target_idx:
                return acc
        # Check 1-based index in sorted keys
        sorted_keys = sorted(accounts.keys())
        if 1 <= target_idx <= len(sorted_keys):
            return sorted_keys[target_idx - 1]

    # 4. Strip bracketed number prefix e.g. "[1] foo" -> "foo"
    clean_re = re.compile(r"^\[\d+\]\s*")
    for acc in accounts:
        stripped = clean_re.sub("", acc).strip()
        if stripped.lower() == cleaned.lower():
            return acc

    return cleaned


def save_account_profile(paths: ManagerPaths, name: str, source_dir: Path, overwrite: bool = False) -> None:
    from antigravity_cli_switcher.manager.accounts import (
        _copy_active_runtime,
        _sync_runtime_to_live_dir,
    )

    if not name.strip():
        raise ValueError("Account name cannot be empty.")
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Source directory does not exist: {source_dir}")

    home_source = _resolve_home_source(source_dir)
    profile_source = _resolve_profile_source(source_dir)
    if not profile_source.exists() or not profile_source.is_dir():
        raise ValueError(f"Usable profile source not found in {source_dir}")

    _sync_keyring_to_home(home_source)
    if not profile_has_login_artifacts(profile_source):
        raise ValueError(f"Profile source is missing required auth files: {profile_source}")

    target = account_dir(paths, name)
    target_exists = target.exists()
    if target_exists and not overwrite:
        raise ValueError(f"Account already exists: {name}")
    if target_exists:
        _clear_directory(target)
    else:
        target.mkdir(parents=True, exist_ok=False)
    _copy_account_profile(home_source, target)
    identity = _best_effort_saved_profile_identity(target)

    with manager_lock(paths):
        state = load_state(paths)
        state = sync_state_from_disk(paths, state)
        previous_meta = state["accounts"].get(name, {})
        state["accounts"][name] = {
            "enabled": previous_meta.get("enabled", True),
            "status": previous_meta.get("status", "standby"),
            "last_error": None if overwrite else previous_meta.get("last_error"),
            "cooldown_until": None if overwrite else previous_meta.get("cooldown_until"),
            "fail_count": 0 if overwrite else previous_meta.get("fail_count", 0),
            "refresh_fail_count": 0 if overwrite else previous_meta.get("refresh_fail_count", 0),
            "created_at": previous_meta.get("created_at") or utc_now().isoformat(),
            "usage_families": _normalize_usage_families(previous_meta),
            "usage_windows": _normalize_usage_windows(previous_meta),
            "usage_status": previous_meta.get("usage_status", "unknown"),
            "usage_value": previous_meta.get("usage_value"),
            "reset_at": previous_meta.get("reset_at"),
            "health_status": "ready" if overwrite else previous_meta.get("health_status", "unknown"),
            "last_live_check_at": None if overwrite else previous_meta.get("last_live_check_at"),
            "last_live_check_error": None if overwrite else previous_meta.get("last_live_check_error"),
            "next_live_check_at": None if overwrite else previous_meta.get("next_live_check_at"),
            "refresh_policy_seconds": int(
                previous_meta.get("refresh_policy_seconds", DEFAULT_REFRESH_POLICY_SECONDS)
                or DEFAULT_REFRESH_POLICY_SECONDS
            ),
            "identity": identity,
            "expected_email": (identity.get("email") if isinstance(identity, dict) else None)
            or (identity.get("account_name") if isinstance(identity, dict) else None)
            or previous_meta.get("expected_email"),
            "plan_type": previous_meta.get("plan_type"),
            "proxy": _normalize_proxy_config(previous_meta.get("proxy")),
            "family_cooldowns": {}
            if overwrite
            else (
                dict(previous_meta.get("family_cooldowns", {}))
                if isinstance(previous_meta.get("family_cooldowns"), dict)
                else {}
            ),
        }
        _sync_legacy_usage_fields(state["accounts"][name])
        if overwrite and state.get("active") == name:
            _copy_active_runtime(paths, name)
            state = sync_state_from_disk(paths, state)
            _sync_runtime_to_live_dir(paths, state)
        save_state(paths, state)
        if not state.get("active"):
            _copy_active_runtime(paths, name)
            state["active"] = name
            state = sync_state_from_disk(paths, state)
            _sync_runtime_to_live_dir(paths, state)
            save_state(paths, state)


def add_account(paths: ManagerPaths, name: str, source_dir: Path) -> None:
    save_account_profile(paths, name, source_dir, overwrite=False)


def rename_account(paths: ManagerPaths, old_name: str, new_name: str) -> None:
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        raise ValueError("Account names cannot be empty.")

    with manager_lock(paths):
        state = load_state(paths)
        if old_name not in state["accounts"]:
            raise ValueError(f"Account '{old_name}' not found.")
        if new_name in state["accounts"]:
            raise ValueError(f"Account '{new_name}' already exists.")

        old_dir = paths.accounts_dir / old_name
        new_dir = paths.accounts_dir / new_name

        if not old_dir.exists():
            raise ValueError(f"Account directory for '{old_name}' is missing on disk.")

        old_dir.rename(new_dir)

        state["accounts"][new_name] = state["accounts"].pop(old_name)
        if state.get("active") == old_name:
            state["active"] = new_name

        fleet_util = state.get("fleet_utilization")
        if isinstance(fleet_util, dict):
            fleet_accounts = fleet_util.get("accounts")
            if isinstance(fleet_accounts, dict) and old_name in fleet_accounts:
                fleet_accounts[new_name] = fleet_accounts.pop(old_name)
            if fleet_util.get("last_active_account") == old_name:
                fleet_util["last_active_account"] = new_name

        save_state(paths, state)


def delete_account(paths: ManagerPaths, name: str) -> bool:
    if not name.strip():
        raise ValueError("Account name cannot be empty.")
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        if name not in state["accounts"]:
            raise ValueError(f"Account not found: {name}")

        was_active = state.get("active") == name

        del state["accounts"][name]
        if was_active:
            state["active"] = None

        fleet_util = state.get("fleet_utilization")
        if isinstance(fleet_util, dict):
            fleet_accounts = fleet_util.get("accounts")
            if isinstance(fleet_accounts, dict):
                fleet_accounts.pop(name, None)
            if fleet_util.get("last_active_account") == name:
                fleet_util["last_active_account"] = ""

        save_state(paths, state)

        target = account_dir(paths, name)
        if target.exists():
            _retries = 5
            for _attempt in range(_retries):
                try:
                    shutil.rmtree(target)
                    break
                except OSError as exc:
                    import errno as _errno

                    if exc.errno != _errno.ENOTEMPTY or _attempt == _retries - 1:
                        raise
                    time.sleep(0.2)

    return was_active


def import_current(paths: ManagerPaths, name: str, source_dir: Path | None = None) -> None:
    from antigravity_cli_switcher.manager.keyring import _sync_keyring_to_home

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        live_dir = source_dir or get_live_dir(state)
        if live_dir is None:
            raise ValueError("No source_dir provided and no live_dir configured.")
        _sync_keyring_to_home(_resolve_home_source(live_dir))
    add_account(paths, name, live_dir)

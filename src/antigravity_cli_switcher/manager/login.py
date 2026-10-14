from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time

from antigravity_cli_switcher.manager.identity import (
    next_available_account_name,
    normalize_account_storage_name,
    profile_has_login_artifacts,
    resolve_login_profile_identity,
)
from antigravity_cli_switcher.manager.keyring import (
    _clear_keyring_token,
    _is_test_environment,
    _load_keyring_token,
    _save_keyring_token,
    _sync_keyring_to_home,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _remove_managed_profile_files,
    _resolve_profile_source,
    account_dir,
    default_live_dir,
    resolve_agy_binary,
    resolve_runtime_home,
)
from antigravity_cli_switcher.manager.state import (
    get_live_dir,
    load_state,
    save_state,
    sync_state_from_disk,
)
from antigravity_cli_switcher.manager.stealth import ensure_account_identity_files


def _get_running_agy_processes() -> list[tuple[int, str]]:
    from antigravity_cli_switcher.manager.stealth import get_external_agy_processes

    pids = get_external_agy_processes()
    return [(pid, str(pid)) for pid in pids]


def _ensure_safe_account_switch() -> list[int]:
    if _is_test_environment() and os.getenv("ACS_TEST_REAL_PROCS") != "1":
        return []
    from antigravity_cli_switcher.manager.stealth import terminate_external_agy_processes

    return terminate_external_agy_processes()


def login_account(
    paths: ManagerPaths,
    name: str,
    agy_binary: str | None,
    timeout_seconds: int = 600,
    overwrite_existing: bool = False,
) -> str | None:
    from antigravity_cli_switcher.manager.accounts import _copy_active_runtime, _sync_runtime_to_live_dir
    from antigravity_cli_switcher.manager.profiles import resolve_account_name, save_account_profile

    if not name.strip():
        raise ValueError("Account name cannot be empty.")
    try:
        stdin_fd = sys.stdin.fileno()
    except (io.UnsupportedOperation, AttributeError, ValueError, OSError):
        stdin_fd = 0
    if not os.isatty(stdin_fd):
        raise ValueError("Interactive login requires a TTY.")
    _ensure_safe_account_switch()

    resolved_binary = resolve_agy_binary(agy_binary)
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        live_dir = get_live_dir(state) or default_live_dir(paths.root)
        state["live_dir"] = str(live_dir.resolve())
        save_state(paths, state)
        resolved_name = resolve_account_name(state, name)
        storage_name = normalize_account_storage_name(resolved_name)

    stashed_active_token: dict | None = None
    try:
        runtime_home = resolve_runtime_home(live_dir)
        runtime_home.mkdir(parents=True, exist_ok=True)

        stashed_active_token = _load_keyring_token()
        _clear_keyring_token()
        _remove_managed_profile_files(live_dir)

        # If relogging into an existing account, pre-seed its established identity
        # files into live_dir so agy reuses them instead of generating a new Install ID.
        target = account_dir(paths, storage_name)
        if target.is_dir():
            target_profile = _resolve_profile_source(target)
            ensure_account_identity_files(target_profile)
            for ident_file in (
                "antigravity-cli/installation_id",
                "installation_id",
                "antigravity-cli/jetski_state.pbtxt",
            ):
                src = target_profile / ident_file
                if src.is_file():
                    dst = live_dir / ident_file
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        print("\n" + "=" * 54)
        print(" Antigravity CLI Switcher (acs) - Login Session")
        print(f" Target Account: {name}")
        print("=" * 54)
        print(" 1. Complete onboarding/login in your browser.")
        print(" 2. Once authenticated, exit agy (/exit or Ctrl+D)")
        print("    to return to the switcher.")
        print("=" * 54 + "\n", flush=True)

        env = os.environ.copy()
        env["HOME"] = str(runtime_home)

        try:
            proc = subprocess.Popen(
                [resolved_binary],
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=runtime_home,
                env=env,
                close_fds=True,
            )
        except FileNotFoundError as exc:
            raise ValueError(f"agy binary not found: {resolved_binary}") from exc

        start_time = time.time()
        try:
            while True:
                if proc.poll() is not None:
                    break
                if time.time() - start_time > timeout_seconds:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise ValueError(f"Login timed out after {timeout_seconds} seconds.")
                time.sleep(0.1)
        except KeyboardInterrupt:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            raise

        _sync_keyring_to_home(runtime_home)

        if not live_dir.is_dir() or not profile_has_login_artifacts(live_dir):
            raise ValueError("agy login did not produce a usable auth profile.")

        identity = resolve_login_profile_identity(live_dir, agy_binary=resolved_binary, live_dir=live_dir)
        detected_name = identity.get("account_name")
        detected_email = identity.get("email") or detected_name
        display_name = identity.get("display_name")
        with manager_lock(paths):
            current_state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(current_state, name)
        storage_name = normalize_account_storage_name(resolved_name)

        colliding_account = None
        for other_name, other_meta in current_state.get("accounts", {}).items():
            if other_name == storage_name:
                continue
            other_email = other_meta.get("expected_email")
            if not other_email:
                other_ident = other_meta.get("identity")
                if isinstance(other_ident, dict):
                    other_email = other_ident.get("email") or other_ident.get("account_name")
            if not other_email and "@" in other_name:
                other_email = other_name.strip()
            if other_email and detected_email and str(other_email).lower() == str(detected_email).lower():
                colliding_account = other_name
                break

        print("\n" + "=" * 48)
        print("ACS Account Verification")
        print(f"Target Account : {resolved_name}")
        if detected_email:
            name_info = f" ({display_name})" if display_name else ""
            print(f"Detected Email : {detected_email}{name_info}")
        else:
            print("Detected Email : (Could not determine email from token)")
        print("=" * 48)

        if colliding_account:
            print(f"\n[WARNING] Account '{colliding_account}' is already using email '{detected_email}'!")
            print("This token appears to belong to an existing account, not a new one.")

        name_mismatch = bool(
            "@" in resolved_name and detected_email and resolved_name.strip().lower() != str(detected_email).lower()
        )
        if name_mismatch:
            print(f"\n[WARNING] Target account name is '{resolved_name}', but token is for '{detected_email}'!")
        has_warning = bool(colliding_account) or name_mismatch

        if overwrite_existing and not has_warning:
            print(f"\n[acs] Verified token for '{resolved_name}' ({detected_email or 'ok'}). Saving profile...")
        else:
            confirm_ans = input(f"\nConfirm saving token for account '{resolved_name}'? [Y/n]: ").strip().lower()
            if confirm_ans in {"n", "no"}:
                print("Login cancelled. Token discarded and active account restored.")
                return None

        overwrite = overwrite_existing
        if not overwrite and account_dir(paths, storage_name).exists():
            if input(f"Account '{storage_name}' already exists. Overwrite it? [Y/n]: ").strip().lower() in {"n", "no"}:
                storage_name = next_available_account_name(paths, storage_name)
                print(f"saving-as: {storage_name}")
            else:
                overwrite = True

        save_account_profile(paths, storage_name, runtime_home, overwrite=overwrite)
        if detected_email:
            with manager_lock(paths):
                state = sync_state_from_disk(paths, load_state(paths))
                if storage_name in state.get("accounts", {}):
                    state["accounts"][storage_name]["expected_email"] = detected_email
                    save_state(paths, state)
        return storage_name

    finally:
        with manager_lock(paths):
            state = sync_state_from_disk(paths, load_state(paths))
            active = state.get("active")
            if active:
                try:
                    _copy_active_runtime(paths, active)
                    _sync_runtime_to_live_dir(paths, state)
                except Exception:
                    pass
            elif stashed_active_token:
                _save_keyring_token(stashed_active_token)

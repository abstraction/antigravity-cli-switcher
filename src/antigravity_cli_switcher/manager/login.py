from __future__ import annotations

import io
import os
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
    _is_test_environment,
    _isolated_keyring_warmup,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _remove_managed_profile_files,
    account_dir,
    default_live_dir,
    resolve_agy_binary,
)
from antigravity_cli_switcher.manager.state import (
    get_live_dir,
    load_state,
    save_state,
    sync_state_from_disk,
)


def _get_running_agy_processes() -> list[tuple[int, str]]:
    current_uid = os.getuid()
    current_pid = os.getpid()

    agy_procs = []
    try:
        if sys.platform.startswith("linux"):
            ps_cmd = ["ps", "-u", str(current_uid), "-o", "pid,args"]
        elif sys.platform == "darwin":
            ps_cmd = ["ps", "-u", str(current_uid), "-o", "pid,command"]
        else:
            return []

        result = subprocess.run(ps_cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")[1:]

        for line in lines:
            parts = line.strip().split(maxsplit=1)
            if len(parts) < 2:
                continue

            pid_str, cmd_str = parts
            if not pid_str.isdigit():
                continue

            pid = int(pid_str)
            if pid == current_pid:
                continue

            cmdline = cmd_str.split()
            if not cmdline:
                continue

            is_agy = False
            if cmdline[0] == "agy" or cmdline[0].endswith("/agy"):
                is_agy = True
            elif len(cmdline) >= 3 and cmdline[1] == "-m" and cmdline[2] == "agy":
                is_agy = True
            elif len(cmdline) >= 2 and ("python" in cmdline[0] or "python3" in cmdline[0]):
                if cmdline[1].endswith("/agy") or cmdline[1] == "agy":
                    is_agy = True

            if is_agy:
                if any(x in cmd_str for x in ("antigravity-cli-switcher", "acs", "agy-cli-manager", "acm")):
                    is_agy = False

            if is_agy:
                agy_procs.append((pid, cmd_str))
    except Exception:
        pass

    return agy_procs


def _ensure_safe_account_switch() -> None:
    if _is_test_environment():
        return
    procs = _get_running_agy_processes()
    if procs:
        pids = [str(p[0]) for p in procs]
        raise RuntimeError(
            f"Cannot safely switch accounts. Active 'agy' processes detected (PIDs: {', '.join(pids)}). "
            f"Please terminate them to avoid global OS keyring corruption."
        )


def login_account(
    paths: ManagerPaths,
    name: str,
    agy_binary: str | None,
    timeout_seconds: int = 600,
    overwrite_existing: bool = False,
) -> str | None:
    from antigravity_cli_switcher.manager.accounts import (
        _copy_active_runtime,
        _sync_runtime_to_live_dir,
    )
    from antigravity_cli_switcher.manager.profiles import (
        resolve_account_name,
        save_account_profile,
    )

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

    try:
        runtime_home = live_dir.parent
        runtime_home.mkdir(parents=True, exist_ok=True)
        _remove_managed_profile_files(live_dir)

        env = os.environ.copy()

        with _isolated_keyring_warmup(runtime_home):
            print("\n" + "=" * 54)
            print(" Antigravity CLI Switcher (acs) - Login Session")
            print(f" Target Account: {name}")
            print("=" * 54)
            print(" 1. Complete onboarding/login in your browser.")
            print(" 2. Once authenticated, exit agy (/exit or Ctrl+D)")
            print("    to return to the switcher.")
            print("=" * 54 + "\n", flush=True)

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

        if "@" in resolved_name and detected_email and resolved_name.strip().lower() != str(detected_email).lower():
            print(
                f"\n[WARNING] Target account name is '{resolved_name}', but the logged-in token is for '{detected_email}'!"
            )

        has_warning = bool(colliding_account) or bool(
            "@" in resolved_name and detected_email and resolved_name.strip().lower() != str(detected_email).lower()
        )

        if overwrite_existing and not has_warning:
            print(f"\n[acs] Verified token for '{resolved_name}' ({detected_email or 'ok'}). Saving profile...")
        else:
            confirm_prompt = f"\nConfirm saving token for account '{resolved_name}'? [Y/n]: "
            confirm_ans = input(confirm_prompt).strip().lower()
            if confirm_ans in {"n", "no"}:
                print("Login cancelled. Token discarded and active account restored.")
                return None

        overwrite = False
        if overwrite_existing:
            overwrite = True
        elif account_dir(paths, storage_name).exists():
            prompt = f"Account '{storage_name}' already exists. Overwrite it? [Y/n]: "
            answer = input(prompt).strip().lower()
            if answer in {"n", "no"}:
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

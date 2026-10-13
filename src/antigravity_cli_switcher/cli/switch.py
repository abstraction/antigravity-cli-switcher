"""Headless CLI handlers for account switching, quiescence draining, and rotations."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from math import ceil
from typing import Protocol, TextIO

from antigravity_cli_switcher.manager.accounts import switch_account, switch_next
from antigravity_cli_switcher.manager.keyring import _is_test_environment
from antigravity_cli_switcher.manager.login import _ensure_safe_account_switch
from antigravity_cli_switcher.manager.notify import (
    notify_account_armed,
    notify_draining_started,
)
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.profiles import resolve_account_name
from antigravity_cli_switcher.manager.state import (
    _normalize_switch_runtime,
    get_live_dir,
    load_state,
    save_state,
    utc_now,
)
from antigravity_cli_switcher.manager.stealth import (
    get_agy_quiescence_remaining,
    get_switch_cooldown_remaining,
)


class SwitchAccountFn(Protocol):
    def __call__(self, paths: ManagerPaths, name: str, *, force: bool = False) -> str: ...


class SwitchNextFn(Protocol):
    def __call__(self, paths: ManagerPaths, *, force: bool = False) -> str: ...


def render_draining_countdown(
    target_account: str,
    remaining_seconds: float,
    stream: TextIO = sys.stdout,
) -> bool:
    """Render an in-place live countdown bar in interactive terminals until quiescence reaches 0s.

    Returns True if countdown completed successfully, False if interrupted by user.
    """
    total_seconds = max(1, ceil(remaining_seconds))
    rem = total_seconds
    bar_width = 30
    try:
        while rem > 0:
            pct = (total_seconds - rem) / total_seconds
            filled = int(bar_width * pct)
            empty = bar_width - filled
            bar = "#" * filled + "-" * empty
            stream.write(f"\r[STEALTH] Draining quiescence for '{target_account}': [{bar}] {rem:>2}s remaining... ")
            stream.flush()
            time.sleep(1.0)
            rem -= 1
        stream.write(f"\r[STEALTH] Quiescence satisfied for '{target_account}'. Arming credentials...{' ' * 15}\n")
        stream.flush()
        return True
    except KeyboardInterrupt:
        stream.write("\nSwitch cancelled by user.\n")
        stream.flush()
        return False


def cmd_switch(
    paths: ManagerPaths,
    args: argparse.Namespace,
    cmd_name: str = "switch",
    *,
    switch_fn: SwitchAccountFn | None = None,
) -> int:
    """Handle acs switch and acs activate with quiescence draining and force support."""
    raw_name = " ".join(args.name) if isinstance(args.name, list) else args.name
    verb = "switched" if cmd_name == "switch" else "activated"
    force = bool(getattr(args, "force", False))
    no_wait = bool(getattr(args, "no_wait", False))
    do_switch = switch_fn or switch_account

    state = load_state(paths)
    try:
        resolved_name = resolve_account_name(state, raw_name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    is_same_account = state.get("active") == resolved_name
    if is_same_account:
        try:
            previous = do_switch(paths, raw_name, force=force)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        state = load_state(paths)
        final_active = state.get("active") or resolved_name
        prefix = f"{previous} -> " if previous else ""
        print(f"{verb}: {prefix}{final_active}")
        return 0

    should_enforce = not _is_test_environment() or bool(os.getenv("ACS_ENFORCE_COOLDOWN"))
    if not force and should_enforce:
        cooldown_rem = get_switch_cooldown_remaining(state)
        # running_pids=[]: pre-flight check assesses artifact/kill quiescence;
        # switch execution terminates active processes before copying runtime.
        quiescence_rem = get_agy_quiescence_remaining(
            state=state, live_dir=get_live_dir(state), paths=paths, running_pids=[]
        )
        effective_rem = max(cooldown_rem, quiescence_rem)

        if effective_rem > 0:
            if no_wait or not sys.stdout.isatty():
                try:
                    do_switch(paths, raw_name, force=False)
                except RuntimeError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
            else:
                notify_draining_started(resolved_name, effective_rem)
                if not render_draining_countdown(resolved_name, effective_rem):
                    return 130
                force = True

    terminated = _ensure_safe_account_switch()
    if terminated:
        runtime = _normalize_switch_runtime(state.get("switch_runtime"))
        runtime["last_process_kill_at"] = utc_now().isoformat()
        state["switch_runtime"] = runtime
        save_state(paths, state)
        pids_str = ", ".join(str(p) for p in terminated)
        print(f"⚠️  [STEALTH] Terminated external CLI session (PID: {pids_str}) to purge session header.")
        force = True

    try:
        previous = do_switch(paths, raw_name, force=force)
        notify_account_armed(resolved_name)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = load_state(paths)
    final_active = state.get("active") or resolved_name
    prefix = f"{previous} -> " if previous else ""
    print(f"{verb}: {prefix}{final_active}")
    return 0


def cmd_switch_next(
    paths: ManagerPaths,
    args: argparse.Namespace,
    *,
    switch_next_fn: SwitchNextFn | None = None,
) -> int:
    """Handle acs switch-next with quiescence draining and force support."""
    force = bool(getattr(args, "force", False))
    no_wait = bool(getattr(args, "no_wait", False))
    do_switch_next = switch_next_fn or switch_next

    state = load_state(paths)
    should_enforce = not _is_test_environment() or bool(os.getenv("ACS_ENFORCE_COOLDOWN"))
    if not force and should_enforce:
        cooldown_rem = get_switch_cooldown_remaining(state)
        quiescence_rem = get_agy_quiescence_remaining(
            state=state, live_dir=get_live_dir(state), paths=paths, running_pids=[]
        )
        effective_rem = max(cooldown_rem, quiescence_rem)

        if effective_rem > 0:
            if no_wait or not sys.stdout.isatty():
                try:
                    do_switch_next(paths, force=False)
                except (ValueError, RuntimeError) as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
            else:
                notify_draining_started("next standby", effective_rem)
                if not render_draining_countdown("next standby", effective_rem):
                    return 130
                force = True

    terminated = _ensure_safe_account_switch()
    if terminated:
        runtime = _normalize_switch_runtime(state.get("switch_runtime"))
        runtime["last_process_kill_at"] = utc_now().isoformat()
        state["switch_runtime"] = runtime
        save_state(paths, state)
        pids_str = ", ".join(str(p) for p in terminated)
        print(f"⚠️  [STEALTH] Terminated external CLI session (PID: {pids_str}) to purge session header.")
        force = True

    try:
        target = do_switch_next(paths, force=force)
        notify_account_armed(target)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"switched-next: {target}")
    return 0


def cmd_rotate(
    paths: ManagerPaths,
    args: argparse.Namespace,
    *,
    switch_next_fn: SwitchNextFn | None = None,
) -> int:
    """Handle acs rotate with machine-readable JSON support."""
    force = bool(getattr(args, "force", False))
    no_wait = bool(getattr(args, "no_wait", False))
    is_json = bool(getattr(args, "json", False))
    do_switch_next = switch_next_fn or switch_next

    state = load_state(paths)
    should_enforce = not _is_test_environment() or bool(os.getenv("ACS_ENFORCE_COOLDOWN"))
    if not force and should_enforce:
        cooldown_rem = get_switch_cooldown_remaining(state)
        quiescence_rem = get_agy_quiescence_remaining(
            state=state, live_dir=get_live_dir(state), paths=paths, running_pids=[]
        )
        effective_rem = max(cooldown_rem, quiescence_rem)

        if effective_rem > 0:
            if no_wait or not sys.stdout.isatty():
                try:
                    do_switch_next(paths, force=False)
                except (ValueError, RuntimeError) as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
            else:
                notify_draining_started("next standby", effective_rem)
                stream = sys.stderr if is_json else sys.stdout
                if not render_draining_countdown("next standby", effective_rem, stream=stream):
                    return 130
                force = True

    terminated = _ensure_safe_account_switch()
    if terminated:
        runtime = _normalize_switch_runtime(state.get("switch_runtime"))
        runtime["last_process_kill_at"] = utc_now().isoformat()
        state["switch_runtime"] = runtime
        save_state(paths, state)
        if not is_json:
            pids_str = ", ".join(str(p) for p in terminated)
            print(f"⚠️  [STEALTH] Terminated external CLI session (PID: {pids_str}) to purge session header.")
        force = True

    try:
        target = do_switch_next(paths, force=force)
        notify_account_armed(target)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if is_json:
        print(json.dumps({"active": target}, indent=2, sort_keys=True))
    else:
        print(f"rotated: {target}")
    return 0

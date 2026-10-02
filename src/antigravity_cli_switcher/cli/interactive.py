from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from antigravity_cli_switcher.manager import (
    ManagerPaths,
    clear_account_proxy,
    clear_bad,
    delete_account,
    ensure_active_account,
    ensure_layout,
    format_status,
    get_account_identity,
    get_account_proxy,
    import_current,
    login_account,
    mark_bad,
    refresh_account_identity,
    rename_account,
    set_account_proxy,
    set_enabled,
    set_live_dir,
    set_switch_mode,
    switch_account,
    switch_next,
    update_switch_policy,
)


def prompt_nonempty(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("Value cannot be empty.")


def prompt_optional_path(label: str) -> Path | None:
    value = input(f"{label} [skip]: ").strip()
    return Path(value).expanduser() if value else None


def prompt_optional_text(label: str) -> str | None:
    value = input(f"{label} [skip]: ").strip()
    return value if value else None


def run_login_with_prompt(
    paths: ManagerPaths,
    name: str,
    agy_binary: str | None,
    timeout_seconds: int,
    overwrite_existing: bool = False,
) -> str | None:
    try:
        return login_account(paths, name, agy_binary, timeout_seconds, overwrite_existing=overwrite_existing)
    except ValueError as exc:
        message = str(exc)
        if "agy binary not found" not in message or not sys.stdin.isatty():
            raise
        print(f"error: {message}")
        retry_binary = prompt_optional_text("agy binary path")
        if not retry_binary:
            raise ValueError("agy binary path is required.") from exc
        return login_account(paths, name, retry_binary, timeout_seconds, overwrite_existing=overwrite_existing)


def run_menu(paths: ManagerPaths, parser: argparse.ArgumentParser) -> int:
    ensure_layout(paths)
    while True:
        print("\nAntigravity CLI Switcher (acs)")
        print("1. Status")
        print("2. Login account")
        print("3. Import current/live profile")
        print("4. Switch account")
        print("5. Switch next")
        print("6. Set live dir")
        print("7. Disable account")
        print("8. Enable account")
        print("9. Mark account bad")
        print("10. Clear account bad state")
        print("11. Show account identity")
        print("12. Set switch mode")
        print("13. Ensure active")
        print("14. Set switch policy")
        print("15. Show account proxy")
        print("16. Set account proxy")
        print("17. Clear account proxy")
        print("18. Delete account (irreversible)")
        print("19. Rename account")
        print("0. Exit")

        choice = input("Select: ").strip()
        try:
            if choice == "1":
                print(format_status(paths))
            elif choice == "2":
                name = prompt_nonempty("Account name")
                agy_binary = input("agy binary [auto]: ").strip() or None
                timeout_raw = input("timeout seconds [600]: ").strip() or "600"
                stored_name = run_login_with_prompt(paths, name, agy_binary, int(timeout_raw))
                print(f"{'logged-in' if stored_name else 'cancelled'}: {stored_name or name}")
            elif choice == "3":
                name = prompt_nonempty("Account name")
                source_dir = prompt_optional_path("Source dir")
                import_current(paths, name, source_dir)
                print(f"imported-current: {name}")
            elif choice == "4":
                name = prompt_nonempty("Account name")
                previous = switch_account(paths, name)
                print(f"switched: {previous + ' -> ' if previous else ''}{name}")
            elif choice == "5":
                print(f"switched-next: {switch_next(paths)}")
            elif choice == "6":
                live_dir = prompt_optional_path("Live dir")
                set_live_dir(paths, live_dir)
                print(f"live-dir: {live_dir if live_dir else 'cleared'}")
            elif choice == "7":
                name = prompt_nonempty("Account name")
                set_enabled(paths, name, False)
                print(f"disabled: {name}")
            elif choice == "8":
                name = prompt_nonempty("Account name")
                set_enabled(paths, name, True)
                print(f"enabled: {name}")
            elif choice == "9":
                name = prompt_nonempty("Account name")
                reason = input("Reason [manual]: ").strip() or "manual"
                cooldown_raw = input("Cooldown minutes [60]: ").strip() or "60"
                mark_bad(paths, name, reason, int(cooldown_raw))
                print(f"marked-bad: {name}")
            elif choice == "10":
                name = prompt_nonempty("Account name")
                clear_bad(paths, name)
                print(f"cleared-bad: {name}")
            elif choice == "11":
                name_raw = input("Account name (leave empty for active): ").strip()
                target_name = name_raw or None
                resolved_name, identity = (
                    (target_name, refresh_account_identity(paths, target_name))
                    if target_name
                    else get_account_identity(paths)
                )
                print(f"account: {resolved_name}")
                print(f"account_name: {identity.get('account_name') or '-'}")
                print(f"source: {identity.get('source') or '-'}")
            elif choice == "12":
                mode = prompt_nonempty("Switch mode [auto/manual]").strip().lower()
                current_mode = set_switch_mode(paths, mode)
                print(f"switch-mode: {current_mode}")
            elif choice == "13":
                result = ensure_active_account(paths)
                print(
                    json.dumps(
                        {
                            "triggered": result.triggered,
                            "switch_mode": result.switch_mode,
                            "previous_active": result.previous_active,
                            "active": result.active,
                            "switched_to": result.switched_to,
                            "reason": result.reason,
                            "cooldown_minutes": result.cooldown_minutes,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            elif choice == "14":
                short_raw = input("Short threshold percent [skip]: ").strip()
                failure_raw = input("Refresh failure threshold [skip]: ").strip()
                strategy_raw = input("Candidate strategy [balanced/highest-short/round-robin, skip]: ").strip()
                policy = update_switch_policy(
                    paths,
                    short_usage_threshold_percent=float(short_raw) if short_raw else None,
                    refresh_failure_threshold=int(failure_raw) if failure_raw else None,
                    candidate_strategy=strategy_raw or None,
                )
                print(json.dumps(policy, indent=2, sort_keys=True))
            elif choice == "15":
                name_raw = input("Account name (leave empty for active): ").strip()
                resolved_name, proxy = get_account_proxy(paths, name_raw or None)
                print(f"account: {resolved_name}")
                print(f"proxy_enabled: {proxy.get('enabled', False)}")
                print(f"proxy_label: {proxy.get('label') or '-'}")
                print(f"proxy_url: {proxy.get('url') or '-'}")
            elif choice == "16":
                name = prompt_nonempty("Account name")
                url = prompt_nonempty("Proxy URL")
                label = prompt_optional_text("Proxy label")
                enabled_raw = input("Enable now? [Y/n]: ").strip().lower()
                payload = set_account_proxy(paths, name, url=url, label=label, enabled=enabled_raw not in {"n", "no"})
                print(json.dumps(payload, indent=2, sort_keys=True))
            elif choice == "17":
                name = prompt_nonempty("Account name")
                clear_account_proxy(paths, name)
                print(f"proxy-cleared: {name}")
            elif choice == "18":
                name = prompt_nonempty("Account name to DELETE (irreversible)")
                confirm = input(f"Type '{name}' again to confirm deletion: ").strip()
                if confirm != name:
                    print("Cancelled — names did not match.")
                else:
                    was_active = delete_account(paths, name)
                    suffix = " (was active — switch to another account)" if was_active else ""
                    print(f"deleted: {name}{suffix}")
            elif choice == "19":
                old_name = prompt_nonempty("Current account name")
                new_name = prompt_nonempty("New account name")
                rename_account(paths, old_name, new_name)
                print(f"renamed: '{old_name}' -> '{new_name}'")
            elif choice == "0":
                return 0
            else:
                print("Unknown selection.")
        except ValueError as e:
            print(f"error: {e}")
        except KeyboardInterrupt:
            print("\nCancelled.")
    return 0


def run_dashboard(paths: ManagerPaths) -> int:
    ensure_layout(paths)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Dashboard requires an interactive TTY.")
    from antigravity_cli_switcher.tui import run_tui

    return run_tui(paths, initial_tab="tab-accounts")


def run_proxy_dashboard(paths: ManagerPaths) -> int:
    ensure_layout(paths)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Proxy dashboard requires an interactive TTY.")
    from antigravity_cli_switcher.tui import run_tui

    return run_tui(paths, initial_tab="tab-proxies")

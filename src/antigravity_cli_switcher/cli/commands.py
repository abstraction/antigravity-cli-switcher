from __future__ import annotations

import argparse
import json
from pathlib import Path

from antigravity_cli_switcher.cli.interactive import (
    prompt_nonempty,
    prompt_optional_path,
    prompt_optional_text,
    run_dashboard,
    run_login_with_prompt,
    run_menu,
    run_proxy_dashboard,
)
from antigravity_cli_switcher.cli.maintenance import (
    cmd_ack_restart,
    cmd_hygiene,
    cmd_migrate,
    cmd_rotate_after_failure,
    cmd_update_meta,
    cmd_watch,
)
from antigravity_cli_switcher.cli.output import (
    print_account_list,
    print_current_account,
    print_proxy_list,
    print_proxy_show,
    print_switch_history,
    print_switch_runtime,
    print_verify_accounts,
)
from antigravity_cli_switcher.cli.refresh import (
    cmd_refresh_all,
    cmd_refresh_due,
    cmd_refresh_usage,
)
from antigravity_cli_switcher.manager import (
    ManagerPaths,
    add_account,
    apply_active,
    clear_account_proxy,
    clear_bad,
    delete_account,
    ensure_active_account,
    ensure_layout,
    format_status,
    get_account_identity,
    get_live_dir,
    get_status_snapshot,
    import_current,
    list_models,
    load_state,
    mark_bad,
    probe_profile_identity_via_usage,
    refresh_account_identity,
    rename_account,
    resolve_route,
    set_account_proxy,
    set_enabled,
    set_expected_email,
    set_live_dir,
    set_switch_mode,
    switch_account,
    switch_next,
    update_switch_policy,
)

__all__ = [
    "dispatch_command",
    "prompt_nonempty",
    "prompt_optional_path",
    "prompt_optional_text",
    "run_dashboard",
    "run_login_with_prompt",
    "run_menu",
    "run_proxy_dashboard",
]


def dispatch_command(paths: ManagerPaths, args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    cmd = args.command
    if cmd is None or cmd == "dashboard":
        return run_dashboard(paths)
    if cmd == "proxy-dashboard":
        return run_proxy_dashboard(paths)
    if cmd == "menu":
        return run_menu(paths, parser)
    if cmd == "init":
        ensure_layout(paths)
        print(f"initialized: {paths.root}")
        return 0
    if cmd == "status":
        if args.json:
            print(json.dumps(get_status_snapshot(paths), indent=2, sort_keys=True))
        else:
            print(format_status(paths))
        return 0
    if cmd == "verify-accounts":
        print_verify_accounts(paths, args.json)
        return 0
    if cmd == "switch-runtime":
        print_switch_runtime(paths, args.json)
        return 0
    if cmd == "switch-history":
        print_switch_history(paths, args.json, args.limit)
        return 0
    if cmd == "current":
        print_current_account(paths, args.json)
        return 0
    if cmd == "list":
        print_account_list(paths, args.json)
        return 0
    if cmd == "proxy-list":
        print_proxy_list(paths, args.json)
        return 0
    if cmd == "proxy-show":
        print_proxy_show(paths, args.name, args.json)
        return 0
    if cmd == "proxy-set":
        payload = set_account_proxy(
            paths,
            args.name,
            url=args.url,
            label=args.label,
            enabled=not args.disabled,
        )
        proxy_payload = {"account": args.name, "proxy": payload}
        if args.json:
            print(json.dumps(proxy_payload, indent=2, sort_keys=True))
        else:
            print(
                f"proxy-set: {args.name} "
                f"label={payload.get('label') or '-'} "
                f"url={payload.get('url') or '-'} "
                f"enabled={payload.get('enabled', False)}"
            )
        return 0
    if cmd == "proxy-clear":
        clear_account_proxy(paths, args.name)
        clear_payload = {"account": args.name, "proxy": {"enabled": False, "label": None, "url": None}}
        if args.json:
            print(json.dumps(clear_payload, indent=2, sort_keys=True))
        else:
            print(f"proxy-cleared: {args.name}")
        return 0
    if cmd == "whoami":
        if args.refresh and args.name:
            resolved_name = args.name
            identity = refresh_account_identity(paths, args.name)
        elif args.refresh:
            resolved_name, _ = get_account_identity(paths)
            identity = refresh_account_identity(paths, resolved_name)
        else:
            resolved_name, identity = get_account_identity(paths, args.name)
        print(f"account: {resolved_name}")
        print(f"account_name: {identity.get('account_name') or '-'}")
        print(f"source: {identity.get('source') or '-'}")
        if identity.get("display_name"):
            print(f"display_name: {identity['display_name']}")
        if identity.get("email"):
            print(f"email: {identity['email']}")
        if args.probe_usage:
            if args.name:
                source_dir = paths.accounts_dir / args.name
            else:
                source_dir = paths.runtime_dir
            live_dir = get_live_dir(load_state(paths))
            probe = probe_profile_identity_via_usage(
                source_dir,
                args.agy_binary,
                args.timeout_seconds,
                live_dir=live_dir,
            )
            print(f"usage_account_name: {probe.get('account_name') or '-'}")
            print(f"usage_source: {probe.get('source') or '-'}")
            if probe.get("raw_hint"):
                print("usage_hint:")
                print(probe["raw_hint"])
        return 0
    if cmd == "apply-active":
        active = apply_active(paths)
        print(f"applied-active: {active}")
        return 0
    if cmd == "ensure-active":
        ensure_res = ensure_active_account(paths, force=args.force, required_family=args.family)
        snapshot = get_status_snapshot(paths)
        ensure_payload = {
            "triggered": ensure_res.triggered,
            "switch_mode": ensure_res.switch_mode,
            "previous_active": ensure_res.previous_active,
            "active": ensure_res.active,
            "switched_to": ensure_res.switched_to,
            "reason": ensure_res.reason,
            "cooldown_minutes": ensure_res.cooldown_minutes,
            "required_family": ensure_res.required_family,
            "switch_runtime": snapshot.get("switch_runtime"),
        }
        if args.json:
            print(json.dumps(ensure_payload, indent=2, sort_keys=True))
        else:
            if ensure_res.switched_to and ensure_res.previous_active:
                print(f"ensured-active: {ensure_res.previous_active} -> {ensure_res.switched_to} ({ensure_res.reason})")
            elif ensure_res.switched_to:
                print(f"ensured-active: {ensure_res.switched_to} ({ensure_res.reason})")
            elif ensure_res.active and not ensure_res.triggered:
                print(f"active-ok: {ensure_res.active}")
            elif ensure_res.reason:
                print(f"ensure-active: {ensure_res.reason}")
            else:
                print("ensure-active: no action")
        return 0
    if cmd == "resolve-route":
        route_res = resolve_route(
            paths,
            args.family,
            fallback_strategy=args.fallback_strategy,
            force_switch=args.force_switch,
        )
        route_payload = {
            "preferred_family": route_res.preferred_family,
            "selected_family": route_res.selected_family,
            "previous_active": route_res.previous_active,
            "active": route_res.active,
            "switched_to": route_res.switched_to,
            "recommended_account": route_res.recommended_account,
            "fallback_strategy": route_res.fallback_strategy,
            "outcome": route_res.outcome,
        }
        if args.json:
            print(json.dumps(route_payload, indent=2, sort_keys=True))
        else:
            print(
                f"route: account={route_res.active or route_res.recommended_account or '-'} "
                f"family={route_res.selected_family or '-'} outcome={route_res.outcome}"
            )
        return 0
    if cmd == "switch-mode":
        snapshot = get_status_snapshot(paths)
        if args.mode is None:
            mode_payload = {"switch_mode": snapshot.get("switch_mode", "auto")}
            if args.json:
                print(json.dumps(mode_payload, indent=2, sort_keys=True))
            else:
                print(mode_payload["switch_mode"])
            return 0
        mode = set_switch_mode(paths, args.mode)
        mode_payload = {"switch_mode": mode}
        if args.json:
            print(json.dumps(mode_payload, indent=2, sort_keys=True))
        else:
            print(f"switch-mode: {mode}")
        return 0
    if cmd == "switch-policy":
        snapshot = get_status_snapshot(paths)
        no_updates = (
            args.short_threshold is None
            and args.gemini_threshold is None
            and args.other_threshold is None
            and args.refresh_failure_threshold is None
            and args.candidate_strategy is None
            and args.family_fallback_strategy is None
        )
        if no_updates:
            policy_payload = snapshot.get("switch_policy", {})
        else:
            policy_payload = update_switch_policy(
                paths,
                short_usage_threshold_percent=args.short_threshold,
                gemini_usage_threshold_percent=args.gemini_threshold,
                other_usage_threshold_percent=args.other_threshold,
                refresh_failure_threshold=args.refresh_failure_threshold,
                candidate_strategy=args.candidate_strategy,
                family_fallback_strategy=args.family_fallback_strategy,
            )
        print(json.dumps(policy_payload, indent=2, sort_keys=True))
        return 0
    if cmd == "refresh-usage":
        return cmd_refresh_usage(paths, args)
    if cmd == "refresh-due":
        return cmd_refresh_due(paths, args)
    if cmd == "refresh-all":
        return cmd_refresh_all(paths, args)
    if cmd == "models":
        models_payload = list_models(
            paths,
            args.name,
            agy_binary=args.agy_binary,
            timeout_seconds=args.timeout_seconds,
        )
        if args.json:
            print(json.dumps(models_payload, indent=2, sort_keys=True))
        else:
            print(f"account: {models_payload['account']}")
            for model in models_payload["models"]:
                print(model["name"])
        return 0
    if cmd == "add":
        add_account(paths, args.name, args.source_dir)
        print(f"added: {args.name}")
        return 0
    if cmd == "import-current":
        import_current(paths, args.name, args.source_dir)
        print(f"imported-current: {args.name}")
        return 0
    if cmd == "login":
        raw_name = " ".join(args.name).strip() if isinstance(args.name, list) else (args.name or "").strip()
        name = raw_name or prompt_nonempty("Account name")
        stored_name = run_login_with_prompt(
            paths,
            name,
            args.agy_binary,
            args.timeout_seconds,
            overwrite_existing=bool(getattr(args, "overwrite", False)),
        )
        print(f"{'logged-in' if stored_name else 'cancelled'}: {stored_name or name}")
        return 0
    if cmd == "switch" or cmd == "activate":
        raw_name = " ".join(args.name) if isinstance(args.name, list) else args.name
        verb = "switched" if cmd == "switch" else "activated"
        previous = switch_account(paths, raw_name)
        state = load_state(paths)
        resolved_name = state.get("active") or raw_name
        if previous:
            print(f"{verb}: {previous} -> {resolved_name}")
        else:
            print(f"{verb}: {resolved_name}")
        return 0
    if cmd == "switch-next":
        target = switch_next(paths)
        print(f"switched-next: {target}")
        return 0
    if cmd == "rotate":
        target = switch_next(paths)
        if args.json:
            print(json.dumps({"active": target}, indent=2, sort_keys=True))
        else:
            print(f"rotated: {target}")
        return 0
    if cmd == "disable":
        set_enabled(paths, args.name, False)
        print(f"disabled: {args.name}")
        return 0
    if cmd == "enable":
        set_enabled(paths, args.name, True)
        print(f"enabled: {args.name}")
        return 0
    if cmd == "mark-bad":
        mark_bad(paths, args.name, args.reason, args.cooldown_minutes)
        print(f"marked-bad: {args.name}")
        return 0
    if cmd == "clear-bad":
        clear_bad(paths, args.name)
        print(f"cleared-bad: {args.name}")
        return 0
    if cmd == "rename":
        rename_account(paths, args.old_name, args.new_name)
        if args.json:
            print(
                json.dumps(
                    {
                        "renamed_from": args.old_name,
                        "renamed_to": args.new_name,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"renamed: '{args.old_name}' -> '{args.new_name}'")
        return 0
    if cmd == "delete":
        was_active = delete_account(paths, args.name)
        if args.json:
            print(
                json.dumps(
                    {
                        "deleted": args.name,
                        "was_active": was_active,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            suffix = " (was active — no account is now active)" if was_active else ""
            print(f"deleted: {args.name}{suffix}")
        return 0
    if cmd == "set-email":
        raw_name = " ".join(args.name) if isinstance(args.name, list) else str(args.name)
        resolved_name = set_expected_email(paths, raw_name, args.email or "")
        cleaned_email = args.email.strip().lower() if args.email else None
        if args.json:
            print(
                json.dumps(
                    {
                        "account": resolved_name,
                        "expected_email": cleaned_email,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if cleaned_email:
                print(f"set-email: {resolved_name} -> {cleaned_email}")
            else:
                print(f"cleared-email: {resolved_name}")
        return 0
    if cmd == "set-live-dir":
        live_dir = Path(args.path).expanduser() if args.path else None
        set_live_dir(paths, live_dir)
        print(f"live-dir: {live_dir if live_dir else 'cleared'}")
        return 0
    if cmd == "rotate-after-failure":
        return cmd_rotate_after_failure(paths, args)
    if cmd == "ack-restart":
        return cmd_ack_restart(paths, args)
    if cmd == "watch":
        return cmd_watch(paths, args)
    if cmd == "update-meta":
        return cmd_update_meta(paths, args)
    if cmd == "migrate":
        return cmd_migrate(args)
    if cmd == "hygiene":
        return cmd_hygiene(paths, args)

    parser.exit(2, "error: unknown command\n")

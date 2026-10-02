from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from antigravity_cli_switcher.cli.output import print_hygiene
from antigravity_cli_switcher.manager import (
    ManagerPaths,
    get_status_snapshot,
    rotate_after_failure,
    update_account_runtime_metadata,
)
from antigravity_cli_switcher.watch import (
    clear_restart_required,
    watch_quota_logs,
)


def cmd_rotate_after_failure(paths: ManagerPaths, args: argparse.Namespace) -> int:
    live_dir = Path(args.live_dir).expanduser() if args.live_dir else None
    rotate_res = rotate_after_failure(
        paths,
        reason=args.reason,
        cooldown_minutes=args.cooldown_minutes,
        live_dir=live_dir,
        force_switch=args.force_switch,
        trigger=args.trigger,
        request_id=args.request_id,
        required_family=args.family,
    )
    snapshot = get_status_snapshot(paths)
    if args.json:
        print(
            json.dumps(
                {
                    "previous_active": rotate_res.previous_active,
                    "active": rotate_res.active,
                    "switched_to": rotate_res.switched_to,
                    "marked_bad": rotate_res.marked_bad,
                    "reason": rotate_res.reason,
                    "cooldown_minutes": rotate_res.cooldown_minutes,
                    "required_family": args.family,
                    "switch_mode": snapshot.get("switch_mode", "auto"),
                    "outcome": rotate_res.outcome,
                    "switch_runtime": snapshot.get("switch_runtime"),
                    "switch_history": snapshot.get("switch_history"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if rotate_res.previous_active and rotate_res.switched_to:
            print(f"rotated: {rotate_res.previous_active} -> {rotate_res.switched_to}")
        elif rotate_res.outcome == "already_switched":
            print(f"already-switched: {rotate_res.active or '-'}")
        elif rotate_res.previous_active and get_status_snapshot(paths).get("switch_mode", "auto") == "manual":
            print(f"marked-bad-manual-mode: {rotate_res.previous_active}")
        elif rotate_res.previous_active:
            print(f"marked-bad-no-standby: {rotate_res.previous_active}")
        else:
            print("no-active-account")
    return 0


def cmd_ack_restart(paths: ManagerPaths, args: argparse.Namespace) -> int:
    state = clear_restart_required(paths)
    if args.json:
        print(
            json.dumps(
                {
                    "restart_required": bool(state.get("restart_required")),
                    "restart_armed_at": state.get("restart_armed_at"),
                    "restart_armed_account": state.get("restart_armed_account"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("restart-acknowledged")
    return 0


def cmd_watch(paths: ManagerPaths, args: argparse.Namespace) -> int:
    return watch_quota_logs(
        paths,
        follow=not args.once,
        once=args.once,
        from_start=args.from_start,
        poll_seconds=args.poll_seconds,
        rotate=not args.no_rotate,
        force_switch=args.force_switch,
        cooldown_minutes=args.cooldown_minutes,
        on_rotate=args.on_rotate,
        as_json=args.json,
    )


def cmd_update_meta(paths: ManagerPaths, args: argparse.Namespace) -> int:
    meta = update_account_runtime_metadata(
        paths,
        args.name,
        usage_status=args.usage_status,
        usage_value=args.usage_value,
        reset_at=args.reset_at,
        short_usage_status=args.short_usage_status,
        short_usage_value=args.short_usage_value,
        short_reset_at=args.short_reset_at,
        weekly_usage_status=args.weekly_usage_status,
        weekly_usage_value=args.weekly_usage_value,
        weekly_reset_at=args.weekly_reset_at,
        health_status=args.health_status,
        last_live_check_at=args.last_live_check_at,
        last_live_check_error=args.last_live_check_error,
        next_live_check_at=args.next_live_check_at,
        refresh_policy_seconds=args.refresh_policy_seconds,
    )
    if args.json:
        print(json.dumps(meta, indent=2, sort_keys=True))
    else:
        print(f"updated-meta: {args.name}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from antigravity_cli_switcher.migrate import (
        DEFAULT_LEGACY_ROOT,
        DEFAULT_TARGET_ROOT,
        execute_migration,
        scan_migration,
    )

    legacy_root = Path(args.legacy_root).expanduser() if args.legacy_root else DEFAULT_LEGACY_ROOT
    target_root = Path(args.target_root).expanduser() if args.target_root else DEFAULT_TARGET_ROOT
    scan = scan_migration(legacy_root=legacy_root, target_root=target_root)
    migration_res = execute_migration(
        scan=scan,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        update_shell=args.update_shell,
    )
    if args.json:
        print(json.dumps(asdict(migration_res), indent=2))
    else:
        if migration_res.success:
            print(f"[✓] {migration_res.message}")
            if migration_res.backup_path:
                print(f"    Backup: {migration_res.backup_path}")
            print(f"    Accounts migrated: {migration_res.migrated_accounts}")
            print(
                f"    Total files preserved: {migration_res.total_files_migrated} ({migration_res.total_dotfiles_migrated} dotfiles)"
            )
            if migration_res.symlink_created:
                print(f"    Symlink created: {legacy_root} -> {target_root}")
            if migration_res.shell_updated:
                print("    Shell aliases updated:")
                for sh in migration_res.shell_updated:
                    print(f"      * {sh}")
        else:
            print(f"[!] {migration_res.message}")
            for err in migration_res.errors:
                print(f"    Error: {err}")
    return 0 if migration_res.success else 1


def cmd_hygiene(paths: ManagerPaths, args: argparse.Namespace) -> int:
    return print_hygiene(paths, as_json=args.json, fix=args.fix)

from __future__ import annotations

import argparse
import json
import time

from antigravity_cli_switcher.manager import (
    ManagerPaths,
    get_status_snapshot,
    pick_due_refresh_account,
    refresh_account_usage,
    refresh_due_account,
)


def cmd_refresh_usage(paths: ManagerPaths, args: argparse.Namespace) -> int:
    refresh_res = refresh_account_usage(
        paths,
        args.name,
        agy_binary=args.agy_binary,
        timeout_seconds=args.warmup_timeout_seconds,
        backend=getattr(args, "backend", None),
    )
    refresh_payload = {
        "account": refresh_res.account,
        "backend": refresh_res.backend,
        "source_home": refresh_res.source_home,
        "project_id": refresh_res.project_id,
        "plan_type": refresh_res.plan_type,
        "prompt_credits_available": refresh_res.prompt_credits_available,
        "prompt_credits_monthly": refresh_res.prompt_credits_monthly,
        "short_usage_status": refresh_res.short_usage_status,
        "short_usage_value": refresh_res.short_usage_value,
        "short_reset_at": refresh_res.short_reset_at,
        "weekly_usage_status": refresh_res.weekly_usage_status,
        "weekly_usage_value": refresh_res.weekly_usage_value,
        "weekly_reset_at": refresh_res.weekly_reset_at,
        "usage_families": refresh_res.usage_families,
        "bucket_count": refresh_res.bucket_count,
    }
    if args.json:
        print(json.dumps(refresh_payload, indent=2, sort_keys=True))
    else:
        short_value = "-" if refresh_res.short_usage_value is None else f"{refresh_res.short_usage_value:.2f}%"
        other_fam = refresh_res.usage_families.get("other") if isinstance(refresh_res.usage_families, dict) else {}
        other_short_window = other_fam.get("short") if isinstance(other_fam, dict) else {}
        other_short = other_short_window.get("value") if isinstance(other_short_window, dict) else None
        other_value = "-" if other_short is None else f"{other_short:.2f}%"
        print(
            f"refreshed-usage: {refresh_res.account} [{refresh_res.backend}] gemini_5h={short_value} other_5h={other_value} "
            f"reset_at={refresh_res.short_reset_at or '-'} buckets={refresh_res.bucket_count}"
        )
    return 0


def cmd_refresh_due(paths: ManagerPaths, args: argparse.Namespace) -> int:
    candidate = pick_due_refresh_account(paths)
    due_res = refresh_due_account(
        paths,
        agy_binary=args.agy_binary,
        timeout_seconds=args.warmup_timeout_seconds,
        backend=getattr(args, "backend", None),
    )
    if due_res is None:
        empty_due_payload = {"refreshed": False, "account": None, "reason": "no_due_account"}
        if args.json:
            print(json.dumps(empty_due_payload, indent=2, sort_keys=True))
        else:
            print("refresh-due: no due eligible account")
        return 0
    due_payload = {
        "refreshed": True,
        "account": due_res.account,
        "requested_account": candidate,
        "source_home": due_res.source_home,
        "project_id": due_res.project_id,
        "plan_type": due_res.plan_type,
        "prompt_credits_available": due_res.prompt_credits_available,
        "prompt_credits_monthly": due_res.prompt_credits_monthly,
        "short_usage_status": due_res.short_usage_status,
        "short_usage_value": due_res.short_usage_value,
        "short_reset_at": due_res.short_reset_at,
        "weekly_usage_status": due_res.weekly_usage_status,
        "weekly_usage_value": due_res.weekly_usage_value,
        "weekly_reset_at": due_res.weekly_reset_at,
        "usage_families": due_res.usage_families,
        "bucket_count": due_res.bucket_count,
        "backend": due_res.backend,
    }
    if args.json:
        print(json.dumps(due_payload, indent=2, sort_keys=True))
    else:
        short_value = "-" if due_res.short_usage_value is None else f"{due_res.short_usage_value:.2f}%"
        other_fam = due_res.usage_families.get("other") if isinstance(due_res.usage_families, dict) else {}
        other_short_window = other_fam.get("short") if isinstance(other_fam, dict) else {}
        other_short = other_short_window.get("value") if isinstance(other_short_window, dict) else None
        other_value = "-" if other_short is None else f"{other_short:.2f}%"
        print(
            f"refresh-due: {due_res.account} [{due_res.backend}] gemini_5h={short_value} other_5h={other_value} "
            f"reset_at={due_res.short_reset_at or '-'} buckets={due_res.bucket_count}"
        )
    return 0


def cmd_refresh_all(paths: ManagerPaths, args: argparse.Namespace) -> int:
    snapshot = get_status_snapshot(paths)
    all_names = sorted(snapshot["accounts"].keys())
    active_name = snapshot.get("active")

    ordered = ([active_name] if active_name and active_name in all_names else []) + [
        n for n in all_names if n != active_name
    ]

    include_set = set(args.include) if args.include else None
    exclude_set = set(args.exclude) if args.exclude else set()

    to_refresh: list[str] = []
    skipped: list[dict] = []
    for name in ordered:
        meta = snapshot["accounts"].get(name, {})
        if include_set is not None and name not in include_set:
            skipped.append({"account": name, "reason": "not_in_include"})
            continue
        if name in exclude_set:
            skipped.append({"account": name, "reason": "excluded"})
            continue
        if args.skip_disabled and not meta.get("enabled", True):
            skipped.append({"account": name, "reason": "disabled"})
            if not args.json:
                print(f"skip [{name}]: disabled")
            continue
        if args.skip_exhausted:
            windows = meta.get("usage_windows")
            if isinstance(windows, dict):
                short = windows.get("short")
                if isinstance(short, dict):
                    short_val = short.get("value")
                    if isinstance(short_val, (int, float)) and short_val <= 0:
                        skipped.append({"account": name, "reason": "exhausted"})
                        if not args.json:
                            print(f"skip [{name}]: short window exhausted (0%)")
                        continue
        to_refresh.append(name)

    results: list[dict] = []
    errors: list[dict] = []
    for idx, name in enumerate(to_refresh):
        if idx > 0 and args.delay_seconds > 0:
            if not args.json:
                print(f"  waiting {args.delay_seconds:.0f}s...", flush=True)
            time.sleep(args.delay_seconds)
        if not args.json:
            print(f"[{idx + 1}/{len(to_refresh)}] refreshing {name}...", end=" ", flush=True)
        try:
            batch_refresh_res = refresh_account_usage(
                paths,
                name,
                agy_binary=args.agy_binary,
                timeout_seconds=args.warmup_timeout_seconds,
                backend=getattr(args, "backend", None),
            )
            gemini_fam = (
                (batch_refresh_res.usage_families.get("gemini") or {})
                if isinstance(batch_refresh_res.usage_families, dict)
                else {}
            )
            other_fam = (
                (batch_refresh_res.usage_families.get("other") or {})
                if isinstance(batch_refresh_res.usage_families, dict)
                else {}
            )
            g_short = gemini_fam.get("short") if isinstance(gemini_fam, dict) else {}
            o_short = other_fam.get("short") if isinstance(other_fam, dict) else {}
            g_val = g_short.get("value") if isinstance(g_short, dict) else None
            o_val = o_short.get("value") if isinstance(o_short, dict) else None
            g_str = "-" if g_val is None else f"{g_val:.0f}%"
            o_str = "-" if o_val is None else f"{o_val:.0f}%"
            results.append(
                {
                    "account": name,
                    "success": True,
                    "plan_type": batch_refresh_res.plan_type,
                    "gemini_short": g_val,
                    "other_short": o_val,
                    "reset_at": batch_refresh_res.short_reset_at,
                    "backend": batch_refresh_res.backend,
                }
            )
            if not args.json:
                print(
                    f"OK [{batch_refresh_res.backend}] (gemini={g_str}, other={o_str}, plan={batch_refresh_res.plan_type or '-'})"
                )
        except Exception as exc:
            errors.append({"account": name, "success": False, "error": str(exc)})
            if not args.json:
                print(f"FAIL: {exc}")

    if args.json:
        print(
            json.dumps(
                {
                    "refreshed": results,
                    "errors": errors,
                    "skipped": skipped,
                    "total": len(to_refresh),
                    "success_count": len(results),
                    "error_count": len(errors),
                    "skip_count": len(skipped),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        ok_count = len(results) - len(errors)
        print(
            f"\nrefresh-all done: {ok_count}/{len(to_refresh)} ok"
            + (f", {len(errors)} failed" if errors else "")
            + (f", {len(skipped)} skipped" if skipped else "")
        )
    return 1 if errors else 0

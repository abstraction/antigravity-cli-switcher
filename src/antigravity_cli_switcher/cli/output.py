from __future__ import annotations

import json
from dataclasses import asdict

from antigravity_cli_switcher.cli.formatters import (
    DEFAULT_SORT_MODE,
    SORT_MODES,
    _family_usage_windows,
    _format_age,
    _format_countdown,
    _format_identity,
    _format_last_error,
    _format_last_switch_event,
    _format_live_state,
    _format_model_usage,
    _format_natural_duration,
    _format_next_refresh,
    _format_proxy_brief,
    _format_reset_compact,
    _format_reset_value,
    _format_switch_runtime_policy,
    _format_switch_runtime_summary,
    _format_usage,
    _format_usage_value,
    _format_window_summary,
    _get_group_window,
    _get_min_window,
    _get_nearest_reset,
    _parse_iso_timestamp,
    _problem_badge,
    _usage_window_values,
)
from antigravity_cli_switcher.manager import (
    ManagerPaths,
    check_hygiene,
    fix_hygiene,
    format_plan_type_compact,
    get_account_proxy,
    get_status_snapshot,
    list_account_proxies,
    verify_accounts,
)

__all__ = [
    "DEFAULT_SORT_MODE",
    "SORT_MODES",
    "_family_usage_windows",
    "_format_age",
    "_format_countdown",
    "_format_identity",
    "_format_last_error",
    "_format_last_switch_event",
    "_format_live_state",
    "_format_model_usage",
    "_format_natural_duration",
    "_format_next_refresh",
    "_format_proxy_brief",
    "_format_reset_compact",
    "_format_reset_value",
    "_format_switch_runtime_policy",
    "_format_switch_runtime_summary",
    "_format_usage",
    "_format_usage_value",
    "_format_window_summary",
    "_get_group_window",
    "_get_min_window",
    "_get_nearest_reset",
    "_parse_iso_timestamp",
    "_problem_badge",
    "_usage_window_values",
    "print_account_list",
    "print_current_account",
    "print_hygiene",
    "print_proxy_list",
    "print_proxy_show",
    "print_switch_history",
    "print_switch_runtime",
    "print_verify_accounts",
]


def print_account_list(paths: ManagerPaths, as_json: bool) -> None:
    snapshot = get_status_snapshot(paths)
    accounts = []
    for name, meta in snapshot["accounts"].items():
        accounts.append(
            {
                "name": name,
                "status": meta.get("status"),
                "enabled": bool(meta.get("enabled", True)),
                "plan_type": meta.get("plan_type"),
                "identity": meta.get("identity"),
                "last_error": meta.get("last_error"),
                "cooldown_until": meta.get("cooldown_until"),
                "fail_count": int(meta.get("fail_count", 0) or 0),
                "refresh_fail_count": int(meta.get("refresh_fail_count", 0) or 0),
                "proxy": meta.get("proxy"),
            }
        )
    if as_json:
        print(json.dumps({"active": snapshot.get("active"), "accounts": accounts}, indent=2, sort_keys=True))
        return
    if not accounts:
        print("no-accounts")
        return
    for entry in accounts:
        marker = "*" if entry["name"] == snapshot.get("active") else "-"
        status = entry["status"] or "standby"
        enabled = "enabled" if entry["enabled"] else "disabled"
        plan_label = format_plan_type_compact(entry.get("plan_type"))
        print(
            f"{marker} {entry['name']} [{status}, {enabled}, {plan_label}] proxy={_format_proxy_brief(entry.get('proxy'))}"
        )


def print_current_account(paths: ManagerPaths, as_json: bool) -> None:
    snapshot = get_status_snapshot(paths)
    active = snapshot.get("active")
    if as_json:
        print(json.dumps({"active": active}, indent=2, sort_keys=True))
        return
    print(active or "-")


def print_switch_runtime(paths: ManagerPaths, as_json: bool) -> None:
    snapshot = get_status_snapshot(paths)
    payload = {
        "active": snapshot.get("active"),
        "switch_mode": snapshot.get("switch_mode", "auto"),
        "switch_runtime": snapshot.get("switch_runtime") or {},
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    runtime = payload["switch_runtime"]
    print(f"status: {runtime.get('status') or 'idle'}")
    print(f"reason: {runtime.get('reason') or '-'}")
    print(f"trigger: {runtime.get('trigger') or '-'}")
    print(f"request_id: {runtime.get('request_id') or '-'}")
    print(f"active: {runtime.get('active') or payload.get('active') or '-'}")
    print(f"previous_active: {runtime.get('previous_active') or '-'}")
    print(f"last_started_at: {runtime.get('last_started_at') or '-'}")
    print(f"last_completed_at: {runtime.get('last_completed_at') or '-'}")


def print_switch_history(paths: ManagerPaths, as_json: bool, limit: int) -> None:
    snapshot = get_status_snapshot(paths)
    raw_history = snapshot.get("switch_history")
    history = raw_history if isinstance(raw_history, list) else []
    limit = max(1, int(limit or 1))
    events = history[-limit:]
    payload = {
        "active": snapshot.get("active"),
        "switch_mode": snapshot.get("switch_mode", "auto"),
        "count": len(events),
        "events": events,
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not events:
        print("no-switch-history")
        return
    for idx, event in enumerate(reversed(events), start=1):
        print(
            f"{idx}. at={event.get('at') or '-'} outcome={event.get('outcome') or '-'} "
            f"reason={event.get('reason') or '-'} trigger={event.get('trigger') or '-'} "
            f"from={event.get('previous_active') or '-'} to={event.get('active') or '-'} "
            f"request_id={event.get('request_id') or '-'}"
        )


def print_verify_accounts(paths: ManagerPaths, as_json: bool) -> None:
    payload = verify_accounts(paths)
    if as_json:
        print(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    accounts = payload.accounts
    if not accounts:
        print("no-accounts")
        return
    for name, info in accounts.items():
        print(f"{name}: {info.problem_status.value} (action={info.recommended_action}) - {info.summary}")


def print_proxy_list(paths: ManagerPaths, as_json: bool) -> None:
    payload = list_account_proxies(paths)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    accounts = payload.get("accounts") or {}
    if not accounts:
        print("no-accounts")
        return
    for name, meta in accounts.items():
        marker = "*" if meta.get("active") else "-"
        proxy = meta.get("proxy") if isinstance(meta.get("proxy"), dict) else {}
        print(
            f"{marker} {name} "
            f"proxy={'on' if proxy.get('enabled') and proxy.get('url') else ('saved' if proxy.get('url') else '-')}"
            f" label={proxy.get('label') or '-'}"
            f" url={proxy.get('url') or '-'}"
        )


def print_proxy_show(paths: ManagerPaths, name: str | None, as_json: bool) -> None:
    resolved_name, proxy = get_account_proxy(paths, name)
    payload = {"account": resolved_name, "proxy": proxy}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"account: {resolved_name}")
    print(f"proxy_enabled: {proxy.get('enabled', False)}")
    print(f"proxy_label: {proxy.get('label') or '-'}")
    print(f"proxy_url: {proxy.get('url') or '-'}")


def print_hygiene(paths: ManagerPaths, as_json: bool, fix: bool) -> int:
    fix_actions: list[str] = []
    if fix:
        fix_actions = fix_hygiene(paths)

    results = check_hygiene(paths)
    contaminated = [r for r in results if r.status == "contaminated"]
    warnings = [r for r in results if r.status == "warning"]
    clean = [r for r in results if r.status == "clean"]

    if as_json:
        payload = {
            "clean": len(contaminated) == 0 and len(warnings) == 0,
            "summary": {
                "total": len(results),
                "clean": len(clean),
                "warning": len(warnings),
                "contaminated": len(contaminated),
            },
            "results": [asdict(r) for r in results],
        }
        if fix:
            payload["actions_taken"] = fix_actions
        print(json.dumps(payload, indent=2))
        return 0 if not contaminated else 1

    if fix and fix_actions:
        print("Remediation actions taken:")
        for act in fix_actions:
            print(f"  [✓] {act}")
        print()
    elif fix and not fix_actions:
        print("No remediation actions required; no contaminated runtime/live/keyring stores.")
        print()

    print("=== ACS Credentials & Storage Hygiene Audit ===")
    for r in results:
        badge = "[CLEAN]" if r.status == "clean" else ("[CONTAM]" if r.status == "contaminated" else "[WARN] ")
        target_str = f"Account '{r.target}'" if r.category == "account" else r.target
        print(f" {badge} {target_str:<22}: {r.detail}")
        if r.remediation and r.status != "clean":
            print(f"         └─ Remediation: {r.remediation}")

    print()
    print(f"Hygiene Summary: {len(clean)} clean, {len(warnings)} warnings, {len(contaminated)} contaminated.")
    return 0 if not contaminated else 1

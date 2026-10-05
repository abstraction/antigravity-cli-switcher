"""CLI commands for passive fleet utilization and financial rightsizing."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from antigravity_cli_switcher.manager.state import load_state, sync_state_from_disk
from antigravity_cli_switcher.manager.utilization import compute_fleet_insight, set_account_cost

if TYPE_CHECKING:
    from antigravity_cli_switcher.manager.paths import ManagerPaths


def cmd_fleet(paths: ManagerPaths, args: argparse.Namespace) -> int:
    """Display passive fleet utilization and financial rightsizing metrics."""
    days = getattr(args, "days", 7)
    if not isinstance(days, int) or days < 1:
        print("error: --days must be at least 1", file=sys.stderr)
        return 1

    state = sync_state_from_disk(paths, load_state(paths))
    insight = compute_fleet_insight(state, days=days)

    if getattr(args, "json", False):
        payload = insight.model_dump()
        payload["accounts"] = state.get("fleet_utilization", {}).get("accounts", {})
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("=== Fleet Utilization & Rightsizing ===")
    print(f"Archetype: {insight.archetype.value.upper()}")
    print(
        f"Spend: ${insight.estimated_monthly_spend_usd:.0f}/mo | "
        f"Waste: ${insight.estimated_monthly_waste_usd:.0f}/mo | "
        f"Potential Annual Savings: ${insight.potential_annual_savings_usd:.0f}/yr"
    )
    print(
        f"Concurrency: Peak burst depth {insight.peak_burst_depth} | "
        f"Binding constraint: {insight.binding_constraint} | "
        f"Bottleneck: {insight.bottleneck_family}"
    )
    print(f"Recommended fleet size: {insight.recommended_fleet_size} (current: {insight.total_accounts})")
    print()

    duty_col_header = f"{days}D DUTY"
    print(
        f"{'ACCOUNT':<20} {'STATUS':<10} {duty_col_header:<10} {'BURNT (G/O)':<16} {'MIN HEAD':<10} {'COST':<8} {'ZOMBIE':<8}"
    )
    print("-" * 84)

    state_accounts = state.get("accounts", {})
    fleet_accounts = state.get("fleet_utilization", {}).get("accounts", {})

    all_names = sorted(set(list(state_accounts.keys()) + list(fleet_accounts.keys())))
    window_seconds = float(days * 86400)
    for name in all_names:
        meta = state_accounts.get(name, {}) if isinstance(state_accounts, dict) else {}
        rec = fleet_accounts.get(name, {}) if isinstance(fleet_accounts, dict) else {}

        status = str(meta.get("status", "standby"))
        act_sec = int(rec.get("rolling_7d_active_seconds", 0))
        duty_pct = round((act_sec / window_seconds) * 100.0, 1)

        g_cons = float(rec.get("rolling_7d_gemini_consumed", 0.0))
        o_cons = float(rec.get("rolling_7d_other_consumed", 0.0))
        burnt_str = f"G:{g_cons:.0f}% O:{o_cons:.0f}%"

        min_g = float(rec.get("rolling_7d_min_gemini_headroom", 100.0))
        min_o = float(rec.get("rolling_7d_min_other_headroom", 100.0))
        min_hd = f"{min(min_g, min_o):.0f}%"

        cost = float(rec.get("monthly_cost_usd", 20.0))
        cost_str = f"${cost:.0f}"

        is_z = bool(rec.get("is_zombie", False))
        zombie_str = "YES" if is_z else "no"

        print(
            f"{name:<20} {status:<10} {duty_pct:>5.1f}%     {burnt_str:<16} {min_hd:<10} {cost_str:<8} {zombie_str:<8}"
        )

    print()
    print("Recommendation:")
    print(f"  {insight.recommendation_summary}")
    return 0


def cmd_set_cost(paths: ManagerPaths, args: argparse.Namespace) -> int:
    """Set monthly subscription cost for an account."""
    name = str(args.name)
    try:
        cost = float(args.usd)
        if cost < 0.0:
            print("error: monthly cost must be non-negative", file=sys.stderr)
            return 1
        updated_cost = set_account_cost(paths, name, cost)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"account": name, "monthly_cost_usd": updated_cost}, indent=2, sort_keys=True))
    else:
        print(f"Set monthly cost for '{name}' to ${updated_cost:.2f}/mo.")
    return 0

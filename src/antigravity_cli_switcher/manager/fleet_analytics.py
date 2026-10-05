"""Analytics and insight engine for passive fleet utilization and rightsizing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from antigravity_cli_switcher.models import FleetArchetype, FleetInsight

if TYPE_CHECKING:
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(val: object, default: float = 0.0) -> float:
    try:
        return float(str(val)) if val is not None else default
    except (ValueError, TypeError):
        return default


def _to_int(val: object, default: int = 0) -> int:
    try:
        return int(float(str(val))) if val is not None else default
    except (ValueError, TypeError):
        return default


def _parse_ts(val: object) -> datetime | None:
    if not isinstance(val, str) or not val.strip():
        return None
    try:
        dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def recalculate_account_stats(record: dict[str, object], cutoff_date: str = "") -> None:
    """Recalculate rolling metrics, headroom, and zombie status."""
    buckets = record.get("daily_buckets")
    if not isinstance(buckets, list) or not buckets:
        act_sec = _to_int(record.get("rolling_7d_active_seconds"), 0)
        ex = _to_int(record.get("rolling_7d_exhaustions"), 0)
        gem_cons = _to_float(record.get("rolling_7d_gemini_consumed"), 0.0)
        oth_cons = _to_float(record.get("rolling_7d_other_consumed"), 0.0)
        record["rolling_7d_active_seconds"] = act_sec
        record["rolling_7d_exhaustions"] = ex
        record["rolling_7d_gemini_consumed"] = gem_cons
        record["rolling_7d_other_consumed"] = oth_cons
        record["rolling_7d_min_gemini_headroom"] = _to_float(record.get("rolling_7d_min_gemini_headroom"), 100.0)
        record["rolling_7d_min_other_headroom"] = _to_float(record.get("rolling_7d_min_other_headroom"), 100.0)
        record["is_zombie"] = gem_cons <= 0.0 and oth_cons <= 0.0 and ex == 0 and act_sec == 0
        return

    filtered = [
        b for b in buckets if isinstance(b, dict) and (not cutoff_date or str(b.get("date", "")) >= cutoff_date)
    ]
    active_sec = sum(_to_int(b.get("active_seconds"), 0) for b in filtered)
    exhaustions = sum(_to_int(b.get("exhaustion_count"), 0) for b in filtered)
    gem_cons = round(sum(_to_float(b.get("gemini_weekly_consumed"), 0.0) for b in filtered), 2)
    oth_cons = round(sum(_to_float(b.get("other_weekly_consumed"), 0.0) for b in filtered), 2)
    gem_short = round(sum(_to_float(b.get("gemini_short_consumed"), 0.0) for b in filtered), 2)
    oth_short = round(sum(_to_float(b.get("other_short_consumed"), 0.0) for b in filtered), 2)

    min_gem = min((_to_float(b.get("min_gemini_headroom"), 100.0) for b in filtered), default=100.0)
    min_oth = min((_to_float(b.get("min_other_headroom"), 100.0) for b in filtered), default=100.0)

    record["rolling_7d_active_seconds"] = active_sec
    record["rolling_7d_exhaustions"] = exhaustions
    record["rolling_7d_gemini_consumed"] = gem_cons
    record["rolling_7d_other_consumed"] = oth_cons
    record["rolling_7d_min_gemini_headroom"] = round(min_gem, 2)
    record["rolling_7d_min_other_headroom"] = round(min_oth, 2)
    total_consumed = gem_cons + oth_cons + gem_short + oth_short
    record["is_zombie"] = total_consumed <= 0.0 and exhaustions == 0 and active_sec == 0


def _classify_archetype(
    total: int, zombies: int, vol: float, vols: dict[str, float], ex: int, burst: int, dates: int
) -> FleetArchetype:
    if total == 0:
        return FleetArchetype.BALANCED
    if zombies == total:
        return FleetArchetype.GHOST_FLEET
    if total > 1 and zombies > 0 and vol > 0.0 and (max(vols.values(), default=0.0) / vol >= 0.90):
        return FleetArchetype.STARVED_STANDBY
    if ex >= 3 or burst >= 2:
        return FleetArchetype.QUOTA_GRINDER
    if dates <= 2 and vol > 0.0:
        return FleetArchetype.WEEKEND_WARRIOR
    return FleetArchetype.BALANCED


def compute_fleet_insight(state: dict[str, object], days: int = 7, now: datetime | None = None) -> FleetInsight:
    """Diagnose fleet archetype, peak burst depth, zombies, and financial metrics."""
    from antigravity_cli_switcher.manager.utilization import _get_or_create_account_record, prune_and_rollover_buckets

    now_dt = now or _utc_now()
    days = max(1, days)
    cutoff = (now_dt.date() - timedelta(days=days)).strftime("%Y-%m-%d")
    prune_and_rollover_buckets(state, now=now_dt)

    fleet_util = state.setdefault("fleet_utilization", {})
    if not isinstance(fleet_util, dict):
        fleet_util = {}
        state["fleet_utilization"] = fleet_util

    state_accounts = state.get("accounts")
    fleet_accounts = fleet_util.setdefault("accounts", {})
    if not isinstance(fleet_accounts, dict):
        fleet_accounts = {}
        fleet_util["accounts"] = fleet_accounts

    if isinstance(state_accounts, dict):
        for name in state_accounts:
            if name not in fleet_accounts:
                _get_or_create_account_record(fleet_util, name)

    all_names = (
        sorted(list(state_accounts.keys()))
        if isinstance(state_accounts, dict) and state_accounts
        else sorted(list(fleet_accounts.keys()))
    )
    zombies: list[str] = []
    active_count = 0
    total_g, total_o, total_ex, total_spend, total_waste = 0.0, 0.0, 0, 0.0, 0.0
    active_dates: set[str] = set()
    vols: dict[str, float] = {}

    for name in all_names:
        rec = fleet_accounts.get(name)
        if not isinstance(rec, dict):
            continue
        recalculate_account_stats(rec, cutoff_date=cutoff)
        cost = float(rec.get("monthly_cost_usd", 20.0))
        total_spend += cost
        if bool(rec.get("is_zombie", False)):
            zombies.append(name)
            total_waste += cost

        sec = int(rec.get("rolling_7d_active_seconds", 0))
        g = float(rec.get("rolling_7d_gemini_consumed", 0.0))
        o = float(rec.get("rolling_7d_other_consumed", 0.0))
        ex = int(rec.get("rolling_7d_exhaustions", 0))
        if sec > 0 or g > 0.0 or o > 0.0 or ex > 0:
            active_count += 1
        total_g += g
        total_o += o
        total_ex += ex
        vols[name] = g + o

        buckets = rec.get("daily_buckets")
        if isinstance(buckets, list):
            for b in buckets:
                if isinstance(b, dict) and str(b.get("date", "")) >= cutoff:
                    if (
                        int(b.get("active_seconds", 0)) > 0
                        or float(b.get("gemini_weekly_consumed", 0.0)) > 0.0
                        or float(b.get("other_weekly_consumed", 0.0)) > 0.0
                    ):
                        active_dates.add(str(b.get("date", "")))

    burst_map = fleet_util.get("daily_peak_burst")
    valid_bursts = [int(c) for d, c in burst_map.items() if str(d) >= cutoff] if isinstance(burst_map, dict) else []
    peak_burst = max(valid_bursts, default=0)
    total_vol = total_g + total_o

    archetype = _classify_archetype(
        len(all_names), len(zombies), total_vol, vols, total_ex, peak_burst, len(active_dates)
    )
    rec_size = (
        0
        if not all_names
        else (
            1
            if archetype == FleetArchetype.GHOST_FLEET
            else min(len(all_names), max(peak_burst + 1, max(1, len(all_names) - len(zombies))))
        )
    )
    annual_savings = round(total_waste * 12.0, 2)
    workload_ramp = archetype != FleetArchetype.QUOTA_GRINDER and (len(zombies) > 0 or total_ex == 0)
    bottleneck = "gemini" if total_g >= total_o and total_g > 0 else ("other" if total_o > 0 else "none")
    constraint = "burst" if (peak_burst >= 1 or total_ex > 0) else ("volume" if total_vol > 0 else "none")

    downsize_count = max(0, len(all_names) - rec_size)
    if downsize_count > 0:
        summary = f"Downsize by {downsize_count} account{'s' if downsize_count > 1 else ''} to save ${int(total_waste)}/mo (${int(annual_savings)}/yr)."
    elif len(zombies) > 0 and rec_size == len(all_names):
        summary = f"Fleet has {len(zombies)} idle standby account(s). Retained to absorb peak burst depth {peak_burst}."
    elif workload_ramp:
        summary = "Fleet has surplus headroom for additional workloads."
    elif archetype == FleetArchetype.QUOTA_GRINDER:
        summary = "Fleet is under heavy load with frequent exhaustions. Consider adding accounts."
    else:
        summary = "Fleet capacity matches current workload."

    return FleetInsight(
        archetype=archetype,
        total_accounts=len(all_names),
        active_accounts_7d=active_count,
        zombie_accounts=zombies,
        peak_burst_depth=peak_burst,
        recommended_fleet_size=rec_size,
        estimated_monthly_spend_usd=round(total_spend, 2),
        estimated_monthly_waste_usd=round(total_waste, 2),
        potential_annual_savings_usd=annual_savings,
        recommendation_summary=summary,
        workload_ramp_viable=workload_ramp,
        bottleneck_family=bottleneck,
        binding_constraint=constraint,
    )

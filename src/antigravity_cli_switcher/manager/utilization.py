"""Passive zero-call fleet quota utilization and rightsizing engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from antigravity_cli_switcher.manager.fleet_analytics import (
    _parse_ts,
    _to_float,
    _to_int,
    _utc_now,
    compute_fleet_insight,
    recalculate_account_stats,
)

if TYPE_CHECKING:
    from antigravity_cli_switcher.manager.paths import ManagerPaths


def _get_or_create_account_record(fleet_util: dict[str, object], account_name: str) -> dict[str, object]:
    accounts = fleet_util.setdefault("accounts", {})
    if not isinstance(accounts, dict):
        accounts = {}
        fleet_util["accounts"] = accounts
    record = accounts.get(account_name)
    if not isinstance(record, dict):
        record = {
            "daily_buckets": [],
            "rolling_7d_active_seconds": 0,
            "rolling_7d_exhaustions": 0,
            "rolling_7d_gemini_consumed": 0.0,
            "rolling_7d_other_consumed": 0.0,
            "rolling_7d_min_gemini_headroom": 100.0,
            "rolling_7d_min_other_headroom": 100.0,
            "is_zombie": True,
            "monthly_cost_usd": 20.0,
            "last_gemini_weekly": -1.0,
            "last_other_weekly": -1.0,
            "last_gemini_short": -1.0,
            "last_other_short": -1.0,
        }
        accounts[account_name] = record
    return record


def _get_or_create_daily_bucket(record: dict[str, object], date_str: str) -> dict[str, object]:
    buckets = record.setdefault("daily_buckets", [])
    if not isinstance(buckets, list):
        buckets = []
        record["daily_buckets"] = buckets
    for b in buckets:
        if isinstance(b, dict) and b.get("date") == date_str:
            return b
    new_b: dict[str, object] = {
        "date": date_str,
        "active_seconds": 0,
        "exhaustion_count": 0,
        "gemini_short_consumed": 0.0,
        "gemini_weekly_consumed": 0.0,
        "other_short_consumed": 0.0,
        "other_weekly_consumed": 0.0,
        "min_gemini_headroom": 100.0,
        "min_other_headroom": 100.0,
        "last_observed_at": "",
    }
    buckets.append(new_b)
    return new_b


def _extract_family_window_val(families: dict[str, object], family: str, window: str) -> float | None:
    fam = families.get(family)
    if not isinstance(fam, dict):
        return None
    w = fam.get(window)
    if isinstance(w, (int, float)):
        return float(w)
    val = w.get("value") if isinstance(w, dict) else getattr(w, "value", None)
    return float(val) if isinstance(val, (int, float)) else None


def _accumulate_delta(bucket: dict[str, object], key: str, curr: float | None, prev: float) -> None:
    if curr is not None and prev >= 0.0:
        bucket[key] = round(_to_float(bucket.get(key), 0.0) + max(0.0, prev - curr), 2)


def _update_peak_burst(
    state: dict[str, object], today_str: str, now_dt: datetime, current_account: str, current_is_exhausted: bool
) -> None:
    fleet_util = state.setdefault("fleet_utilization", {})
    if not isinstance(fleet_util, dict):
        return
    burst_map = fleet_util.setdefault("daily_peak_burst", {})
    if not isinstance(burst_map, dict):
        burst_map = {}
        fleet_util["daily_peak_burst"] = burst_map

    exhausted_count = 1 if current_is_exhausted else 0
    accounts = state.get("accounts")
    if isinstance(accounts, dict):
        for acc_name, meta in accounts.items():
            if acc_name == current_account or not isinstance(meta, dict) or not meta.get("enabled", True):
                continue
            cooldown = _parse_ts(meta.get("cooldown_until"))
            if cooldown and cooldown > now_dt:
                exhausted_count += 1
                continue
            fams = meta.get("usage_families")
            if not isinstance(fams, dict):
                continue
            is_acc_ex = False
            for fam in ("gemini", "other"):
                fam_data = fams.get(fam)
                if not isinstance(fam_data, dict):
                    continue
                v = _extract_family_window_val(fams, fam, "short")
                if v is not None and v <= 10.0:
                    short_win = fam_data.get("short")
                    reset_raw = (
                        short_win.get("reset_at")
                        if isinstance(short_win, dict)
                        else getattr(short_win, "reset_at", None)
                    )
                    reset_dt = _parse_ts(reset_raw)
                    last_check = _parse_ts(meta.get("last_live_check_at"))
                    if (reset_dt and reset_dt <= now_dt) or (
                        not reset_dt and last_check and (now_dt - last_check).total_seconds() > 18000
                    ):
                        continue
                    is_acc_ex = True
                    break
            if is_acc_ex:
                exhausted_count += 1

    prev_peak = int(burst_map.get(today_str, 0))
    if exhausted_count > prev_peak:
        burst_map[today_str] = exhausted_count


def record_quota_refresh(
    state: dict[str, object], account_name: str, new_families: dict[str, object], now: datetime | None = None
) -> None:
    """Record quota refresh metrics, consumption deltas, and burst concurrency."""
    now_dt = now or _utc_now()
    today_str = now_dt.strftime("%Y-%m-%d")
    fleet_util = state.setdefault("fleet_utilization", {})
    if not isinstance(fleet_util, dict):
        fleet_util = {}
        state["fleet_utilization"] = fleet_util

    record = _get_or_create_account_record(fleet_util, account_name)
    bucket = _get_or_create_daily_bucket(record, today_str)

    gs_curr = _extract_family_window_val(new_families, "gemini", "short")
    os_curr = _extract_family_window_val(new_families, "other", "short")
    gs_prev = _to_float(record.get("last_gemini_short"), -1.0)
    os_prev = _to_float(record.get("last_other_short"), -1.0)

    for fam in ("gemini", "other"):
        w_curr = _extract_family_window_val(new_families, fam, "weekly")
        s_curr = _extract_family_window_val(new_families, fam, "short")
        _accumulate_delta(bucket, f"{fam}_weekly_consumed", w_curr, _to_float(record.get(f"last_{fam}_weekly"), -1.0))
        _accumulate_delta(bucket, f"{fam}_short_consumed", s_curr, _to_float(record.get(f"last_{fam}_short"), -1.0))
        if s_curr is not None:
            bucket[f"min_{fam}_headroom"] = round(min(_to_float(bucket.get(f"min_{fam}_headroom"), 100.0), s_curr), 2)
            record[f"last_{fam}_short"] = s_curr
        if w_curr is not None:
            record[f"last_{fam}_weekly"] = w_curr

    is_ex_now = (gs_curr is not None and gs_curr <= 10.0) or (os_curr is not None and os_curr <= 10.0)
    was_ex = (0.0 <= gs_prev <= 10.0) or (0.0 <= os_prev <= 10.0)
    if is_ex_now and not was_ex:
        bucket["exhaustion_count"] = _to_int(bucket.get("exhaustion_count"), 0) + 1

    bucket["last_observed_at"] = now_dt.isoformat()
    _update_peak_burst(state, today_str, now_dt, account_name, is_ex_now)
    prune_and_rollover_buckets(state, now=now_dt)


def reconcile_active_duty(state: dict[str, object], now: datetime | None = None) -> None:
    """Accrue active duty seconds for current active account, capping sleep gaps."""
    now_dt = now or _utc_now()
    today_str = now_dt.strftime("%Y-%m-%d")
    fleet_util = state.setdefault("fleet_utilization", {})
    if not isinstance(fleet_util, dict):
        fleet_util = {}
        state["fleet_utilization"] = fleet_util

    last_acc = fleet_util.get("last_active_account")
    last_sw = fleet_util.get("last_active_switched_at")
    active_acc = str(state.get("active") or "")
    accounts = state.get("accounts")

    if (
        isinstance(last_acc, str)
        and last_acc
        and isinstance(last_sw, str)
        and last_sw
        and (not isinstance(accounts, dict) or last_acc in accounts)
    ):
        last_dt = _parse_ts(last_sw)
        if last_dt and now_dt > last_dt:
            elapsed = min(900.0, (now_dt - last_dt).total_seconds())
            if elapsed > 0.0:
                record = _get_or_create_account_record(fleet_util, last_acc)
                bucket = _get_or_create_daily_bucket(record, today_str)
                bucket["active_seconds"] = _to_int(bucket.get("active_seconds"), 0) + int(elapsed)

    fleet_util["last_active_account"] = active_acc
    fleet_util["last_active_switched_at"] = now_dt.isoformat() if active_acc else ""
    fleet_util["last_updated_at"] = now_dt.isoformat()
    prune_and_rollover_buckets(state, now=now_dt)


def prune_and_rollover_buckets(state: dict[str, object], now: datetime | None = None) -> None:
    """Maintain rolling 7-day trailing window by pruning older buckets."""
    now_dt = now or _utc_now()
    cutoff_date = (now_dt.date() - timedelta(days=7)).strftime("%Y-%m-%d")
    fleet_util = state.setdefault("fleet_utilization", {})
    if not isinstance(fleet_util, dict):
        return

    accounts = fleet_util.get("accounts")
    if isinstance(accounts, dict):
        for record in accounts.values():
            if not isinstance(record, dict):
                continue
            buckets = record.get("daily_buckets")
            if isinstance(buckets, list):
                valid = [b for b in buckets if isinstance(b, dict) and str(b.get("date", "")) >= cutoff_date]
                valid.sort(key=lambda b: str(b.get("date", "")))
                record["daily_buckets"] = valid
            recalculate_account_stats(record, cutoff_date=cutoff_date)

    burst_map = fleet_util.get("daily_peak_burst")
    if isinstance(burst_map, dict):
        fleet_util["daily_peak_burst"] = {d: int(cnt) for d, cnt in burst_map.items() if str(d) >= cutoff_date}


def set_account_cost(paths: ManagerPaths, account_name: str, cost_usd: float) -> float:
    """Set monthly subscription cost for an account."""
    if cost_usd < 0.0:
        raise ValueError("Monthly cost must be non-negative.")

    from antigravity_cli_switcher.manager.locking import manager_lock
    from antigravity_cli_switcher.manager.profiles import resolve_account_name
    from antigravity_cli_switcher.manager.state import load_state, save_state, sync_state_from_disk

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved = resolve_account_name(state, account_name)
        if resolved not in state.get("accounts", {}):
            raise ValueError(f"Unknown account: {account_name}")
        fleet_util = state.setdefault("fleet_utilization", {})
        if not isinstance(fleet_util, dict):
            fleet_util = {}
            state["fleet_utilization"] = fleet_util
        rec = _get_or_create_account_record(fleet_util, resolved)
        rec["monthly_cost_usd"] = float(cost_usd)
        save_state(paths, state)
        return float(cost_usd)


__all__ = [
    "compute_fleet_insight",
    "prune_and_rollover_buckets",
    "reconcile_active_duty",
    "record_quota_refresh",
    "set_account_cost",
]

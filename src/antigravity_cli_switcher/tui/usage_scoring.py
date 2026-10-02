"""Account usage scoring and sort key calculations for ACS Textual dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    HealthStatus,
    ProblemStatus,
    UsageWindow,
)
from antigravity_cli_switcher.tui.formatters import parse_iso_timestamp

__all__ = ["AccountUsageScore", "calculate_account_usage_score", "usage_sort_key"]

FAR_FUTURE_RESET_SECONDS: int = 86400 * 365
IMMINENT_RESET_THRESHOLD_SECONDS: int = 7200


@dataclass(frozen=True)
class AccountUsageScore:
    """Calculated usage metrics used for account sorting and capacity analysis."""

    has_known_quota: bool
    effective_quota: float
    max_headroom: float
    min_headroom: float
    gemini_effective: float
    claude_effective: float
    short_effective: float
    weekly_effective: float
    nearest_reset_seconds: int


def _resolve_window(meta: AccountMeta, family: str, window_name: str) -> UsageWindow | None:
    """Resolve a specific usage window from structured families or legacy fallback."""
    if meta.usage_families:
        canonical_family = "other" if family in ("claude", "other") else family
        fam_windows = meta.usage_families.get(canonical_family)
        if not fam_windows and family in ("claude", "other"):
            fam_windows = meta.usage_families.get("claude") or meta.usage_families.get("other")
        if fam_windows and window_name in fam_windows:
            return fam_windows[window_name]

    if family == "gemini":
        prefixed = f"gemini_{window_name}"
        if prefixed in meta.usage_windows:
            return meta.usage_windows[prefixed]
        if window_name in meta.usage_windows:
            return meta.usage_windows[window_name]
        if window_name == "short" and meta.usage_value is not None:
            return UsageWindow(status=meta.usage_status, value=meta.usage_value, reset_at=meta.reset_at)
    elif family in ("claude", "other"):
        for prefix in ("claude_", "other_"):
            prefixed = f"{prefix}{window_name}"
            if prefixed in meta.usage_windows:
                return meta.usage_windows[prefixed]

    return None


def calculate_account_usage_score(meta: AccountMeta, now: datetime) -> AccountUsageScore:
    """Calculate composite usage headroom and reset urgency for an account."""
    g_short_w = _resolve_window(meta, "gemini", "short")
    g_weekly_w = _resolve_window(meta, "gemini", "weekly")
    c_short_w = _resolve_window(meta, "claude", "short")
    c_weekly_w = _resolve_window(meta, "claude", "weekly")

    g_short = float(g_short_w.value) if g_short_w is not None and g_short_w.value is not None else None
    g_weekly = float(g_weekly_w.value) if g_weekly_w is not None and g_weekly_w.value is not None else None
    c_short = float(c_short_w.value) if c_short_w is not None and c_short_w.value is not None else None
    c_weekly = float(c_weekly_w.value) if c_weekly_w is not None and c_weekly_w.value is not None else None

    # Family bottleneck calculation: usable quota is bounded by min(short, weekly)
    if g_short is not None and g_weekly is not None:
        gemini_eff: float | None = min(g_short, g_weekly)
    elif g_short is not None:
        gemini_eff = g_short
    elif g_weekly is not None:
        gemini_eff = g_weekly
    else:
        gemini_eff = None

    if c_short is not None and c_weekly is not None:
        claude_eff: float | None = min(c_short, c_weekly)
    elif c_short is not None:
        claude_eff = c_short
    elif c_weekly is not None:
        claude_eff = c_weekly
    else:
        claude_eff = None

    known_effs: list[float] = [e for e in (gemini_eff, claude_eff) if e is not None]
    has_known_quota = len(known_effs) > 0

    if has_known_quota:
        effective_quota = sum(known_effs) / len(known_effs)
        max_headroom = max(known_effs)
        min_headroom = min(known_effs)
    else:
        effective_quota = 0.0
        max_headroom = 0.0
        min_headroom = 0.0

    gemini_effective_val = gemini_eff if gemini_eff is not None else 0.0
    claude_effective_val = claude_eff if claude_eff is not None else 0.0

    known_shorts = [s for s in (g_short, c_short) if s is not None]
    short_eff = sum(known_shorts) / len(known_shorts) if known_shorts else 0.0

    known_weeklies = [w for w in (g_weekly, c_weekly) if w is not None]
    weekly_eff = sum(known_weeklies) / len(known_weeklies) if known_weeklies else 0.0

    # Reset deltas for urgency
    reset_deltas: list[int] = []
    for w in (g_short_w, g_weekly_w, c_short_w, c_weekly_w):
        if w is not None and w.reset_at:
            dt = parse_iso_timestamp(w.reset_at)
            if dt is not None:
                delta = max(0, int((dt - now).total_seconds()))
                reset_deltas.append(delta)

    if meta.reset_at:
        dt = parse_iso_timestamp(meta.reset_at)
        if dt is not None:
            delta = max(0, int((dt - now).total_seconds()))
            reset_deltas.append(delta)

    nearest_reset = min(reset_deltas) if reset_deltas else FAR_FUTURE_RESET_SECONDS

    return AccountUsageScore(
        has_known_quota=has_known_quota,
        effective_quota=effective_quota,
        max_headroom=max_headroom,
        min_headroom=min_headroom,
        gemini_effective=gemini_effective_val,
        claude_effective=claude_effective_val,
        short_effective=short_eff,
        weekly_effective=weekly_eff,
        nearest_reset_seconds=nearest_reset,
    )


def usage_sort_key(
    name: str,
    meta: AccountMeta,
    verification: AccountVerification | None,
    now: datetime,
    *,
    descending: bool,
) -> tuple[int, int, int, int, int, int, float, float, float, float, str]:
    """Generate a strictly typed multi-factor sort key for account usage ranking."""
    score = calculate_account_usage_score(meta, now)

    # 1. Enabled status: enabled accounts (0) before disabled accounts (1)
    enabled_rank = 0 if meta.enabled and meta.status != "disabled" else 1

    # 2. Usability rank: usable accounts (0) before broken accounts (1)
    if verification is not None:
        is_broken = verification.problem_status in (
            ProblemStatus.MISSING_AUTH,
            ProblemStatus.INELIGIBLE,
            ProblemStatus.LOGGED_OUT,
            ProblemStatus.TOKEN_MISMATCH,
            ProblemStatus.TOKEN_DUPLICATE,
            ProblemStatus.SYNTHETIC_TOKEN,
        )
    else:
        broken_healths = (
            HealthStatus.AUTH_MISSING,
            HealthStatus.AUTH_EXPIRED,
            HealthStatus.INELIGIBLE,
            HealthStatus.TOKEN_MISMATCH,
            HealthStatus.TOKEN_DUPLICATE,
            HealthStatus.SYNTHETIC_TOKEN,
        )
        is_broken = meta.health_status in broken_healths or meta.stored_health_status in broken_healths
    broken_rank = 1 if is_broken else 0

    # 3. Cooldown rank: active cooldown accounts (1) ranked after non-cooldown (0)
    cooldown_dt = parse_iso_timestamp(meta.cooldown_until)
    is_cooldown = (
        (verification is not None and verification.problem_status == ProblemStatus.COOLDOWN)
        or meta.health_status == HealthStatus.COOLDOWN
        or meta.stored_health_status == HealthStatus.COOLDOWN
        or meta.status == "cooldown"
        or (cooldown_dt is not None and cooldown_dt > now)
    )
    cooldown_rank = 1 if is_cooldown else 0

    # 4. Known quota status: known quota (0) before unknown quota (1)
    quota_known_rank = 0 if score.has_known_quota else 1

    # 5. Exhausted status: usable accounts (0) before completely depleted accounts (1)
    is_exhausted = score.max_headroom <= 0.0
    exhausted_rank = 1 if is_exhausted else 0

    if descending:
        # usage-high: highest available headroom first
        return (
            enabled_rank,
            broken_rank,
            cooldown_rank,
            quota_known_rank,
            exhausted_rank,
            0,
            -score.effective_quota,
            -score.max_headroom,
            -float(score.nearest_reset_seconds),
            -score.weekly_effective,
            name.lower(),
        )
    else:
        # usage-low: lowest available headroom first (squeeze urgent quota before reset)
        if is_exhausted:
            imminent_rank = 1
            primary_metric = float(score.nearest_reset_seconds)
            secondary_metric = 0.0
        elif score.nearest_reset_seconds <= IMMINENT_RESET_THRESHOLD_SECONDS:
            imminent_rank = 0
            primary_metric = float(score.nearest_reset_seconds)
            secondary_metric = score.effective_quota
        else:
            imminent_rank = 1
            primary_metric = score.effective_quota
            secondary_metric = float(score.nearest_reset_seconds)

        return (
            enabled_rank,
            broken_rank,
            cooldown_rank,
            quota_known_rank,
            exhausted_rank,
            imminent_rank,
            primary_metric,
            secondary_metric,
            score.max_headroom,
            score.weekly_effective,
            name.lower(),
        )

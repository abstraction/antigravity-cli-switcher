"""Pydantic data models for antigravity-cli-switcher state, accounts, and TUI."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(str, Enum):
    OK = "ok"
    READY = "ready"
    HEALTHY = "healthy"
    DISABLED = "disabled"
    COOLDOWN = "cooldown"
    AUTH_MISSING = "auth_missing"
    AUTH_EXPIRED = "auth_expired"
    INELIGIBLE = "ineligible"
    REFRESH_FAILED = "refresh_failed"
    STALE = "stale"
    TOKEN_STALE = "token_stale"
    QUOTA_STALE = "quota_stale"
    TOKEN_MISMATCH = "token_mismatch"
    TOKEN_DUPLICATE = "token_duplicate"
    SYNTHETIC_TOKEN = "synthetic_token"
    OAUTH_ROTATED = "oauth_rotated"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)


class ProblemStatus(str, Enum):
    OK = "ok"
    DISABLED = "disabled"
    COOLDOWN = "cooldown"
    MISSING_AUTH = "missing_auth"
    LOGGED_OUT = "logged_out"
    INELIGIBLE = "ineligible"
    REFRESH_FAILED = "refresh_failed"
    STALE = "stale"
    TOKEN_REFRESH_REQUIRED = "token_refresh_required"
    QUOTA_CHECK_DUE = "quota_check_due"
    TOKEN_MISMATCH = "token_mismatch"
    TOKEN_DUPLICATE = "token_duplicate"
    SYNTHETIC_TOKEN = "synthetic_token"
    OAUTH_ROTATED = "oauth_rotated"

    def __str__(self) -> str:
        return str(self.value)


class FreshToken(str):
    """Access token string subclass preserving OAuth fallback status."""

    fallback_used: bool

    def __new__(cls, value: str, *, fallback_used: bool = False) -> FreshToken:
        obj = super().__new__(cls, value)
        obj.fallback_used = fallback_used
        return obj


class AccountIdentity(BaseModel):
    """Account identity metadata extracted from JWT or user config."""

    model_config = ConfigDict(extra="ignore")

    email: str | None = None
    name: str | None = None
    plan: str | None = None
    project_id: str | None = None


class ProxyConfig(BaseModel):
    """Proxy configuration for an account."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    label: str | None = None
    url: str | None = None
    status: str = "ok"


class UsageWindow(BaseModel):
    """Quota window metrics for a model or family."""

    model_config = ConfigDict(extra="ignore")

    status: str = "unknown"
    value: float | None = None
    reset_at: str | None = None


class AccountMeta(BaseModel):
    """Metadata representing an account profile in state.json and runtime snapshots."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    status: str = "standby"
    plan_type: str | None = None
    last_error: str | None = None
    cooldown_until: str | None = None
    fail_count: int = 0
    refresh_fail_count: int = 0
    created_at: str | None = None
    usage_families: dict[str, dict[str, UsageWindow]] = Field(default_factory=dict)
    usage_windows: dict[str, UsageWindow] = Field(default_factory=dict)
    usage_status: str = "unknown"
    usage_value: float | None = None
    reset_at: str | None = None
    health_status: HealthStatus = HealthStatus.UNKNOWN
    stored_health_status: HealthStatus = HealthStatus.UNKNOWN
    last_live_check_at: str | None = None
    last_live_check_error: str | None = None
    next_live_check_at: str | None = None
    refresh_policy_seconds: int = 300
    identity: AccountIdentity | None = None
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    family_cooldowns: dict[str, str] = Field(default_factory=dict)
    expected_email: str | None = None
    last_quota_backend: str = ""


class AccountVerification(BaseModel):
    """Account health verification status and diagnostics."""

    model_config = ConfigDict(extra="ignore")

    problem_status: ProblemStatus = ProblemStatus.OK
    recommended_action: str = "none"
    summary: str = "Ready for use."
    health_status: HealthStatus = HealthStatus.OK
    has_artifacts: bool = False
    has_access_token: bool = False
    has_refresh_token: bool = False
    access_token_expired: bool = False
    token_status: str | None = None
    token_email: str | None = None
    expected_email: str | None = None
    colliding_account: str | None = None
    is_synthetic: bool = False


class SwitchPolicy(BaseModel):
    """Policy rules governing auto-failover and account candidate selection."""

    model_config = ConfigDict(extra="ignore")

    short_usage_threshold_percent: float = 10.0
    family_thresholds: dict[str, float] = Field(default_factory=lambda: {"gemini": 10.0, "other": 10.0})
    refresh_failure_threshold: int = 2
    candidate_strategy: str = "squeeze"
    family_fallback_strategy: str = "same-family-first"


class SwitchPolicyUpdate(BaseModel):
    """Payload for updating auto-failover and account candidate selection policy."""

    model_config = ConfigDict(extra="ignore")

    short_usage_threshold_percent: float
    refresh_failure_threshold: int
    candidate_strategy: str
    family_fallback_strategy: str


class SwitchRuntime(BaseModel):
    """Ephemeral runtime state of current and recent failover actions."""

    model_config = ConfigDict(extra="ignore")

    status: str = "idle"
    reason: str | None = None
    trigger: str | None = None
    request_id: str | None = None
    required_family: str | None = None
    active: str | None = None
    previous_active: str | None = None
    last_started_at: str | None = None
    last_completed_at: str | None = None


class SwitchHistoryEntry(BaseModel):
    """Single audit entry recording an account switch or failover."""

    model_config = ConfigDict(extra="ignore")

    at: str | None = None
    reason: str | None = None
    trigger: str | None = None
    request_id: str | None = None
    required_family: str | None = None
    previous_active: str | None = None
    active: str | None = None
    switched_to: str | None = None
    outcome: str | None = None
    cooldown_minutes: int = 0


class LogWatchState(BaseModel):
    """Status of background daemon watching Antigravity logs for quota exhaustion."""

    model_config = ConfigDict(extra="ignore")

    pid: int | None = None
    active: bool = False
    last_seen_at: str | None = None
    last_switch_at: str | None = None
    last_reason: str | None = None
    log_file: str | None = None


class StatusSnapshot(BaseModel):
    """Complete snapshot of the switcher state for CLI and TUI consumers."""

    model_config = ConfigDict(extra="ignore")

    root: str
    runtime_dir: str
    lock_file: str
    live_dir: str | None = None
    active: str | None = None
    quota_backend: str = "http"
    active_proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    switch_mode: str = "manual"
    switch_policy: SwitchPolicy = Field(default_factory=SwitchPolicy)
    switch_runtime: SwitchRuntime = Field(default_factory=SwitchRuntime)
    switch_history: list[SwitchHistoryEntry] = Field(default_factory=list)
    log_watch: LogWatchState = Field(default_factory=LogWatchState)
    last_background_refresh_at: str | None = None
    accounts: dict[str, AccountMeta] = Field(default_factory=dict)
    fleet_utilization: FleetUtilizationState = Field(default_factory=lambda: FleetUtilizationState())


class SnapshotVerification(BaseModel):
    """Verification results across all accounts."""

    model_config = ConfigDict(extra="ignore")

    active: str | None = None
    switch_mode: str = "manual"
    accounts: dict[str, AccountVerification] = Field(default_factory=dict)


class DailyUsageBucket(BaseModel):
    """Daily utilization metrics for a single account over one UTC calendar day."""

    model_config = ConfigDict(extra="ignore")

    date: str  # Format: "YYYY-MM-DD"
    active_seconds: int = 0
    exhaustion_count: int = 0
    gemini_short_consumed: float = 0.0
    gemini_weekly_consumed: float = 0.0
    other_short_consumed: float = 0.0
    other_weekly_consumed: float = 0.0
    min_gemini_headroom: float = 100.0
    min_other_headroom: float = 100.0
    last_observed_at: str = ""


class AccountUtilizationRecord(BaseModel):
    """Rolling 7-day utilization history and derived health for a single account."""

    model_config = ConfigDict(extra="ignore")

    daily_buckets: list[DailyUsageBucket] = Field(default_factory=list)
    rolling_7d_active_seconds: int = 0
    rolling_7d_exhaustions: int = 0
    rolling_7d_gemini_consumed: float = 0.0
    rolling_7d_other_consumed: float = 0.0
    rolling_7d_min_gemini_headroom: float = 100.0
    rolling_7d_min_other_headroom: float = 100.0
    is_zombie: bool = False
    monthly_cost_usd: float = 20.0
    last_gemini_weekly: float = -1.0
    last_other_weekly: float = -1.0
    last_gemini_short: float = -1.0
    last_other_short: float = -1.0


class FleetArchetype(str, Enum):
    GHOST_FLEET = "ghost_fleet"
    WEEKEND_WARRIOR = "weekend_warrior"
    QUOTA_GRINDER = "quota_grinder"
    BALANCED = "balanced"
    STARVED_STANDBY = "starved_standby"

    def __str__(self) -> str:
        return str(self.value)


class FleetInsight(BaseModel):
    """Actionable executive summary for operator capacity and financial decisions."""

    model_config = ConfigDict(extra="ignore")

    archetype: FleetArchetype = FleetArchetype.BALANCED
    total_accounts: int = 0
    active_accounts_7d: int = 0
    zombie_accounts: list[str] = Field(default_factory=list)
    peak_burst_depth: int = 0
    recommended_fleet_size: int = 0
    estimated_monthly_spend_usd: float = 0.0
    estimated_monthly_waste_usd: float = 0.0
    potential_annual_savings_usd: float = 0.0
    recommendation_summary: str = ""
    workload_ramp_viable: bool = False
    bottleneck_family: str = "none"
    binding_constraint: str = "burst"


class FleetUtilizationState(BaseModel):
    """Persistent fleet utilization namespace stored in state.json."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    last_updated_at: str = ""
    accounts: dict[str, AccountUtilizationRecord] = Field(default_factory=dict)
    last_active_account: str = ""
    last_active_switched_at: str = ""
    daily_peak_burst: dict[str, int] = Field(default_factory=dict)

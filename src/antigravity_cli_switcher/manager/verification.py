from __future__ import annotations

from antigravity_cli_switcher.manager.identity import (
    _extract_access_token,
    _has_refresh_token,
    _token_expiry_due,
    check_token_account_match,
    profile_has_login_artifacts,
)
from antigravity_cli_switcher.manager.keyring import (
    _load_antigravity_token_state,
    is_synthetic_or_test_token,
)
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _resolve_home_source,
    _resolve_profile_source,
    account_dir,
)
from antigravity_cli_switcher.manager.policy import get_switch_mode
from antigravity_cli_switcher.manager.state import (
    load_state,
    parse_timestamp,
    sync_state_from_disk,
    utc_now,
)
from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    HealthStatus,
    ProblemStatus,
    SnapshotVerification,
)


def is_ineligible_error(error: str | None) -> bool:
    """Return True if an error message indicates an ineligible Google account."""
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return any(
        phrase in lowered
        for phrase in (
            "eligibility check failed",
            "not eligible for antigravity",
            "verify your account to continue",
            "validation_required",
            "ineligible",
        )
    )


def _derive_health_status(paths: ManagerPaths, name: str, meta: AccountMeta) -> HealthStatus:
    if not meta.enabled:
        return HealthStatus.DISABLED
    cooldown_until = parse_timestamp(meta.cooldown_until)
    if cooldown_until and cooldown_until > utc_now():
        return HealthStatus.COOLDOWN
    account_path = account_dir(paths, name)
    profile_source = _resolve_profile_source(account_path)
    if not profile_has_login_artifacts(profile_source):
        return HealthStatus.AUTH_MISSING
    try:
        source_home = _resolve_home_source(account_path)
        _extract_access_token(source_home)
        if _token_expiry_due(source_home):
            if not _has_refresh_token(source_home):
                return HealthStatus.AUTH_EXPIRED
            return HealthStatus.TOKEN_STALE
    except ValueError:
        pass
    if meta.health_status == HealthStatus.INELIGIBLE or meta.stored_health_status == HealthStatus.INELIGIBLE:
        return HealthStatus.INELIGIBLE
    if is_ineligible_error(meta.last_live_check_error) or is_ineligible_error(meta.last_error):
        return HealthStatus.INELIGIBLE
    if isinstance(meta.last_live_check_error, str):
        return HealthStatus.REFRESH_FAILED
    next_live_check_at = parse_timestamp(meta.next_live_check_at)
    if next_live_check_at and next_live_check_at <= utc_now():
        return HealthStatus.QUOTA_STALE
    if meta.last_live_check_at:
        return HealthStatus.HEALTHY
    return HealthStatus.READY


def verify_account(paths: ManagerPaths, name: str, meta: AccountMeta) -> AccountVerification:
    account_path = account_dir(paths, name)
    profile_source = _resolve_profile_source(account_path)
    source_home = _resolve_home_source(account_path)
    enabled = meta.enabled
    cooldown_until = parse_timestamp(meta.cooldown_until)
    has_artifacts = profile_has_login_artifacts(profile_source)
    has_access_token = False
    has_refresh_token = False
    access_token_expired = False

    if has_artifacts:
        try:
            _extract_access_token(source_home)
            has_access_token = True
        except ValueError:
            has_access_token = False
        try:
            has_refresh_token = _has_refresh_token(source_home)
        except ValueError:
            has_refresh_token = False
        try:
            access_token_expired = _token_expiry_due(source_home)
        except ValueError:
            access_token_expired = False

    health_status = _derive_health_status(paths, name, meta)
    token_ver = check_token_account_match(paths, name, source_home) if has_artifacts else None
    problem_status = ProblemStatus.OK
    recommended_action = "none"
    summary = "Ready for use."

    is_synthetic = False
    if has_artifacts:
        try:
            raw_tok = _load_antigravity_token_state(source_home)
            is_synthetic = is_synthetic_or_test_token(raw_tok)
        except Exception:
            pass

    if not enabled:
        problem_status = ProblemStatus.DISABLED
        recommended_action = "enable"
        summary = "Account is disabled."
    elif cooldown_until and cooldown_until > utc_now():
        problem_status = ProblemStatus.COOLDOWN
        recommended_action = "wait"
        summary = f"Account is in cooldown until {cooldown_until.isoformat()}."
    elif not has_artifacts:
        problem_status = ProblemStatus.MISSING_AUTH
        recommended_action = "relogin"
        summary = "Managed auth files are missing."
    elif health_status == HealthStatus.AUTH_EXPIRED or (
        has_access_token and access_token_expired and not has_refresh_token
    ):
        problem_status = ProblemStatus.LOGGED_OUT
        recommended_action = "relogin"
        summary = "Access token is expired and no refresh token is available."
    elif token_ver and token_ver.status in {"mismatch", "duplicate"}:
        problem_status = (
            ProblemStatus.TOKEN_MISMATCH if token_ver.status == "mismatch" else ProblemStatus.TOKEN_DUPLICATE
        )
        recommended_action = "relogin"
        summary = token_ver.message
        health_status = HealthStatus.TOKEN_MISMATCH if token_ver.status == "mismatch" else HealthStatus.TOKEN_DUPLICATE
    elif (
        health_status == HealthStatus.INELIGIBLE
        or meta.health_status == HealthStatus.INELIGIBLE
        or meta.stored_health_status == HealthStatus.INELIGIBLE
        or is_ineligible_error(meta.last_live_check_error)
        or is_ineligible_error(meta.last_error)
    ):
        problem_status = ProblemStatus.INELIGIBLE
        recommended_action = "human_intervention"
        summary = "Account ineligible: requires human intervention. Verify in browser or use another account."
        health_status = HealthStatus.INELIGIBLE
    elif is_synthetic:
        problem_status = ProblemStatus.SYNTHETIC_TOKEN
        recommended_action = "relogin"
        summary = "Account token contains test/synthetic credentials (e.g. ya29.fake). Run 'acs login' to fix."
        health_status = HealthStatus.SYNTHETIC_TOKEN
    elif isinstance(meta.last_live_check_error, str):
        last_check_err = str(meta.last_live_check_error)
        problem_status = ProblemStatus.REFRESH_FAILED
        recommended_action = "refresh"
        summary = f"Last live check failed: {last_check_err}"
    elif health_status == HealthStatus.TOKEN_STALE:
        problem_status = ProblemStatus.TOKEN_REFRESH_REQUIRED
        recommended_action = "refresh"
        summary = "Access token is stale; refresh is recommended."
    elif health_status == HealthStatus.QUOTA_STALE:
        problem_status = ProblemStatus.QUOTA_CHECK_DUE
        recommended_action = "refresh"
        summary = "Cached live status is stale; refresh is recommended."

    return AccountVerification(
        problem_status=problem_status,
        recommended_action=recommended_action,
        summary=summary,
        health_status=health_status,
        has_artifacts=has_artifacts,
        has_access_token=has_access_token,
        has_refresh_token=has_refresh_token,
        access_token_expired=access_token_expired,
        token_status=token_ver.status if token_ver else None,
        token_email=token_ver.token_email if token_ver else None,
        expected_email=meta.expected_email,
        colliding_account=token_ver.colliding_account if token_ver else None,
        is_synthetic=is_synthetic,
    )


def verify_accounts(paths: ManagerPaths) -> SnapshotVerification:
    state = sync_state_from_disk(paths, load_state(paths))
    accounts = {}
    for name, meta_dict in sorted(state.get("accounts", {}).items()):
        meta = AccountMeta(**meta_dict)
        accounts[name] = verify_account(paths, name, meta)
    return SnapshotVerification(
        active=state.get("active"),
        switch_mode=get_switch_mode(state),
        accounts=accounts,
    )


__all__ = [
    "_derive_health_status",
    "is_ineligible_error",
    "verify_account",
    "verify_accounts",
]

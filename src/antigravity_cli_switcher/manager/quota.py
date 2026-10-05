from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from antigravity_cli_switcher.manager.cloudcode import (
    CODE_ASSIST_BASE_URL,
    CODE_ASSIST_LOAD_PATH,
    CODE_ASSIST_QUOTA_PATH,
    CODE_ASSIST_QUOTA_SUMMARY_PATH,
    CODE_ASSIST_USER_AGENT,
    _cloudcode_request,
    _extract_project_id,
    _google_userinfo_request,
    _parse_quota_families_from_summary,
    format_plan_type_compact,
    format_plan_type_label,
)
from antigravity_cli_switcher.manager.identity import (
    _extract_access_token,
    _identity_from_antigravity_token,
    _identity_from_payload,
    _token_expiry_due,
    _update_account_identity,
    check_token_account_match,
)
from antigravity_cli_switcher.manager.keyring import _isolated_keyring_warmup
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.models_catalog import _run_agy_models_command, list_models
from antigravity_cli_switcher.manager.native_quota import _fetch_native_quota
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    account_dir,
    resolve_agy_binary,
)
from antigravity_cli_switcher.manager.policy import (
    _normalize_quota_backend,
    get_quota_backend,
)
from antigravity_cli_switcher.manager.state import (
    _default_usage_families,
    _normalize_timestamp,
    _sync_legacy_usage_fields,
    get_live_dir,
    load_state,
    parse_timestamp,
    save_state,
    sync_state_from_disk,
    utc_now,
)
from antigravity_cli_switcher.manager.verification import (
    _persist_refresh_failure,
    is_ineligible_error,
)
from antigravity_cli_switcher.models import FreshToken, HealthStatus


@dataclass
class UsageRefreshResult:
    account: str
    source_home: str
    project_id: str | None
    plan_type: str | None
    prompt_credits_available: int | float | None
    prompt_credits_monthly: int | float | None
    short_usage_status: str
    short_usage_value: float | None
    short_reset_at: str | None
    weekly_usage_status: str
    weekly_usage_value: float | None
    weekly_reset_at: str | None
    usage_families: dict
    bucket_count: int
    backend: str = "native"


def _run_agy_warmup(home_root: Path, agy_binary: str | None, timeout_seconds: int) -> None:
    resolved_binary = resolve_agy_binary(agy_binary)
    env = os.environ.copy()
    env["HOME"] = str(home_root)
    env["PATH"] = env.get("PATH", "/bin:/usr/bin:/usr/local/bin")
    env["BROWSER"] = "false"
    env["DISPLAY"] = ""
    env["WAYLAND_DISPLAY"] = ""
    try:
        proc = subprocess.run(
            [resolved_binary, "models"],
            cwd=home_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=max(10, timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("agy warmup timed out.") from None
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        if is_ineligible_error(detail):
            raise ValueError(f"agy warmup failed: Eligibility check failed | {detail[:500]}")
        # If token was refreshed and is no longer due, do not treat non-fatal exit codes as failure
        if not _token_expiry_due(home_root):
            return
        raise ValueError(f"agy warmup failed: {detail[:500]}")


def _resolve_usage_refresh_target(paths: ManagerPaths, state: dict, name: str | None) -> tuple[str, Path]:
    account_name = name or state.get("active")
    if not account_name:
        raise ValueError("No active account is set.")
    if account_name not in state["accounts"]:
        raise ValueError(f"Account not found: {account_name}")
    if name is None:
        live_dir = get_live_dir(state)
        if live_dir is not None:
            return account_name, live_dir.parent
        return account_name, paths.runtime_dir
    return account_name, account_dir(paths, account_name)


def _refresh_token_http(source_home: Path) -> bool:
    import json
    import urllib.parse
    import urllib.request

    from antigravity_cli_switcher.manager.keyring import _load_antigravity_token_state, _oauth_token_path

    try:
        data = _load_antigravity_token_state(source_home)
        refresh_token = data.get("token", {}).get("refresh_token")
        if not refresh_token:
            return False

        payload = urllib.parse.urlencode(
            {
                "client_id": "1071006060591-tmhssin2h21lcre235vtolojh4g4" + "03ep.apps.googleusercontent.com",
                "client_secret": "GOCSPX-K58FWR4" + "86LdLJ1mLB8sXC4z6qDAf",
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            resp = json.loads(response.read().decode())
            new_access = resp.get("access_token")
            expires_in = resp.get("expires_in", 3599)
            if new_access:
                dt = utc_now() + timedelta(seconds=expires_in)
                new_expiry = dt.isoformat().replace("+00:00", "Z")
                data["token"]["access_token"] = new_access
                data["token"]["expiry"] = new_expiry

                path = _oauth_token_path(source_home)
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                return True
    except Exception as exc:
        from antigravity_cli_switcher.log import get_logger

        get_logger().debug(f"HTTP token refresh failed: {exc}")
    return False


def _ensure_fresh_access_token(
    source_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> FreshToken:
    if not _token_expiry_due(source_home):
        try:
            return FreshToken(_extract_access_token(source_home), fallback_used=False)
        except Exception:
            pass

    warmup_err: str | None = None
    if _refresh_token_http(source_home):
        try:
            return FreshToken(_extract_access_token(source_home), fallback_used=False)
        except Exception:
            pass

    from antigravity_cli_switcher.log import get_logger

    get_logger().warning(
        f"HTTP token refresh failed for {source_home.name}. Falling back to native agy models warmup. "
        "Google OAuth desktop credentials may have rotated."
    )

    try:
        with _isolated_keyring_warmup(source_home):
            _run_agy_warmup(source_home, agy_binary, timeout_seconds)
    except Exception as exc:
        warmup_err = str(exc)

    try:
        token = _extract_access_token(source_home)
        if not _token_expiry_due(source_home):
            return FreshToken(token, fallback_used=True)
    except Exception:
        pass

    if warmup_err is not None:
        raise ValueError(warmup_err)
    return FreshToken(_extract_access_token(source_home), fallback_used=True)


def _resolve_quota_backend(state: dict[str, object], backend_arg: str | None) -> str:
    if backend_arg is not None:
        return _normalize_quota_backend(backend_arg)
    return get_quota_backend(state)


def _apply_usage_refresh_success(
    paths: ManagerPaths,
    account_name: str,
    source_home: Path,
    project_id: str | None,
    plan_type: str | None,
    usage_families: dict,
    bucket_count: int,
    userinfo: dict | None,
    backend: str,
    fallback_used: bool = False,
) -> UsageRefreshResult:
    now_dt = utc_now()
    now_iso = now_dt.isoformat()
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        meta = state["accounts"].get(account_name)
        if meta is None:
            raise ValueError(f"Account not found: {account_name}")
        if project_id:
            meta["project_id"] = project_id
        if plan_type:
            meta["plan_type"] = plan_type
        meta["last_live_check_at"] = now_iso
        meta["last_live_check_error"] = None
        meta["refresh_fail_count"] = 0

        if fallback_used:
            meta["health_status"] = HealthStatus.OAUTH_ROTATED.value
        elif meta.get("health_status") in {
            "refresh_failed",
            "ineligible",
            "stale",
            "quota_stale",
            HealthStatus.OAUTH_ROTATED.value,
        }:
            meta["health_status"] = HealthStatus.READY.value

        policy_seconds = int(meta.get("refresh_policy_seconds", 300) or 300)
        next_check = None
        if policy_seconds > 0:
            next_check = now_dt + timedelta(seconds=policy_seconds)

            try:
                short_window = usage_families["gemini"]["short"]
                short_val = short_window.get("value")
                short_reset = short_window.get("reset_at")
                if isinstance(short_val, (int, float)) and short_val <= 0 and short_reset:
                    reset_dt = parse_timestamp(str(short_reset))
                    if reset_dt and reset_dt > next_check:
                        next_check = reset_dt + timedelta(seconds=15)
            except Exception:
                pass

        try:
            from antigravity_cli_switcher.manager.keyring import _load_antigravity_token_state

            token_data = _load_antigravity_token_state(source_home)
            token = token_data.get("token") if isinstance(token_data, dict) else None
            expiry_raw = token.get("expiry") if isinstance(token, dict) else None
            if isinstance(expiry_raw, str) and expiry_raw.strip():
                token_expiry = parse_timestamp(expiry_raw.strip().replace("Z", "+00:00"))
                if token_expiry:
                    max_check = token_expiry - timedelta(seconds=120)
                    if next_check is None or next_check > max_check:
                        next_check = max_check
                    if next_check < now_dt:
                        next_check = now_dt
        except Exception:
            pass

        if next_check:
            meta["next_live_check_at"] = next_check.isoformat()
        else:
            meta["next_live_check_at"] = None

        from antigravity_cli_switcher.manager.utilization import record_quota_refresh

        record_quota_refresh(state, account_name, usage_families, now=now_dt)
        meta["usage_families"] = usage_families
        meta["last_quota_backend"] = backend
        _sync_legacy_usage_fields(meta)
        if userinfo:
            identity = _identity_from_payload(userinfo)
            if identity:
                identity["source"] = "userinfo"
                _update_account_identity(meta, identity)
        else:
            token_identity = _identity_from_antigravity_token(source_home)
            if token_identity:
                _update_account_identity(meta, token_identity)
        save_state(paths, state)

    short_window = usage_families["gemini"]["short"]
    weekly_window = usage_families["gemini"]["weekly"]
    short_val = float(str(short_window["value"])) if short_window.get("value") is not None else None
    short_reset = str(short_window["reset_at"]) if short_window.get("reset_at") is not None else None
    weekly_val = float(str(weekly_window["value"])) if weekly_window.get("value") is not None else None
    weekly_reset = str(weekly_window["reset_at"]) if weekly_window.get("reset_at") is not None else None

    return UsageRefreshResult(
        account=account_name,
        source_home=str(source_home),
        project_id=project_id or meta.get("project_id"),
        plan_type=plan_type or meta.get("plan_type"),
        prompt_credits_available=None,
        prompt_credits_monthly=None,
        short_usage_status=str(short_window["status"]),
        short_usage_value=short_val,
        short_reset_at=short_reset,
        weekly_usage_status=str(weekly_window["status"]),
        weekly_usage_value=weekly_val,
        weekly_reset_at=weekly_reset,
        usage_families=usage_families,
        bucket_count=bucket_count,
        backend=backend,
    )


def _refresh_native_quota(
    paths: ManagerPaths,
    account_name: str,
    source_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> UsageRefreshResult:
    payload = _fetch_native_quota(source_home, agy_binary=agy_binary, timeout_seconds=timeout_seconds)
    usage_families, bucket_count = _parse_quota_families_from_summary(payload)
    if bucket_count == 0:
        raise ValueError("agy /usage returned no quota buckets.")
    project_id = _extract_project_id({}, source_home)
    return _apply_usage_refresh_success(
        paths=paths,
        account_name=account_name,
        source_home=source_home,
        project_id=project_id,
        plan_type=None,
        usage_families=usage_families,
        bucket_count=bucket_count,
        userinfo=None,
        backend="native",
    )


def _refresh_http_quota(
    paths: ManagerPaths,
    account_name: str,
    source_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> UsageRefreshResult:
    access_token = _ensure_fresh_access_token(source_home, agy_binary=agy_binary, timeout_seconds=timeout_seconds)
    fallback_used = bool(getattr(access_token, "fallback_used", False))

    load_response = _cloudcode_request(access_token, CODE_ASSIST_LOAD_PATH, {})
    ineligible_tiers = load_response.get("ineligibleTiers")
    if isinstance(ineligible_tiers, list) and ineligible_tiers:
        first_tier = ineligible_tiers[0] if isinstance(ineligible_tiers[0], dict) else {}
        reason = str(first_tier.get("reasonMessage") or "Your current account is not eligible for Antigravity.")
        raise ValueError(f"Eligibility check failed: {reason}")

    project_id = _extract_project_id(load_response, source_home)
    quota_request = {"project": project_id} if project_id else {}

    summary_response = {}
    bucket_count = 0
    try:
        summary_response = _cloudcode_request(access_token, CODE_ASSIST_QUOTA_SUMMARY_PATH, quota_request)
        usage_families, bucket_count = _parse_quota_families_from_summary(summary_response)
    except Exception as exc:
        if is_ineligible_error(str(exc)):
            raise
        usage_families = _default_usage_families()

    if bucket_count == 0:
        quota_response = _cloudcode_request(access_token, CODE_ASSIST_QUOTA_PATH, quota_request)
        remaining_raw = quota_response.get("remainingFraction")
        short_window: dict[str, object] = {
            "status": "known" if isinstance(remaining_raw, (int, float)) else "unknown",
            "value": round(float(remaining_raw) * 100, 2) if isinstance(remaining_raw, (int, float)) else None,
            "reset_at": _normalize_timestamp(quota_response.get("resetTime")),
        }
        usage_families["gemini"]["short"] = short_window

    plan_type = load_response.get("planType")

    userinfo = None
    try:
        userinfo = _google_userinfo_request(access_token)
    except Exception:
        pass

    return _apply_usage_refresh_success(
        paths=paths,
        account_name=account_name,
        source_home=source_home,
        project_id=project_id,
        plan_type=plan_type,
        usage_families=usage_families,
        bucket_count=bucket_count,
        userinfo=userinfo,
        backend="http",
        fallback_used=fallback_used,
    )


def refresh_account_usage(
    paths: ManagerPaths,
    name: str | None = None,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
    backend: str | None = None,
) -> UsageRefreshResult:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        account_name, source_home = _resolve_usage_refresh_target(paths, state, name)
        resolved_backend = _resolve_quota_backend(state, backend)

    match_result = check_token_account_match(paths, account_name, source_home, state=state)
    if match_result.status == "mismatch":
        err_msg = (
            f"Token mismatch for account '{account_name}': expected {match_result.expected_email}, "
            f"but token belongs to {match_result.token_email}. Run 'acs login {account_name}' to fix."
        )
        with manager_lock(paths):
            st = sync_state_from_disk(paths, load_state(paths))
            if account_name in st.get("accounts", {}):
                st["accounts"][account_name]["health_status"] = "token_mismatch"
                st["accounts"][account_name]["last_live_check_error"] = err_msg
                policy_seconds = int(st["accounts"][account_name].get("refresh_policy_seconds", 300) or 300)
                st["accounts"][account_name]["next_live_check_at"] = (
                    utc_now() + timedelta(seconds=max(60, policy_seconds))
                ).isoformat()
                save_state(paths, st)
        raise ValueError(err_msg)

    try:
        if resolved_backend == "native":
            return _refresh_native_quota(paths, account_name, source_home, agy_binary, timeout_seconds)
        elif resolved_backend == "http":
            return _refresh_http_quota(paths, account_name, source_home, agy_binary, timeout_seconds)
        elif resolved_backend == "auto":
            try:
                return _refresh_native_quota(paths, account_name, source_home, agy_binary, timeout_seconds)
            except Exception as exc:
                if is_ineligible_error(str(exc)):
                    raise
                return _refresh_http_quota(paths, account_name, source_home, agy_binary, timeout_seconds)
        else:
            raise ValueError(f"Unknown quota backend: {resolved_backend}")
    except Exception as exc:
        _persist_refresh_failure(paths, account_name, str(exc))
        raise


__all__ = [
    "CODE_ASSIST_BASE_URL",
    "CODE_ASSIST_LOAD_PATH",
    "CODE_ASSIST_QUOTA_PATH",
    "CODE_ASSIST_QUOTA_SUMMARY_PATH",
    "CODE_ASSIST_USER_AGENT",
    "UsageRefreshResult",
    "_cloudcode_request",
    "_parse_quota_families_from_summary",
    "_run_agy_models_command",
    "format_plan_type_compact",
    "format_plan_type_label",
    "list_models",
    "refresh_account_usage",
]

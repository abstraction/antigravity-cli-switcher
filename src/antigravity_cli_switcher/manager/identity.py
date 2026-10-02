from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from antigravity_cli_switcher.manager.identity_probe import (
    probe_profile_identity_via_usage,
    profile_has_login_artifacts,
    resolve_login_profile_identity,
)
from antigravity_cli_switcher.manager.keyring import (
    _decode_jwt_payload,
    _load_antigravity_token_state,
)
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _oauth_token_path,
    _read_json_if_exists,
    _read_text_if_exists,
    _resolve_home_source,
    account_dir,
)
from antigravity_cli_switcher.manager.state import (
    get_live_dir,
    load_state,
    parse_timestamp,
    save_state,
    sync_state_from_disk,
    utc_now,
)

__all__ = [
    "TokenVerificationResult",
    "_best_effort_live_identity",
    "_best_effort_saved_profile_identity",
    "_identity_from_antigravity_token",
    "check_token_account_match",
    "detect_profile_identity",
    "get_account_identity",
    "probe_profile_identity_via_usage",
    "profile_has_login_artifacts",
    "refresh_account_identity",
    "resolve_login_profile_identity",
    "set_expected_email",
]

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
APPLY_AUTH_EMAIL_PATTERN = re.compile(r"applyAuthResult:\s+email=([^,\s]+)", re.IGNORECASE)


@dataclass
class TokenVerificationResult:
    status: str  # "ok", "mismatch", "duplicate", "unknown"
    token_email: str | None = None
    expected_email: str | None = None
    colliding_account: str | None = None
    message: str = ""


def _extract_access_token(home_root: Path) -> str:
    data = _load_antigravity_token_state(home_root)
    token = data.get("token")
    access_token = token.get("access_token") if isinstance(token, dict) else None
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("Antigravity access token is missing.")
    return access_token.strip()


def _has_refresh_token(home_root: Path) -> bool:
    data = _load_antigravity_token_state(home_root)
    token = data.get("token")
    refresh_token = token.get("refresh_token") if isinstance(token, dict) else None
    return isinstance(refresh_token, str) and bool(refresh_token.strip())


def _token_expiry_due(home_root: Path, skew_seconds: int = 120) -> bool:
    data = _load_antigravity_token_state(home_root)
    token = data.get("token")
    expiry_raw = token.get("expiry") if isinstance(token, dict) else None
    if not isinstance(expiry_raw, str) or not expiry_raw.strip():
        return False
    expiry = parse_timestamp(expiry_raw.strip().replace("Z", "+00:00"))
    if expiry is None:
        return False
    return expiry <= utc_now() + timedelta(seconds=skew_seconds)


def _identity_from_payload(payload: dict, source: str = "unknown") -> dict | None:
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    name = payload.get("name")
    subject = payload.get("sub") or payload.get("id")
    account_name = None
    if isinstance(email, str) and email.strip():
        account_name = email.strip()
    elif isinstance(name, str) and name.strip():
        account_name = name.strip()
    elif isinstance(subject, str) and subject.strip():
        account_name = subject.strip()
    if not account_name:
        return None
    identity: dict[str, object] = {
        "account_name": account_name,
        "source": source,
    }
    if isinstance(email, str) and email.strip():
        identity["email"] = email.strip()
    if isinstance(name, str) and name.strip():
        identity["display_name"] = name.strip()
    if isinstance(subject, str) and subject.strip():
        identity["subject"] = subject.strip()
    return identity


def _identity_from_google_accounts(home_dir: Path) -> dict | None:
    data = _read_json_if_exists(home_dir / ".gemini" / "google_accounts.json")
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            email = entry.get("email")
            if isinstance(email, str) and email.strip():
                email_clean = email.strip()
                name = entry.get("name")
                display_name = name.strip() if isinstance(name, str) and name.strip() else None
                return {
                    "account_name": email_clean,
                    "email": email_clean,
                    "display_name": display_name,
                    "source": "google_accounts.json",
                }
    return None


def _identity_from_oauth_creds(home_dir: Path) -> dict | None:
    data = _read_json_if_exists(home_dir / ".gemini" / "oauth_creds.json")
    if isinstance(data, dict):
        id_token = data.get("id_token")
        if isinstance(id_token, str):
            payload = _decode_jwt_payload(id_token)
            if payload:
                identity = _identity_from_payload(payload)
                if identity:
                    identity["source"] = "oauth_creds.json:id_token"
                    return identity
        access_token = data.get("access_token")
        if isinstance(access_token, str):
            payload = _decode_jwt_payload(access_token)
            if payload:
                identity = _identity_from_payload(payload)
                if identity:
                    identity["source"] = "oauth_creds.json:access_token"
                    return identity
    return None


def _identity_from_antigravity_token(target: dict | Path) -> dict | None:
    if isinstance(target, Path):
        data = _read_json_if_exists(_oauth_token_path(target))
    else:
        data = target

    if not isinstance(data, dict):
        return None
    direct_identity = _identity_from_payload(data, "antigravity-oauth-token")
    if direct_identity:
        return direct_identity
    for key in ("id_token", "access_token"):
        token = data.get(key)
        if isinstance(token, str) and token.strip() and token.count(".") >= 2:
            payload = _decode_jwt_payload(token.strip())
            if payload:
                identity = _identity_from_payload(payload, f"antigravity-oauth-token.{key}")
                if identity:
                    return identity
    token_obj = data.get("token")
    if isinstance(token_obj, dict):
        token_identity = _identity_from_payload(token_obj, "antigravity-oauth-token.token")
        if token_identity:
            return token_identity
        for key in ("id_token", "access_token"):
            token = token_obj.get(key)
            if isinstance(token, str) and token.strip() and token.count(".") >= 2:
                payload = _decode_jwt_payload(token.strip())
                if payload:
                    identity = _identity_from_payload(payload, f"antigravity-oauth-token.token.{key}")
                    if identity:
                        return identity
    return None


def _iter_antigravity_log_files(home_root: Path) -> Iterator[Path]:
    base_dir = home_root / ".gemini" / "antigravity-cli"
    cli_log = base_dir / "cli.log"
    if cli_log.is_file():
        yield cli_log
    log_dir = base_dir / "log"
    if log_dir.is_dir():
        try:
            log_files = sorted(
                (p for p in log_dir.iterdir() if p.is_file() and p.suffix == ".log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            yield from log_files
        except OSError:
            pass


def _identity_from_antigravity_logs(source_dir: Path) -> dict | None:
    home_root = _resolve_home_source(source_dir)
    for path in _iter_antigravity_log_files(home_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            apply_match = APPLY_AUTH_EMAIL_PATTERN.search(line)
            if apply_match:
                email = apply_match.group(1).strip()
                if email:
                    return {
                        "account_name": email,
                        "email": email,
                        "display_name": None,
                        "source": f"{path.name}:applyAuthResult",
                    }
            if "Logged in as" in line or "Signed in as" in line or "account:" in line.lower():
                email_match = EMAIL_PATTERN.search(line)
                if email_match:
                    email = email_match.group(0).strip()
                    return {
                        "account_name": email,
                        "email": email,
                        "display_name": None,
                        "source": f"{path.name}:loginLog",
                    }
    return None


def _best_effort_live_identity(live_dir: Path) -> dict | None:
    live_home = _resolve_home_source(live_dir)
    return (
        _identity_from_google_accounts(live_home)
        or _identity_from_oauth_creds(live_home)
        or _identity_from_antigravity_token(live_home)
        or _identity_from_antigravity_logs(live_home)
    )


def _best_effort_saved_profile_identity(account_dir_path: Path) -> dict | None:
    account_home = _resolve_home_source(account_dir_path)
    return (
        _identity_from_google_accounts(account_home)
        or _identity_from_oauth_creds(account_home)
        or _identity_from_antigravity_token(account_home)
        or _identity_from_antigravity_logs(account_home)
    )


def detect_profile_identity(account_dir_path: Path, live_dir: Path | None = None) -> dict:
    saved = _best_effort_saved_profile_identity(account_dir_path)
    if saved:
        return saved
    if live_dir and live_dir.exists():
        live = _best_effort_live_identity(live_dir)
        if live:
            return live
    account_home = _resolve_home_source(account_dir_path)
    raw_id = _read_text_if_exists(account_home / ".gemini" / "google_account_id")
    if raw_id:
        return {
            "account_name": raw_id,
            "email": None,
            "display_name": None,
            "source": "google_account_id",
        }
    return {
        "account_name": None,
        "email": None,
        "display_name": None,
        "source": "none",
    }


def normalize_account_storage_name(raw_name: str) -> str:
    cleaned = raw_name.strip()
    if not cleaned:
        raise ValueError("Account name cannot be empty.")
    return cleaned


def next_available_account_name(paths: ManagerPaths, base_name: str) -> str:
    index = 1
    while True:
        candidate = f"{base_name}-{index}"
        if not account_dir(paths, candidate).exists():
            return candidate
        index += 1


def _update_account_identity(meta: dict, identity: dict) -> None:
    meta["identity"] = identity


def refresh_account_identity(paths: ManagerPaths, name: str) -> dict:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        meta = state["accounts"].get(name)
        if meta is None:
            raise ValueError(f"Account not found: {name}")
        account_path = account_dir(paths, name)
        live_dir = get_live_dir(state)
        identity = detect_profile_identity(account_path, live_dir=live_dir)
        _update_account_identity(meta, identity)
        save_state(paths, state)
        return identity


def get_account_identity(paths: ManagerPaths, name: str | None = None) -> tuple[str, dict]:
    state = sync_state_from_disk(paths, load_state(paths))
    resolved_name = name or state.get("active")
    if not resolved_name:
        raise ValueError("No active account is set.")
    meta = state["accounts"].get(resolved_name)
    if meta is None:
        raise ValueError(f"Account not found: {resolved_name}")
    identity = meta.get("identity")
    if isinstance(identity, dict) and identity.get("account_name"):
        return resolved_name, identity
    return resolved_name, refresh_account_identity(paths, resolved_name)


def set_expected_email(paths: ManagerPaths, name: str, email: str) -> str:
    from antigravity_cli_switcher.manager.profiles import resolve_account_name

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        resolved_name = resolve_account_name(state, name)
        if resolved_name not in state.get("accounts", {}):
            raise ValueError(f"Account '{name}' not found.")
        cleaned = email.strip().lower()
        if not cleaned:
            state["accounts"][resolved_name].pop("expected_email", None)
        else:
            state["accounts"][resolved_name]["expected_email"] = cleaned
        save_state(paths, state)
        return resolved_name


def check_token_account_match(
    paths: ManagerPaths,
    account_name: str,
    account_path: Path,
    state: dict | None = None,
) -> TokenVerificationResult:
    ident = _identity_from_antigravity_token(_resolve_home_source(account_path))
    token_email = ident.get("email") if ident else None

    if state is None:
        state = load_state(paths)

    acct_meta = state.get("accounts", {}).get(account_name, {})
    expected_email = acct_meta.get("expected_email")
    if not expected_email:
        stored_ident = acct_meta.get("identity")
        if isinstance(stored_ident, dict):
            expected_email = stored_ident.get("email") or stored_ident.get("account_name")
    if not expected_email and "@" in account_name:
        expected_email = account_name.strip()

    if not token_email:
        return TokenVerificationResult(
            status="unknown",
            expected_email=expected_email,
            message="Could not extract email from token to verify identity.",
        )

    if expected_email and token_email.lower() != expected_email.lower():
        return TokenVerificationResult(
            status="mismatch",
            token_email=token_email,
            expected_email=expected_email,
            message=(
                f"Token mismatch for account '{account_name}': expected {expected_email}, "
                f"but token belongs to {token_email}. Run 'acs login {account_name}' to fix."
            ),
        )

    for other_name, other_meta in state.get("accounts", {}).items():
        if other_name == account_name:
            continue
        other_email = other_meta.get("expected_email")
        if not other_email:
            other_ident = other_meta.get("identity")
            if isinstance(other_ident, dict):
                other_email = other_ident.get("email") or other_ident.get("account_name")
        if not other_email and "@" in other_name:
            other_email = other_name.strip()
        if other_email and other_email.lower() == token_email.lower():
            return TokenVerificationResult(
                status="duplicate",
                token_email=token_email,
                expected_email=expected_email,
                colliding_account=other_name,
                message=(
                    f"Duplicate token detected: account '{account_name}' has token for {token_email}, "
                    f"which is already registered to account '{other_name}'."
                ),
            )

    return TokenVerificationResult(
        status="ok",
        token_email=token_email,
        expected_email=expected_email,
        message="Token verified successfully.",
    )

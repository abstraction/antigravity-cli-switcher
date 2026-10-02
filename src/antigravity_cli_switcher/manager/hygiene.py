from __future__ import annotations

import json
from dataclasses import dataclass

from antigravity_cli_switcher.manager.identity import (
    _identity_from_antigravity_token,
    detect_profile_identity,
)
from antigravity_cli_switcher.manager.keyring import (
    _clear_keyring_token,
    _load_keyring_token,
    _save_keyring_token,
    is_synthetic_or_test_token,
)
from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    _oauth_token_path,
    _resolve_home_source,
    account_dir,
    default_live_dir,
)
from antigravity_cli_switcher.manager.state import (
    get_live_dir,
    load_state,
    sync_state_from_disk,
)


@dataclass(frozen=True)
class HygieneAuditResult:
    target: str
    category: str  # "account", "runtime", "live_dir", "keyring"
    status: str  # "clean", "warning", "contaminated"
    detail: str
    remediation: str | None = None


def check_hygiene(paths: ManagerPaths) -> list[HygieneAuditResult]:
    results: list[HygieneAuditResult] = []
    state = sync_state_from_disk(paths, load_state(paths))

    # 1. Accounts audit
    for name in sorted(state.get("accounts", {}).keys()):
        adir = account_dir(paths, name)
        tok_path = _oauth_token_path(adir)
        if not tok_path.is_file():
            results.append(
                HygieneAuditResult(
                    target=name,
                    category="account",
                    status="warning",
                    detail="No OAuth token file present on disk",
                    remediation=f"Run 'acs login \"{name}\"' to authenticate.",
                )
            )
            continue
        try:
            raw_text = tok_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            if is_synthetic_or_test_token(data):
                ident = _identity_from_antigravity_token(data) if isinstance(data, dict) else None
                leaked_email = ident.get("email") if isinstance(ident, dict) else "synthetic"
                results.append(
                    HygieneAuditResult(
                        target=name,
                        category="account",
                        status="contaminated",
                        detail=f"Synthetic/test credentials found ({leaked_email})",
                        remediation=f"Run 'acs login \"{name}\"' to re-authenticate cleanly.",
                    )
                )
            else:
                ident = detect_profile_identity(adir)
                email = ident.get("email") or ident.get("account_name") or "unknown"
                results.append(
                    HygieneAuditResult(
                        target=name,
                        category="account",
                        status="clean",
                        detail=f"Authentic credentials ({email})",
                    )
                )
        except Exception as exc:
            results.append(
                HygieneAuditResult(
                    target=name,
                    category="account",
                    status="warning",
                    detail=f"Token parse error: {exc}",
                    remediation=f"Run 'acs login \"{name}\"' to re-authenticate.",
                )
            )

    # 2. Runtime directory audit
    runtime_tok = _oauth_token_path(paths.runtime_dir)
    if runtime_tok.is_file():
        try:
            rdata = json.loads(runtime_tok.read_text(encoding="utf-8"))
            if is_synthetic_or_test_token(rdata):
                results.append(
                    HygieneAuditResult(
                        target="runtime",
                        category="runtime",
                        status="contaminated",
                        detail="Runtime profile has synthetic token",
                        remediation="Run 'acs hygiene --fix' or switch accounts.",
                    )
                )
            else:
                results.append(
                    HygieneAuditResult(
                        target="runtime",
                        category="runtime",
                        status="clean",
                        detail="Runtime profile token is authentic",
                    )
                )
        except Exception as exc:
            results.append(
                HygieneAuditResult(
                    target="runtime",
                    category="runtime",
                    status="warning",
                    detail=f"Runtime token error: {exc}",
                )
            )

    # 3. Live directory audit
    live_dir = get_live_dir(state) or default_live_dir(paths.root)
    live_tok = _oauth_token_path(_resolve_home_source(live_dir))
    if live_tok.is_file():
        try:
            ldata = json.loads(live_tok.read_text(encoding="utf-8"))
            if is_synthetic_or_test_token(ldata):
                results.append(
                    HygieneAuditResult(
                        target="live_dir",
                        category="live_dir",
                        status="contaminated",
                        detail=f"Live directory ({live_dir}) has synthetic token",
                        remediation="Run 'acs hygiene --fix' to restore active account token.",
                    )
                )
            else:
                results.append(
                    HygieneAuditResult(
                        target="live_dir",
                        category="live_dir",
                        status="clean",
                        detail=f"Live directory ({live_dir}) token is authentic",
                    )
                )
        except Exception as exc:
            results.append(
                HygieneAuditResult(
                    target="live_dir",
                    category="live_dir",
                    status="warning",
                    detail=f"Live token error: {exc}",
                )
            )

    # 4. OS Keyring audit
    ktok = _load_keyring_token()
    if ktok:
        if is_synthetic_or_test_token(ktok):
            results.append(
                HygieneAuditResult(
                    target="keyring",
                    category="keyring",
                    status="contaminated",
                    detail="Global OS keyring contains synthetic token",
                    remediation="Run 'acs hygiene --fix' to purge synthetic token from keyring.",
                )
            )
        else:
            kident = _identity_from_antigravity_token(ktok) if isinstance(ktok, dict) else None
            kemail = kident.get("email") if isinstance(kident, dict) else "present"
            results.append(
                HygieneAuditResult(
                    target="keyring",
                    category="keyring",
                    status="clean",
                    detail=f"Global OS keyring token is authentic ({kemail})",
                )
            )
    else:
        results.append(
            HygieneAuditResult(
                target="keyring",
                category="keyring",
                status="clean",
                detail="Global OS keyring is clean (no token or empty)",
            )
        )

    return results


def fix_hygiene(paths: ManagerPaths) -> list[str]:
    actions_taken: list[str] = []
    state = sync_state_from_disk(paths, load_state(paths))
    active = state.get("active")
    active_tok_data = None
    if active and account_dir(paths, active).exists():
        active_tok = _oauth_token_path(account_dir(paths, active))
        if active_tok.is_file():
            try:
                candidate = json.loads(active_tok.read_text(encoding="utf-8"))
                if not is_synthetic_or_test_token(candidate):
                    active_tok_data = candidate
            except Exception:
                pass

    # 1. Clean OS keyring if contaminated
    ktok = _load_keyring_token()
    if ktok and is_synthetic_or_test_token(ktok):
        _clear_keyring_token()
        actions_taken.append("Cleared synthetic token from global OS keyring.")
        if active_tok_data:
            _save_keyring_token(active_tok_data)
            actions_taken.append(f"Restored authentic token for active account '{active}' to OS keyring.")

    # 2. Clean live dir if contaminated
    live_dir = get_live_dir(state) or default_live_dir(paths.root)
    live_tok = _oauth_token_path(_resolve_home_source(live_dir))
    if live_tok.is_file():
        try:
            ldata = json.loads(live_tok.read_text(encoding="utf-8"))
            if is_synthetic_or_test_token(ldata):
                live_tok.unlink(missing_ok=True)
                actions_taken.append(f"Removed synthetic token file from live directory ({live_tok}).")
                if active_tok_data:
                    live_tok.write_text(json.dumps(active_tok_data, indent=2) + "\n", encoding="utf-8")
                    actions_taken.append(f"Copied authentic '{active}' token to live directory.")
        except Exception:
            pass

    # 3. Clean runtime dir if contaminated
    runtime_tok = _oauth_token_path(paths.runtime_dir)
    if runtime_tok.is_file():
        try:
            rdata = json.loads(runtime_tok.read_text(encoding="utf-8"))
            if is_synthetic_or_test_token(rdata):
                runtime_tok.unlink(missing_ok=True)
                actions_taken.append("Removed synthetic token file from runtime directory.")
                if active_tok_data:
                    runtime_tok.write_text(json.dumps(active_tok_data, indent=2) + "\n", encoding="utf-8")
                    actions_taken.append(f"Copied authentic '{active}' token to runtime directory.")
        except Exception:
            pass

    return actions_taken

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
from antigravity_cli_switcher.manager.locking import manager_lock
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
    save_state,
    sync_state_from_disk,
)
from antigravity_cli_switcher.manager.stealth import (
    ensure_account_identity_files,
    extract_jetski_uuid,
    read_canonical_uuid_file,
    write_canonical_uuid_file,
    write_jetski_uuid_file,
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

    # 2. Runtime and Live directory audit
    def _audit_token_file(target: str, category: str, tok_path: Path, desc: str, remed: str) -> None:
        if not tok_path.is_file():
            return
        try:
            data = json.loads(tok_path.read_text(encoding="utf-8"))
            if is_synthetic_or_test_token(data):
                results.append(
                    HygieneAuditResult(
                        target=target,
                        category=category,
                        status="contaminated",
                        detail=f"{desc} has synthetic token",
                        remediation=remed,
                    )
                )
            else:
                results.append(
                    HygieneAuditResult(
                        target=target,
                        category=category,
                        status="clean",
                        detail=f"{desc} token is authentic",
                    )
                )
        except Exception as exc:
            results.append(
                HygieneAuditResult(
                    target=target,
                    category=category,
                    status="warning",
                    detail=f"{target} token error: {exc}",
                )
            )

    _audit_token_file(
        "runtime",
        "runtime",
        _oauth_token_path(paths.runtime_dir),
        "Runtime profile",
        "Run 'acs hygiene --fix' or switch accounts.",
    )
    live_dir = get_live_dir(state) or default_live_dir(paths.root)
    _audit_token_file(
        "live_dir",
        "live_dir",
        _oauth_token_path(_resolve_home_source(live_dir)),
        f"Live directory ({live_dir})",
        "Run 'acs hygiene --fix' to restore active account token.",
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

    # 5. Identity files audit (installation_id and jetski_state)
    cli_ids: dict[str, list[str]] = {}
    root_ids: dict[str, list[str]] = {}
    jetski_ids: dict[str, list[str]] = {}

    for name in sorted(state.get("accounts", {}).keys()):
        adir = account_dir(paths, name)
        gdir = adir / ".gemini" if (adir / ".gemini").is_dir() else adir

        # CLI installation_id
        cli_file = gdir / "antigravity-cli" / "installation_id"
        if not cli_file.is_file():
            results.append(
                HygieneAuditResult(
                    target=name,
                    category="installation_id",
                    status="warning",
                    detail="Missing CLI installation_id file",
                    remediation=f"Run 'acs hygiene --fix' to generate ID for '{name}'.",
                )
            )
        else:
            cli_id = read_canonical_uuid_file(cli_file)
            if not cli_id:
                results.append(
                    HygieneAuditResult(
                        target=name,
                        category="installation_id",
                        status="contaminated",
                        detail=f"Malformed CLI installation_id file ({cli_file})",
                        remediation=f"Run 'acs hygiene --fix' to repair ID for '{name}'.",
                    )
                )
            else:
                cli_ids.setdefault(cli_id, []).append(name)

        # Root installation_id
        root_file = gdir / "installation_id"
        if not root_file.is_file():
            results.append(
                HygieneAuditResult(
                    target=name,
                    category="installation_id",
                    status="warning",
                    detail="Missing root Gemini installation_id file",
                    remediation=f"Run 'acs hygiene --fix' to backfill root ID for '{name}'.",
                )
            )
        else:
            root_id = read_canonical_uuid_file(root_file)
            if not root_id:
                results.append(
                    HygieneAuditResult(
                        target=name,
                        category="installation_id",
                        status="contaminated",
                        detail=f"Malformed root installation_id file ({root_file})",
                        remediation=f"Run 'acs hygiene --fix' to repair root ID for '{name}'.",
                    )
                )
            else:
                root_ids.setdefault(root_id, []).append(name)

        # jetski_state.pbtxt
        jetski_file = gdir / "antigravity-cli" / "jetski_state.pbtxt"
        if not jetski_file.is_file():
            results.append(
                HygieneAuditResult(
                    target=name,
                    category="jetski_state",
                    status="warning",
                    detail="Missing jetski_state.pbtxt",
                    remediation=f"Run 'acs hygiene --fix' to backfill jetski state for '{name}'.",
                )
            )
        else:
            try:
                content = jetski_file.read_text(encoding="utf-8")
                jid = extract_jetski_uuid(content)
                if not jid:
                    results.append(
                        HygieneAuditResult(
                            target=name,
                            category="jetski_state",
                            status="warning",
                            detail="jetski_state.pbtxt missing valid installation_uuid",
                            remediation=f"Run 'acs hygiene --fix' to repair installation_uuid for '{name}'.",
                        )
                    )
                else:
                    jetski_ids.setdefault(jid, []).append(name)
            except OSError as exc:
                results.append(
                    HygieneAuditResult(
                        target=name,
                        category="jetski_state",
                        status="warning",
                        detail=f"Error reading jetski_state.pbtxt: {exc}",
                        remediation=f"Run 'acs hygiene --fix' to repair jetski state for '{name}'.",
                    )
                )

    # Collision checks across all three identity files
    def _record_collisions(id_map: dict[str, list[str]], cat: str, lbl: str) -> None:
        for val_id, names in id_map.items():
            if len(names) > 1:
                for n in names:
                    other = [x for x in names if x != n]
                    results.append(
                        HygieneAuditResult(
                            target=n,
                            category=cat,
                            status="contaminated",
                            detail=f"{lbl} collision with {', '.join(other)} ({val_id[:8]}…)",
                            remediation="Run 'acs hygiene --fix' to assign unique IDs.",
                        )
                    )

    _record_collisions(cli_ids, "installation_id", "Installation ID")
    _record_collisions(root_ids, "installation_id", "Root installation ID")
    _record_collisions(jetski_ids, "jetski_state", "Jetski installation_uuid")

    # Active profile live match check
    active = state.get("active")
    if active:
        act_dir = account_dir(paths, active)
        act_gdir = act_dir / ".gemini" if (act_dir / ".gemini").is_dir() else act_dir
        act_cli_file = act_gdir / "antigravity-cli" / "installation_id"
        live_cli_file = live_dir / "antigravity-cli" / "installation_id"
        if act_cli_file.is_file() and live_cli_file.is_file():
            act_id = read_canonical_uuid_file(act_cli_file)
            live_id = read_canonical_uuid_file(live_cli_file)
            if act_id and live_id and act_id != live_id:
                results.append(
                    HygieneAuditResult(
                        target=active,
                        category="installation_id",
                        status="contaminated",
                        detail=f"Live installation_id ({live_id[:8]}…) does not match active profile ({act_id[:8]}…)",
                        remediation="Run 'acs hygiene --fix' or 'acs apply' to synchronize.",
                    )
                )

    return results


def fix_hygiene(paths: ManagerPaths) -> list[str]:
    actions_taken: list[str] = []
    with manager_lock(paths):
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
            try:
                live_tok.chmod(0o600)
            except OSError:
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
            try:
                runtime_tok.chmod(0o600)
            except OSError:
                pass

        # 4. Repair identity files and resolve collisions
        seen_cli_ids: set[str] = set()
        seen_root_ids: set[str] = set()
        seen_jetski_ids: set[str] = set()
        accounts = state.get("accounts", {})

        def _account_age_key(acc_name: str) -> tuple[str, str]:
            meta = accounts.get(acc_name, {})
            created_at = meta.get("created_at")
            if not created_at:
                adir = account_dir(paths, acc_name)
                try:
                    created_at = datetime.fromtimestamp(adir.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    created_at = "9999-99-99T99:99:99Z"
            return (str(created_at), acc_name)

        for name in sorted(accounts.keys(), key=_account_age_key):
            adir = account_dir(paths, name)
            gdir = adir / ".gemini" if (adir / ".gemini").is_dir() else adir
            cli_file = gdir / "antigravity-cli" / "installation_id"
            root_file = gdir / "installation_id"
            jetski_file = gdir / "antigravity-cli" / "jetski_state.pbtxt"

            existing_cli = read_canonical_uuid_file(cli_file)
            if not existing_cli or existing_cli in seen_cli_ids:
                new_id = write_canonical_uuid_file(cli_file)
                seen_cli_ids.add(new_id)
                actions_taken.append(f"Assigned unique CLI installation_id for '{name}'.")
            else:
                seen_cli_ids.add(existing_cli)

            existing_root = read_canonical_uuid_file(root_file)
            if not existing_root or existing_root in seen_root_ids:
                new_root = write_canonical_uuid_file(root_file)
                seen_root_ids.add(new_root)
                actions_taken.append(f"Assigned unique root installation_id for '{name}'.")
            else:
                seen_root_ids.add(existing_root)

            existing_jetski: str | None = None
            if jetski_file.is_file():
                try:
                    content = jetski_file.read_text(encoding="utf-8")
                    existing_jetski = extract_jetski_uuid(content)
                except OSError:
                    existing_jetski = None

            if not existing_jetski or existing_jetski in seen_jetski_ids:
                new_jid = write_jetski_uuid_file(jetski_file)
                seen_jetski_ids.add(new_jid)
                actions_taken.append(f"Assigned unique jetski installation_uuid for '{name}'.")
            else:
                seen_jetski_ids.add(existing_jetski)

            ensure_account_identity_files(gdir)
            acc_tok = _oauth_token_path(adir)
            if acc_tok.is_file():
                try:
                    acc_tok.chmod(0o600)
                except OSError:
                    pass

        # 5. Synchronize active account identity files to live and runtime
        if active and account_dir(paths, active).exists():
            from antigravity_cli_switcher.manager.accounts import (
                _copy_active_runtime,
                _sync_runtime_to_live_dir,
            )

            _copy_active_runtime(paths, active)
            _sync_runtime_to_live_dir(paths, state)
            actions_taken.append(f"Synchronized active profile '{active}' identity files to runtime and live host.")

        save_state(paths, state)

    return actions_taken

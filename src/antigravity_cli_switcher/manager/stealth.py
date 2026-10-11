"""Stealth architecture, installation ID matching, process termination, and pacing."""

from __future__ import annotations

import os
import re
import signal
import time
import uuid
from pathlib import Path

from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.state import (
    _normalize_switch_runtime,
    parse_timestamp,
    utc_now,
)

STEALTH_SWITCH_COOLDOWN_SECONDS: int = 60
DEFAULT_JETSKI_TEMPLATE: str = (
    "post_onboarding:  {{\n"
    "  completed_steps:  POST_ONBOARDING_STEP_TYPE_MANAGER_WELCOME\n"
    "  completed_steps:  POST_ONBOARDING_STEP_TYPE_USAGE_MODE\n"
    "  completed_steps:  POST_ONBOARDING_STEP_TYPE_AGENT_CONFIGURATION\n"
    "  completed_steps:  POST_ONBOARDING_STEP_TYPE_ADD_WORKSPACE\n"
    "}}\n"
    'installation_uuid:  "{uuid}"\n'
    "migrations:  {{\n  key:  3\n  value:  MIGRATION_STATUS_COMPLETED\n}}\n"
    "migrations:  {{\n  key:  4\n  value:  MIGRATION_STATUS_COMPLETED\n}}\n"
    "migrations:  {{\n  key:  5\n  value:  MIGRATION_STATUS_COMPLETED\n}}\n"
)


def is_valid_uuid_string(val: str) -> bool:
    """Return True if string is an exact 36-char lowercase RFC 4122 UUID v4."""
    if len(val) != 36:
        return False
    try:
        return str(uuid.UUID(val)).lower() == val.lower()
    except (ValueError, AttributeError):
        return False


def write_canonical_uuid_file(target: Path, raw_uuid: str | None = None) -> str:
    """Write canonical 36-byte lowercase RFC 4122 UUID v4 with zero trailing newline."""
    val = str(uuid.UUID(raw_uuid)).lower() if raw_uuid else str(uuid.uuid4()).lower()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(val.encode("ascii"))
    try:
        target.chmod(0o644)
    except OSError:
        pass
    return val


def read_canonical_uuid_file(target: Path) -> str | None:
    """Read a 36-byte canonical UUID file or return None if missing/invalid."""
    if not target.is_file():
        return None
    try:
        raw = target.read_bytes()
        if len(raw) == 36 and is_valid_uuid_string(text := raw.decode("ascii").strip().lower()):
            return text
    except (OSError, UnicodeDecodeError):
        pass
    return None


def extract_jetski_uuid(content: str) -> str | None:
    """Extract installation_uuid from protobuf text content if present and valid."""
    match = re.search(r'installation_uuid:\s*["\']([a-fA-F0-9-]+)["\']', content)
    if match and is_valid_uuid_string(match.group(1)):
        return match.group(1).lower()
    return None


def ensure_account_identity_files(gemini_dir: Path) -> dict[str, str]:
    """Ensure all 3 identity files exist in the profile without corrupting existing IDs."""
    cli_install_id_path = gemini_dir / "antigravity-cli" / "installation_id"
    root_install_id_path = gemini_dir / "installation_id"
    jetski_path = gemini_dir / "antigravity-cli" / "jetski_state.pbtxt"

    existing_cli = read_canonical_uuid_file(cli_install_id_path)
    cli_id = existing_cli or write_canonical_uuid_file(cli_install_id_path)

    existing_root = read_canonical_uuid_file(root_install_id_path)
    root_id = existing_root or write_canonical_uuid_file(root_install_id_path)

    jetski_uuid: str
    if jetski_path.is_file():
        try:
            content = jetski_path.read_text(encoding="utf-8")
            found = extract_jetski_uuid(content)
            if found:
                jetski_uuid = found
            else:
                jetski_uuid = str(uuid.uuid4()).lower()
                if "installation_uuid:" in content:
                    updated = re.sub(
                        r'installation_uuid:\s*["\'][^"\']+["\']',
                        f'installation_uuid:  "{jetski_uuid}"',
                        content,
                    )
                else:
                    updated = content.rstrip() + f'\ninstallation_uuid:  "{jetski_uuid}"\n'
                jetski_path.write_text(updated, encoding="utf-8")
        except OSError:
            jetski_uuid = str(uuid.uuid4()).lower()
            jetski_path.write_text(DEFAULT_JETSKI_TEMPLATE.format(uuid=jetski_uuid), encoding="utf-8")
    else:
        jetski_uuid = str(uuid.uuid4()).lower()
        jetski_path.parent.mkdir(parents=True, exist_ok=True)
        jetski_path.write_text(DEFAULT_JETSKI_TEMPLATE.format(uuid=jetski_uuid), encoding="utf-8")
        try:
            jetski_path.chmod(0o644)
        except OSError:
            pass

    return {"installation_id": cli_id, "root_installation_id": root_id, "jetski_uuid": jetski_uuid}


def backfill_account_identities(paths: ManagerPaths) -> int:
    """Backfill missing identity files across all saved account profiles on startup."""
    if not paths.accounts_dir.is_dir():
        return 0
    count = 0
    for child in paths.accounts_dir.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        gemini_dir = child / ".gemini" if (child / ".gemini").is_dir() else child
        ensure_account_identity_files(gemini_dir)
        count += 1
    return count


def get_ancestor_pids() -> set[int]:
    """Collect all ancestor process IDs up to init (PID 1)."""
    ancestors = {1, os.getpid()}
    curr = os.getppid()
    while curr > 1:
        ancestors.add(curr)
        try:
            stat_path = Path(f"/proc/{curr}/stat")
            if not stat_path.exists():
                break
            content = stat_path.read_text(encoding="utf-8")
            rparen = content.rfind(")")
            if rparen == -1:
                break
            ppid = int(content[rparen + 2 :].split()[1])
            if ppid <= 1 or ppid == curr:
                break
            curr = ppid
        except Exception:
            break
    return ancestors


def is_canonical_agy_binary(pid: int) -> bool:
    """Verify process binary actually resolves to antigravity or agy."""
    try:
        exe_path = Path(f"/proc/{pid}/exe").resolve()
        name = exe_path.name.removesuffix(" (deleted)").strip()
        return name in ("antigravity", "agy")
    except Exception:
        return False


def get_external_agy_processes() -> list[int]:
    """Find standalone agy/antigravity processes excluding ancestor hierarchy and self."""
    ancestors = get_ancestor_pids()
    target_pids: list[int] = []
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return target_pids

    current_uid = os.getuid()
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in ancestors:
            continue
        try:
            stat = entry.stat()
            if stat.st_uid != current_uid:
                continue
            if is_canonical_agy_binary(pid):
                target_pids.append(pid)
        except (PermissionError, FileNotFoundError, OSError):
            continue
    return sorted(target_pids)


def terminate_external_agy_processes() -> list[int]:
    """Safely terminate external standalone agy processes to purge in-memory session headers."""
    pids = get_external_agy_processes()
    if not pids:
        return []

    signaled: set[int] = set()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            signaled.add(pid)
        except (ProcessLookupError, OSError):
            pass

    start = time.time()
    remaining = list(signaled)
    terminated: list[int] = []
    while remaining and (time.time() - start) < 1.5:
        time.sleep(0.05)
        still_alive: list[int] = []
        for pid in remaining:
            try:
                os.kill(pid, 0)
                still_alive.append(pid)
            except PermissionError:
                still_alive.append(pid)
            except (ProcessLookupError, OSError):
                terminated.append(pid)
        remaining = still_alive

    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
            os.kill(pid, 0)
        except PermissionError:
            pass
        except (ProcessLookupError, OSError):
            terminated.append(pid)

    return sorted(list(set(terminated)))


def calculate_cooldown_remaining_from_iso(iso_str: str | None) -> float:
    """Calculate remaining cooldown in seconds from an ISO timestamp."""
    if not iso_str or not isinstance(iso_str, str):
        return 0.0
    dt = parse_timestamp(iso_str)
    return max(0.0, STEALTH_SWITCH_COOLDOWN_SECONDS - (utc_now() - dt).total_seconds()) if dt else 0.0


def get_switch_cooldown_remaining(state: dict) -> float:
    """Return remaining stealth switch cooldown in seconds (0.0 if expired or unset)."""
    runtime = _normalize_switch_runtime(state.get("switch_runtime"))
    raw = runtime.get("last_account_switch_at")
    return calculate_cooldown_remaining_from_iso(str(raw) if isinstance(raw, str) else None)

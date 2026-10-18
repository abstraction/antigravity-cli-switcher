"""Stealth architecture, installation ID matching, process termination, and pacing."""

from __future__ import annotations

import os
import re
import signal
import time
import uuid
from pathlib import Path

from antigravity_cli_switcher.manager.paths import (
    ManagerPaths,
    backup_file_with_rotation,
)
from antigravity_cli_switcher.manager.state import (
    _normalize_switch_runtime,
    get_live_dir,
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
    """Write canonical 36-byte lowercase RFC 4122 UUID v4 atomically with pre-mutation backup."""
    val = str(uuid.UUID(raw_uuid)).lower() if raw_uuid else str(uuid.uuid4()).lower()
    target.parent.mkdir(parents=True, exist_ok=True)
    val_bytes = val.encode("ascii")

    if target.is_file():
        try:
            if target.read_bytes() == val_bytes:
                return val
        except OSError:
            pass
        backup_file_with_rotation(target)

    tmp_path = target.parent / f".tmp_{target.name}_{uuid.uuid4().hex}"
    try:
        tmp_path.write_bytes(val_bytes)
        try:
            tmp_path.chmod(0o644)
        except OSError:
            pass
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return val


def read_canonical_uuid_file(target: Path) -> str | None:
    """Read a canonical UUID file, strip trailing whitespace/newlines, re-save if needed, or return None."""
    if not target.is_file():
        return None
    try:
        raw = target.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace").strip().lower()
        if is_valid_uuid_string(text):
            if raw != text.encode("ascii"):
                write_canonical_uuid_file(target, text)
            return text
    except (OSError, UnicodeDecodeError):
        pass
    return None


def extract_jetski_uuid(content: str) -> str | None:
    """Extract installation_uuid from protobuf text content if present and valid."""
    match = re.search(r'installation_uuid:\s*["\']?\s*([a-fA-F0-9-]{36})\s*["\']?', content)
    if match and is_valid_uuid_string(match.group(1)):
        return match.group(1).lower()
    return None


def write_jetski_uuid_file(jetski_path: Path, raw_uuid: str | None = None) -> str:
    """Write or update installation_uuid in jetski_state.pbtxt atomically with backup."""
    val = str(uuid.UUID(raw_uuid)).lower() if raw_uuid else str(uuid.uuid4()).lower()
    jetski_path.parent.mkdir(parents=True, exist_ok=True)

    if jetski_path.is_file():
        try:
            existing = jetski_path.read_text(encoding="utf-8")
            if extract_jetski_uuid(existing) == val:
                return val
            if "installation_uuid:" in existing:
                content = re.sub(
                    r'installation_uuid:\s*["\']?[^"\'\r\n]*["\']?',
                    f'installation_uuid:  "{val}"',
                    existing,
                )
            else:
                content = existing.rstrip() + f'\ninstallation_uuid:  "{val}"\n'
        except OSError:
            content = DEFAULT_JETSKI_TEMPLATE.format(uuid=val)
        backup_file_with_rotation(jetski_path)
    else:
        content = DEFAULT_JETSKI_TEMPLATE.format(uuid=val)

    tmp_path = jetski_path.parent / f".tmp_{jetski_path.name}_{uuid.uuid4().hex}"
    try:
        tmp_path.write_text(content, encoding="utf-8")
        try:
            tmp_path.chmod(0o644)
        except OSError:
            pass
        os.replace(tmp_path, jetski_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return val


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
                jetski_uuid = write_jetski_uuid_file(jetski_path)
        except OSError:
            jetski_uuid = write_jetski_uuid_file(jetski_path)
    else:
        jetski_uuid = write_jetski_uuid_file(jetski_path)

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


def get_agy_quiescence_remaining(
    state: dict[str, object] | None = None,
    live_dir: Path | None = None,
    paths: ManagerPaths | None = None,
    now: float | None = None,
    running_pids: list[int] | None = None,
) -> float:
    """Calculate remaining quiescence duration required before credentials can be safely committed.

    Checks:
    1. Active agy/antigravity processes (if running, full cooldown is required).
    2. last_process_kill_at timestamp from switch_runtime.
    3. Log, WAL, and summary artifact modification times in live ~/.gemini/antigravity-cli/
       (cli.log, log/cli-*.log, conversation_summaries.db-wal, history.jsonl,
        jetbox_summaries_proto.pb, agyhub_summaries_proto.pb).

    Returns 0.0 if agy has been inactive for >= STEALTH_SWITCH_COOLDOWN_SECONDS (60s).
    """
    current_epoch = now if now is not None else time.time()
    max_remaining: float = 0.0

    # 1. Check running processes
    from antigravity_cli_switcher.manager.keyring import _is_test_environment

    pids: list[int] = []
    if running_pids is not None:
        pids = running_pids
    elif not _is_test_environment() or os.getenv("ACS_TEST_REAL_PROCS") == "1":
        pids = get_external_agy_processes()

    if pids:
        return float(STEALTH_SWITCH_COOLDOWN_SECONDS)

    # 2. Check last_process_kill_at
    if state is not None:
        runtime = _normalize_switch_runtime(state.get("switch_runtime"))
        kill_raw = runtime.get("last_process_kill_at")
        if isinstance(kill_raw, str) and kill_raw.strip():
            kill_dt = parse_timestamp(kill_raw)
            if kill_dt:
                elapsed_kill = max(0.0, current_epoch - kill_dt.timestamp())
                rem_kill = min(
                    float(STEALTH_SWITCH_COOLDOWN_SECONDS),
                    max(0.0, float(STEALTH_SWITCH_COOLDOWN_SECONDS) - elapsed_kill),
                )
                max_remaining = max(max_remaining, rem_kill)

    # 3. Check live directory artifacts
    resolved_live = live_dir
    if resolved_live is None and state is not None:
        resolved_live = get_live_dir(state)
    if resolved_live is None and paths is not None:
        from antigravity_cli_switcher.manager.paths import default_live_dir

        resolved_live = default_live_dir(paths.root)
    if resolved_live is None and (not _is_test_environment() or os.getenv("ACS_TEST_REAL_GEMINI") == "1"):
        from antigravity_cli_switcher.manager.paths import default_live_dir

        resolved_live = default_live_dir()

    if resolved_live and resolved_live.exists():
        agy_dirs = [resolved_live / "antigravity-cli", resolved_live]
        latest_mtime: float = 0.0
        for adir in agy_dirs:
            if not adir.is_dir():
                continue
            cli_log = adir / "cli.log"
            if cli_log.is_file():
                try:
                    latest_mtime = max(latest_mtime, cli_log.stat().st_mtime)
                except OSError:
                    pass
            log_sub = adir / "log"
            if log_sub.is_dir():
                try:
                    for p in log_sub.glob("cli-*.log"):
                        if p.is_file():
                            latest_mtime = max(latest_mtime, p.stat().st_mtime)
                except OSError:
                    pass
            db = adir / "conversation_summaries.db"
            if db.is_file():
                try:
                    latest_mtime = max(latest_mtime, db.stat().st_mtime)
                except OSError:
                    pass
            wal = adir / "conversation_summaries.db-wal"
            if wal.is_file():
                try:
                    latest_mtime = max(latest_mtime, wal.stat().st_mtime)
                except OSError:
                    pass
            hist = adir / "history.jsonl"
            if hist.is_file():
                try:
                    latest_mtime = max(latest_mtime, hist.stat().st_mtime)
                except OSError:
                    pass
            for pb_name in (
                "jetbox_summaries_proto.pb",
                "agyhub_summaries_proto.pb",
                "jetbox_summaries.pb",
                "agyhub_summaries.pb",
            ):
                pb_file = adir / pb_name
                if pb_file.is_file():
                    try:
                        latest_mtime = max(latest_mtime, pb_file.stat().st_mtime)
                    except OSError:
                        pass
            brain_dir = adir / "brain"
            if brain_dir.is_dir():
                try:
                    latest_mtime = max(latest_mtime, brain_dir.stat().st_mtime)
                except OSError:
                    pass
            worktrees_dir = adir / "worktrees"
            if worktrees_dir.is_dir():
                try:
                    latest_mtime = max(latest_mtime, worktrees_dir.stat().st_mtime)
                except OSError:
                    pass

        if latest_mtime > 0.0:
            elapsed_artifact = max(0.0, current_epoch - latest_mtime)
            rem_artifact = min(
                float(STEALTH_SWITCH_COOLDOWN_SECONDS),
                max(0.0, float(STEALTH_SWITCH_COOLDOWN_SECONDS) - elapsed_artifact),
            )
            max_remaining = max(max_remaining, rem_artifact)

    return max_remaining

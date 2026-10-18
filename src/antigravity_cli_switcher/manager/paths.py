from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT_NAME = ".antigravity-cli-switcher"
MANAGED_PROFILE_FILES = (
    "antigravity-cli/antigravity-oauth-token",
    "antigravity-cli/installation_id",
    "installation_id",
    "antigravity-cli/jetski_state.pbtxt",
    "antigravity-cli/cache/default_project_id.txt",
    "antigravity-cli/cache/projects.json",
    "antigravity-cli/conversation_summaries.db",
    "antigravity-cli/jetbox_summaries_proto.pb",
    "antigravity-cli/agyhub_summaries_proto.pb",
    "antigravity-cli/jetbox_summaries.pb",
    "antigravity-cli/agyhub_summaries.pb",
)
LOGIN_ARTIFACT_SETS = (("antigravity-cli/antigravity-oauth-token",),)


@dataclass
class ManagerPaths:
    root: Path
    accounts_dir: Path
    state_file: Path
    runtime_dir: Path
    lock_file: Path


def default_root() -> Path:
    env_root = os.getenv("ACS_ROOT", "").strip() or os.getenv("AGY_MANAGER_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    new_root = Path.home() / ".antigravity-cli-switcher"
    legacy_root = Path.home() / ".agy-cli-manager"
    if not new_root.exists() and legacy_root.exists():
        return legacy_root
    return new_root


def default_live_dir(root: Path | None = None) -> Path:
    env_live_dir = os.getenv("AGY_MANAGER_LIVE_DIR", "").strip()
    if env_live_dir:
        return Path(env_live_dir).expanduser()
    if root is not None:
        try:
            resolved_root = Path(root).resolve()
            if resolved_root != default_root().resolve():
                return resolved_root / ".gemini"
        except Exception:
            pass
    return Path.home() / ".gemini"


def build_paths(root: Path | str | None = None) -> ManagerPaths:
    resolved_root = Path(root).expanduser() if root is not None else default_root()
    return ManagerPaths(
        root=resolved_root,
        accounts_dir=resolved_root / "accounts",
        state_file=resolved_root / "state.json",
        runtime_dir=resolved_root / "runtime",
        lock_file=resolved_root / "manager.lock",
    )


def ensure_layout(paths: ManagerPaths) -> None:
    import antigravity_cli_switcher.manager as manager_pkg

    paths.root.mkdir(parents=True, exist_ok=True)
    paths.accounts_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    if not paths.state_file.exists():
        manager_pkg.save_state(
            paths,
            {
                "active": None,
                "accounts": {},
                "live_dir": str(manager_pkg.default_live_dir(paths.root)),
                "switch_mode": manager_pkg.DEFAULT_SWITCH_MODE,
                "switch_policy": manager_pkg._default_switch_policy(),
                "switch_runtime": manager_pkg._default_switch_runtime(),
                "switch_history": [],
            },
        )
    manager_pkg.backfill_account_identities(paths)


def account_dir(paths: ManagerPaths, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError(f"Invalid and potentially unsafe account name: {name}")

    target_path = paths.accounts_dir / name

    if target_path.is_symlink():
        raise ValueError(f"Account directory {name} is a symlink, which is not allowed.")

    return target_path


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def resolve_runtime_home(live_dir: Path | None = None) -> Path:
    if live_dir is not None:
        resolved = live_dir.resolve()
        if resolved.name == ".gemini":
            return resolved.parent
        return resolved
    return Path.home()


def _oauth_token_path(home_root: Path) -> Path:
    return home_root / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def _project_id_path(home_root: Path) -> Path:
    return home_root / ".gemini" / "antigravity-cli" / "cache" / "default_project_id.txt"


def _resolve_profile_source(source_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    gemini_dir = source_dir / ".gemini"
    if gemini_dir.is_dir():
        return gemini_dir
    return source_dir


def _resolve_home_source(source_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    if (source_dir / ".gemini").is_dir():
        return source_dir
    if source_dir.name == ".gemini":
        return source_dir.parent
    return source_dir


def _read_json_if_exists(path: Path) -> dict | list | None:
    import json

    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_text_if_exists(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def resolve_agy_binary(agy_binary: str | None = None) -> str:
    if agy_binary and agy_binary.strip():
        return agy_binary.strip()

    env_binary = os.getenv("AGY_BINARY", "").strip()
    if env_binary:
        return env_binary

    path_binary = shutil.which("agy")
    if path_binary:
        return path_binary

    install_sibling = Path(__file__).resolve().parents[4] / "agy"
    if install_sibling.is_file() and os.access(install_sibling, os.X_OK):
        return str(install_sibling)

    raise ValueError("agy binary not found. Use --agy-binary, set AGY_BINARY, or put `agy` in PATH.")


def prune_file_backups(target: Path, max_backups: int = 5) -> list[Path]:
    """Prune backups of target in target.parent, retaining at most max_backups."""
    parent = target.parent
    if not parent.is_dir():
        return []
    prefix = f"{target.name}.bak"
    backups: list[Path] = []
    for item in parent.iterdir():
        if not item.is_file():
            continue
        if item.name == prefix or item.name.startswith(f"{prefix}."):
            backups.append(item)

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    backups.sort(key=lambda p: (_safe_mtime(p), p.name))
    removed: list[Path] = []
    while len(backups) > max(0, max_backups):
        oldest = backups.pop(0)
        try:
            oldest.unlink(missing_ok=True)
            removed.append(oldest)
        except OSError:
            pass
    return removed


def backup_file_with_rotation(target: Path, max_backups: int = 5) -> Path | None:
    """Create a timestamped backup of target if it exists and is non-empty, and prune old backups."""
    if not target.is_file():
        return None
    try:
        if target.stat().st_size == 0:
            return None
    except OSError:
        return None

    now_ts = time.time()
    bak_path = target.parent / f"{target.name}.bak.{int(now_ts)}"
    if bak_path.exists():
        bak_path = target.parent / f"{target.name}.bak.{int(now_ts * 1000)}"
    if bak_path.exists():
        bak_path = target.parent / f"{target.name}.bak.{time.time_ns()}"
    counter = 1
    while bak_path.exists():
        bak_path = target.parent / f"{target.name}.bak.{time.time_ns()}_{counter}"
        counter += 1

    try:
        shutil.copy2(target, bak_path)
    except OSError:
        return None
    prune_file_backups(target, max_backups=max_backups)
    return bak_path


def get_acs_id_path(acc_dir: Path) -> Path:
    """Return path to .acs_id tag file directly in the account directory."""
    return acc_dir / ".acs_id"


def read_acs_id(acc_dir: Path) -> str | None:
    """Read account UUID from .acs_id file or return None if missing or empty."""
    p = get_acs_id_path(acc_dir)
    if not p.is_file():
        return None
    try:
        val = p.read_text(encoding="utf-8").strip()
        return val or None
    except OSError:
        return None


def write_acs_id(acc_dir: Path, acs_id: str | None = None) -> str:
    """Write account UUID to .acs_id file atomically."""
    acc_dir.mkdir(parents=True, exist_ok=True)
    val = acs_id.strip() if (acs_id and acs_id.strip()) else str(uuid.uuid4()).lower()
    p = get_acs_id_path(acc_dir)
    tmp_path = acc_dir / f".tmp_acs_id_{uuid.uuid4().hex}"
    try:
        tmp_path.write_text(val, encoding="utf-8")
        try:
            tmp_path.chmod(0o644)
        except OSError:
            pass
        os.replace(tmp_path, p)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return val


def _checkpoint_sqlite_wal(db_path: Path) -> None:
    """Checkpoint and truncate SQLite WAL file to ensure pristine state before copy."""
    try:
        import sqlite3

        con = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            con.close()
    except Exception:
        try:
            import subprocess

            subprocess.run(
                ["sqlite3", str(db_path), "PRAGMA wal_checkpoint(TRUNCATE);"],
                capture_output=True,
                check=False,
                timeout=2,
            )
        except Exception:
            pass


def _copy_managed_profile_files(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_PROFILE_FILES:
        src = source / name
        dst = target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            if src.name.endswith(".db"):
                _checkpoint_sqlite_wal(src)
                src_wal = src.with_name(f"{src.name}-wal")
                dst_wal = dst.with_name(f"{dst.name}-wal")
                dst_shm = dst.with_name(f"{dst.name}-shm")
                dst_shm.unlink(missing_ok=True)
                if src_wal.is_file() and src_wal.stat().st_size > 0:
                    shutil.copy2(src_wal, dst_wal)
                else:
                    dst_wal.unlink(missing_ok=True)
            if dst.name in ("installation_id", "jetski_state.pbtxt") and dst.is_file() and dst.stat().st_size > 0:
                try:
                    if dst.read_bytes() != src.read_bytes():
                        backup_file_with_rotation(dst)
                except OSError:
                    backup_file_with_rotation(dst)
            shutil.copy2(src, dst)
            if dst.name == "antigravity-oauth-token":
                try:
                    os.chmod(dst, 0o600)
                except OSError:
                    pass
        else:
            dst.unlink(missing_ok=True)
            if dst.name.endswith(".db"):
                dst.with_name(f"{dst.name}-wal").unlink(missing_ok=True)
                dst.with_name(f"{dst.name}-shm").unlink(missing_ok=True)


def _remove_managed_profile_files(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_PROFILE_FILES:
        (target / name).unlink(missing_ok=True)
        if name.endswith(".db"):
            (target / f"{name}-wal").unlink(missing_ok=True)
            (target / f"{name}-shm").unlink(missing_ok=True)

    for cache_name in ("cache/default_project_id.txt", "cache/projects.json"):
        (target / cache_name).unlink(missing_ok=True)

    for legacy_name in ("google_accounts.json", "google_account_id", "oauth_creds.json"):
        (target / legacy_name).unlink(missing_ok=True)


def _copy_account_profile(source_dir: Path, target_home: Path) -> None:
    profile_source = _resolve_profile_source(source_dir)
    target_profile = target_home / ".gemini"
    _copy_managed_profile_files(profile_source, target_profile)

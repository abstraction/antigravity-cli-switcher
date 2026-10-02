from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT_NAME = ".antigravity-cli-switcher"
MANAGED_PROFILE_FILES = ("antigravity-cli/antigravity-oauth-token",)
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


def _copy_managed_profile_files(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_PROFILE_FILES:
        src = source / name
        dst = target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copy2(src, dst)
        else:
            dst.unlink(missing_ok=True)


def _remove_managed_profile_files(target: Path) -> None:
    import antigravity_cli_switcher.manager as manager_pkg

    target.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_PROFILE_FILES:
        (target / name).unlink(missing_ok=True)

    for legacy_name in ("google_accounts.json", "google_account_id", "oauth_creds.json"):
        (target / legacy_name).unlink(missing_ok=True)
    manager_pkg._clear_keyring_token()


def _copy_account_profile(source_dir: Path, target_home: Path) -> None:
    profile_source = _resolve_profile_source(source_dir)
    target_profile = target_home / ".gemini"
    _copy_managed_profile_files(profile_source, target_profile)

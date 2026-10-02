"""Migration tool for transitioning from agy-cli-manager to antigravity-cli-switcher.

Handles:
- Complete migration of all account directories, including all hidden dotfiles/dot-directories (.gemini, .migrated, .cache, etc.)
- Atomic rename or recursive copy preserving exact file permissions and timestamps
- Creation of backward-compatibility symlink (~/.agy-cli-manager -> ~/.antigravity-cli-switcher)
- Detection and safe updating of shell aliases (e.g. in ~/.bashrc)
- Detection of legacy running processes and uv tool installations
- Non-destructive backup creation prior to migration
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LEGACY_ROOT = Path.home() / ".agy-cli-manager"
DEFAULT_TARGET_ROOT = Path.home() / ".antigravity-cli-switcher"


@dataclass
class AccountScan:
    name: str
    path: str
    has_gemini: bool
    has_token: bool
    dotfile_count: int
    total_files: int


@dataclass
class MigrationScan:
    legacy_root: str
    target_root: str
    legacy_exists: bool
    legacy_is_symlink: bool
    target_exists: bool
    target_is_symlink: bool
    target_points_to_legacy: bool
    legacy_points_to_target: bool
    already_migrated: bool
    accounts: list[AccountScan] = field(default_factory=list)
    has_state_file: bool = False
    has_runtime_dir: bool = False
    has_logs_dir: bool = False
    total_files: int = 0
    total_dotfiles: int = 0
    total_bytes: int = 0
    running_processes: list[dict[str, object]] = field(default_factory=list)
    shell_aliases: list[dict[str, object]] = field(default_factory=list)
    legacy_uv_tool_installed: bool = False


@dataclass
class MigrationResult:
    success: bool
    message: str
    backup_path: str | None = None
    migrated_accounts: int = 0
    total_files_migrated: int = 0
    total_dotfiles_migrated: int = 0
    symlink_created: bool = False
    shell_updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def count_files_and_dotfiles(path: Path) -> tuple[int, int, int]:
    """Recursively count total files, dotfiles/directories, and total bytes.

    Dotfiles include any file or directory whose basename starts with '.'.
    """
    total_files = 0
    total_dotfiles = 0
    total_bytes = 0

    if not path.exists():
        return 0, 0, 0

    for root, dirs, files in os.walk(path, followlinks=False):
        for d in dirs:
            if d.startswith("."):
                total_dotfiles += 1
        for f in files:
            total_files += 1
            if f.startswith("."):
                total_dotfiles += 1
            fp = Path(root) / f
            try:
                if not fp.is_symlink():
                    total_bytes += fp.stat().st_size
            except OSError:
                pass

    return total_files, total_dotfiles, total_bytes


def detect_running_legacy_processes() -> list[dict[str, object]]:
    """Detect running processes running legacy agy-cli-manager or acm."""
    results: list[dict[str, object]] = []
    current_pid = os.getpid()

    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return results

    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == current_pid:
            continue
        cmdline_file = entry / "cmdline"
        try:
            with open(cmdline_file, "rb") as f:
                raw = f.read()
            cmdline = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
            if not cmdline:
                continue
            lower = cmdline.lower()
            if "agy-cli-manager" in lower or "/acm" in lower or "agy_cli_manager" in lower:
                results.append({"pid": pid, "cmdline": cmdline})
        except (OSError, PermissionError):
            continue

    return results


def detect_shell_aliases() -> list[dict[str, object]]:
    """Scan common shell configuration files for references to agy-cli-manager or acm."""
    candidates = [
        Path.home() / ".bashrc",
        Path.home() / ".bash_profile",
        Path.home() / ".profile",
        Path.home() / ".zshrc",
        Path.home() / ".zshenv",
    ]
    found: list[dict[str, object]] = []
    pattern = re.compile(r"^\s*alias\s+acm=.*", re.MULTILINE)
    generic_pattern = re.compile(r"agy-cli-manager", re.IGNORECASE)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern.match(line) or (generic_pattern.search(line) and line.strip().startswith("alias")):
                    found.append(
                        {
                            "file": str(candidate),
                            "line_number": idx,
                            "line": line.strip(),
                        }
                    )
        except OSError:
            pass

    return found


def detect_legacy_uv_tool() -> bool:
    """Check if agy-cli-manager is installed in uv tool list."""
    try:
        proc = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0:
            return "agy-cli-manager" in proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    legacy_tool_dir = Path.home() / ".local/share/uv/tools/agy-cli-manager"
    return legacy_tool_dir.exists()


def scan_migration(
    legacy_root: Path = DEFAULT_LEGACY_ROOT,
    target_root: Path = DEFAULT_TARGET_ROOT,
) -> MigrationScan:
    """Analyze both directories and report the current migration status."""
    legacy_is_symlink = legacy_root.is_symlink()
    legacy_exists = legacy_root.exists() or legacy_is_symlink

    target_is_symlink = target_root.is_symlink()
    target_exists = target_root.exists() or target_is_symlink

    target_points_to_legacy = False
    if target_is_symlink:
        try:
            target_points_to_legacy = target_root.resolve() == legacy_root.resolve()
        except OSError:
            pass

    legacy_points_to_target = False
    if legacy_is_symlink:
        try:
            legacy_points_to_target = legacy_root.resolve() == target_root.resolve()
        except OSError:
            pass

    # Already migrated if target is a real directory and legacy is a symlink pointing to it
    already_migrated = False
    if target_exists and not target_is_symlink:
        if (legacy_is_symlink and legacy_points_to_target) or not legacy_exists:
            already_migrated = True

    active_dir = target_root if already_migrated else legacy_root

    # Scan accounts
    accounts: list[AccountScan] = []
    accounts_dir = active_dir / "accounts"
    if accounts_dir.is_dir():
        for entry in sorted(accounts_dir.iterdir()):
            if entry.is_dir():
                gemini_dir = entry / ".gemini"
                token_file = gemini_dir / "antigravity-cli" / "antigravity-oauth-token"
                total_f, dot_f, _ = count_files_and_dotfiles(entry)
                accounts.append(
                    AccountScan(
                        name=entry.name,
                        path=str(entry),
                        has_gemini=gemini_dir.is_dir(),
                        has_token=token_file.is_file(),
                        dotfile_count=dot_f,
                        total_files=total_f,
                    )
                )

    total_files, total_dotfiles, total_bytes = count_files_and_dotfiles(active_dir)

    return MigrationScan(
        legacy_root=str(legacy_root),
        target_root=str(target_root),
        legacy_exists=legacy_exists,
        legacy_is_symlink=legacy_is_symlink,
        target_exists=target_exists,
        target_is_symlink=target_is_symlink,
        target_points_to_legacy=target_points_to_legacy,
        legacy_points_to_target=legacy_points_to_target,
        already_migrated=already_migrated,
        accounts=accounts,
        has_state_file=(active_dir / "state.json").is_file(),
        has_runtime_dir=(active_dir / "runtime").is_dir(),
        has_logs_dir=(active_dir / "logs").is_dir(),
        total_files=total_files,
        total_dotfiles=total_dotfiles,
        total_bytes=total_bytes,
        running_processes=detect_running_legacy_processes(),
        shell_aliases=detect_shell_aliases(),
        legacy_uv_tool_installed=detect_legacy_uv_tool(),
    )


def execute_migration(
    scan: MigrationScan,
    dry_run: bool = False,
    backup: bool = True,
    update_shell: bool = False,
) -> MigrationResult:
    """Execute migration from legacy ~/.agy-cli-manager to ~/.antigravity-cli-switcher.

    Guarantees:
    - Dotfiles and hidden files/directories (.gemini, .migrated, .cache) are fully preserved.
    - An atomic rename is attempted first; falls back to copy2 tree with symlink preservation.
    - Pre-migration backup is created before modifying state (unless backup=False).
    - Backward-compatibility symlink (legacy -> target) is created.
    """
    legacy_path = Path(scan.legacy_root)
    target_path = Path(scan.target_root)

    if scan.already_migrated:
        return MigrationResult(
            success=True,
            message="Already migrated: target directory is active and legacy path is symlinked.",
            migrated_accounts=len(scan.accounts),
            total_files_migrated=scan.total_files,
            total_dotfiles_migrated=scan.total_dotfiles,
            symlink_created=scan.legacy_is_symlink,
        )

    if not scan.legacy_exists:
        return MigrationResult(
            success=False,
            message=f"Legacy directory '{legacy_path}' does not exist. Nothing to migrate.",
            errors=[f"Directory '{legacy_path}' not found."],
        )

    if scan.target_exists and not scan.target_is_symlink:
        return MigrationResult(
            success=False,
            message=f"Target directory '{target_path}' already exists as a non-symlink directory. Refusing to overwrite.",
            errors=[f"Target '{target_path}' already exists."],
        )

    if dry_run:
        return MigrationResult(
            success=True,
            message="Dry-run completed successfully. No filesystem changes were made.",
            migrated_accounts=len(scan.accounts),
            total_files_migrated=scan.total_files,
            total_dotfiles_migrated=scan.total_dotfiles,
            symlink_created=True,
        )

    # 1. Pre-migration backup
    backup_path: Path | None = None
    if backup:
        timestamp = int(time.time())
        backup_path = legacy_path.parent / f"{legacy_path.name}.backup.{timestamp}"
        try:
            # Try btrfs/reflink fast copy first
            proc = subprocess.run(
                ["cp", "--reflink=auto", "-a", str(legacy_path), str(backup_path)],
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                shutil.copytree(legacy_path, backup_path, symlinks=True, copy_function=shutil.copy2)
        except Exception as e:
            return MigrationResult(
                success=False,
                message=f"Failed to create pre-migration backup: {e}",
                errors=[str(e)],
            )

    # 2. Remove target symlink if it currently points to legacy
    if scan.target_is_symlink:
        try:
            target_path.unlink()
        except OSError as e:
            return MigrationResult(
                success=False,
                message=f"Failed to remove temporary symlink '{target_path}': {e}",
                errors=[str(e)],
                backup_path=str(backup_path) if backup_path else None,
            )

    # 3. Move/migrate legacy directory to target directory
    try:
        # On same filesystem, os.rename is an atomic inode operation that preserves all dotfiles instantly
        os.rename(legacy_path, target_path)
    except OSError:
        # Cross-filesystem fallback
        try:
            shutil.copytree(legacy_path, target_path, symlinks=True, copy_function=shutil.copy2)
            shutil.rmtree(legacy_path)
        except Exception as e:
            return MigrationResult(
                success=False,
                message=f"Failed to move directory '{legacy_path}' to '{target_path}': {e}",
                errors=[str(e)],
                backup_path=str(backup_path) if backup_path else None,
            )

    # 4. Create backward-compatibility symlink: legacy_path -> target_path
    symlink_ok = False
    try:
        legacy_path.symlink_to(target_path)
        symlink_ok = True
    except OSError:
        # Non-fatal warning if symlink creation fails
        pass

    # 5. Verify target state
    state_file = target_path / "state.json"
    accounts_dir = target_path / "accounts"
    if not state_file.is_file() or not accounts_dir.is_dir():
        return MigrationResult(
            success=False,
            message="Migration finished but verification failed: state.json or accounts/ missing.",
            errors=["Missing state.json or accounts/ after move."],
            backup_path=str(backup_path) if backup_path else None,
        )

    # 6. Update shell aliases if requested
    shell_updates: list[str] = []
    if update_shell and scan.shell_aliases:
        for alias_entry in scan.shell_aliases:
            alias_file = alias_entry["file"]
            if not isinstance(alias_file, str):
                continue
            shell_file = Path(alias_file)
            try:
                content = shell_file.read_text(encoding="utf-8")
                # Replace alias acm='agy-cli-manager' with alias acs='antigravity-cli-switcher'
                new_content = re.sub(
                    r"alias\s+acm=['\"]?agy-cli-manager['\"]?",
                    "alias acs='antigravity-cli-switcher'",
                    content,
                )
                if new_content != content:
                    # Backup shell file
                    bak = shell_file.parent / f"{shell_file.name}.bak.{int(time.time())}"
                    bak.write_text(content, encoding="utf-8")
                    shell_file.write_text(new_content, encoding="utf-8")
                    shell_updates.append(f"{shell_file} (backed up to {bak.name})")
            except OSError:
                pass

    return MigrationResult(
        success=True,
        message="Migration completed successfully.",
        backup_path=str(backup_path) if backup_path else None,
        migrated_accounts=len(scan.accounts),
        total_files_migrated=scan.total_files,
        total_dotfiles_migrated=scan.total_dotfiles,
        symlink_created=symlink_ok,
        shell_updated=shell_updates,
    )

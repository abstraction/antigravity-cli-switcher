"""Profile identity probing via active usage and artifact verification."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from antigravity_cli_switcher.manager.keyring import (
    _clear_keyring_token,
    _load_keyring_token,
    _save_keyring_token,
    _sync_home_to_keyring,
    _sync_keyring_to_home,
)
from antigravity_cli_switcher.manager.paths import (
    LOGIN_ARTIFACT_SETS,
    _copy_account_profile,
    _resolve_home_source,
    _resolve_profile_source,
    resolve_agy_binary,
    resolve_runtime_home,
)

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def profile_has_login_artifacts(profile_dir: Path) -> bool:
    """Check if profile directory contains required auth artifact files."""
    if not profile_dir.is_dir():
        return False
    for artifact_set in LOGIN_ARTIFACT_SETS:
        if all((profile_dir / name).is_file() for name in artifact_set):
            return True
    return False


def probe_profile_identity_via_usage(
    source_dir: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
    live_dir: Path | None = None,
) -> dict[str, str | None]:
    """Run non-mutating agy /usage task against account home to probe active identity hints."""
    resolved_binary = resolve_agy_binary(agy_binary)
    source_home = _resolve_home_source(source_dir)
    profile_source = _resolve_profile_source(source_dir)
    if not profile_has_login_artifacts(profile_source):
        raise ValueError(f"Profile source is missing required auth files: {profile_source}")
    runtime_home = resolve_runtime_home(live_dir)

    with tempfile.TemporaryDirectory(prefix="agy-usage-restore-") as restore_root_str:
        restore_root = Path(restore_root_str)
        restore_home = restore_root / "home"
        _copy_account_profile(runtime_home, restore_home)
        restore_keyring = _load_keyring_token()
        try:
            _copy_account_profile(source_home, runtime_home)
            _sync_home_to_keyring(source_home)

            env = os.environ.copy()
            env["HOME"] = str(runtime_home)
            env["PATH"] = env.get("PATH", "/bin:/usr/bin:/usr/local/bin")

            proc = subprocess.run(
                [resolved_binary, "-p", "/usage"],
                cwd=runtime_home,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
            if proc.returncode != 0:
                tail = "\n".join(output.splitlines()[-8:]) if output else "no output"
                raise ValueError(f"agy /usage failed with exit code {proc.returncode}: {tail}")

            _sync_keyring_to_home(source_home)
            match = EMAIL_PATTERN.search(output)
            if match:
                return {
                    "account_name": match.group(0),
                    "source": "agy:/usage",
                }
            return {
                "account_name": None,
                "source": "agy:/usage",
                "raw_hint": "\n".join(output.splitlines()[:8]),
            }
        finally:
            _copy_account_profile(restore_home, runtime_home)
            if restore_keyring:
                _save_keyring_token(restore_keyring)
            else:
                _clear_keyring_token()


def resolve_login_profile_identity(
    source_dir: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
    live_dir: Path | None = None,
) -> dict[str, str | None]:
    """Resolve identity hints after a login, probing via usage if needed."""
    from antigravity_cli_switcher.manager.identity import detect_profile_identity

    detected = detect_profile_identity(source_dir, live_dir=live_dir)
    if detected.get("account_name"):
        return detected
    try:
        probed = probe_profile_identity_via_usage(
            source_dir,
            agy_binary=agy_binary,
            timeout_seconds=timeout_seconds,
            live_dir=live_dir,
        )
        if probed.get("account_name"):
            return probed
    except Exception:
        pass
    return detected

"""Profile identity probing via active usage and artifact verification."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from antigravity_cli_switcher.manager.keyring import _isolated_keyring_warmup
from antigravity_cli_switcher.manager.paths import (
    LOGIN_ARTIFACT_SETS,
    _copy_account_profile,
    _resolve_home_source,
    _resolve_profile_source,
    resolve_agy_binary,
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

    with tempfile.TemporaryDirectory(prefix="agy-usage-probe-") as temp_dir:
        probe_home = Path(temp_dir)
        _copy_account_profile(source_home, probe_home)

        with _isolated_keyring_warmup(probe_home):
            env = os.environ.copy()
            env["HOME"] = str(probe_home)
            env["PATH"] = env.get("PATH", "/bin:/usr/bin:/usr/local/bin")

            proc = subprocess.run(
                [resolved_binary, "-p", "/usage"],
                cwd=probe_home,
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

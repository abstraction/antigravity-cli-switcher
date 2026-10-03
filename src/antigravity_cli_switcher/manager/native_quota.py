from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from antigravity_cli_switcher.manager.keyring import _isolated_keyring_warmup
from antigravity_cli_switcher.manager.paths import resolve_agy_binary
from antigravity_cli_switcher.manager.verification import is_ineligible_error


def _fetch_native_quota(
    source_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> dict:
    resolved_binary = resolve_agy_binary(agy_binary)
    env = os.environ.copy()
    env["HOME"] = str(source_home)
    env["PATH"] = env.get("PATH", "/bin:/usr/bin:/usr/local/bin")
    env["BROWSER"] = "false"
    env["DISPLAY"] = ""
    env["WAYLAND_DISPLAY"] = ""

    with _isolated_keyring_warmup(source_home):
        try:
            proc = subprocess.run(
                [resolved_binary, "-p", "/usage", "--output-format", "json"],
                cwd=source_home,
                env=env,
                capture_output=True,
                text=True,
                timeout=max(10, timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("agy /usage timed out.") from None

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            detail = stderr or stdout or f"exit code {proc.returncode}"
            if is_ineligible_error(detail):
                raise ValueError(f"Eligibility check failed: {detail[:500]}")
            raise ValueError(f"agy /usage failed: {detail[:500]}")

        raw_stdout = proc.stdout.strip()
        if not raw_stdout:
            raise ValueError("agy /usage returned empty output.")
        try:
            payload = json.loads(raw_stdout)
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected JSON output from agy /usage: {type(payload)}")
            return payload
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse agy /usage JSON: {exc} | output: {raw_stdout[:200]}") from exc


__all__ = [
    "_fetch_native_quota",
]

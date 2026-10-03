from __future__ import annotations

import os
import subprocess
from pathlib import Path

from antigravity_cli_switcher.manager.cloudcode import (
    CODE_ASSIST_LOAD_PATH,
    _cloudcode_request,
    _parse_model_label,
)
from antigravity_cli_switcher.manager.identity import check_token_account_match
from antigravity_cli_switcher.manager.keyring import _isolated_keyring_warmup
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths, resolve_agy_binary
from antigravity_cli_switcher.manager.state import load_state, sync_state_from_disk


def _run_agy_models_command(
    runtime_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> list[dict]:
    resolved_binary = resolve_agy_binary(agy_binary)
    env = os.environ.copy()
    env["HOME"] = str(runtime_home)
    env["PATH"] = env.get("PATH", "/bin:/usr/bin:/usr/local/bin")
    proc = subprocess.run(
        [resolved_binary, "models"],
        cwd=runtime_home,
        env=env,
        capture_output=True,
        text=True,
        timeout=max(10, timeout_seconds),
        check=False,
    )
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-8:]) if output else "no output"
        raise ValueError(f"agy models failed with exit code {proc.returncode}: {tail}")
    models: list[dict] = []
    for line in output.splitlines():
        parsed = _parse_model_label(line)
        if parsed:
            models.append(parsed)
    if not models:
        raise ValueError("agy models returned no usable model entries.")
    return models


def list_models(
    paths: ManagerPaths,
    name: str | None = None,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> dict:
    from antigravity_cli_switcher.manager.quota import (
        _ensure_fresh_access_token,
        _resolve_usage_refresh_target,
    )

    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        account_name, source_home = _resolve_usage_refresh_target(paths, state, name)

    match_result = check_token_account_match(paths, account_name, source_home, state=state)
    if match_result.status == "mismatch":
        raise ValueError(
            f"Token mismatch for account '{account_name}': expected {match_result.expected_email}, "
            f"but token belongs to {match_result.token_email}. Run 'acs login {account_name}' to fix."
        )

    models: list[dict] = []
    try:
        access_token = _ensure_fresh_access_token(source_home, agy_binary=agy_binary, timeout_seconds=timeout_seconds)
        load_response = _cloudcode_request(access_token, CODE_ASSIST_LOAD_PATH, {})
        raw_models = load_response.get("models")
        if isinstance(raw_models, list):
            parsed_models: list[dict] = []
            for item in raw_models:
                if isinstance(item, str):
                    parsed = _parse_model_label(item)
                    if parsed:
                        parsed_models.append(parsed)
            if parsed_models:
                models = parsed_models
    except Exception:
        pass

    if not models:
        with _isolated_keyring_warmup(source_home):
            models = _run_agy_models_command(source_home, agy_binary=agy_binary, timeout_seconds=timeout_seconds)

    return {
        "account": account_name,
        "source_home": str(source_home),
        "models": models,
        "count": len(models),
    }


__all__ = [
    "_run_agy_models_command",
    "list_models",
]

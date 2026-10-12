from __future__ import annotations

import os
import subprocess
from pathlib import Path

from antigravity_cli_switcher.manager.cloudcode import (
    CODE_ASSIST_MODELS_PATH,
    _cloudcode_request,
    _parse_model_label,
)
from antigravity_cli_switcher.manager.identity import check_token_account_match
from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths, resolve_agy_binary
from antigravity_cli_switcher.manager.state import load_state, sync_state_from_disk


def _parse_raw_models(raw_models: object) -> list[dict[str, object]]:
    parsed_models: list[dict[str, object]] = []
    if isinstance(raw_models, dict):
        for model_id, model_info in raw_models.items():
            label = ""
            if isinstance(model_info, dict):
                disp = (
                    model_info.get("displayName")
                    or model_info.get("display_name")
                    or model_info.get("name")
                    or model_info.get("modelId")
                    or model_info.get("id")
                )
                if isinstance(disp, str) and disp.strip():
                    label = disp.strip()
            elif isinstance(model_info, str) and model_info.strip():
                label = model_info.strip()
            if not label and isinstance(model_id, str):
                label = model_id.strip()
            if label:
                parsed = _parse_model_label(label)
                if parsed:
                    parsed_models.append(parsed)
                else:
                    parsed_models.append({"name": label, "provider": None, "family": None, "variant": None})
    elif isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, str) and item.strip():
                parsed = _parse_model_label(item)
                if parsed:
                    parsed_models.append(parsed)
            elif isinstance(item, dict):
                disp = (
                    item.get("displayName")
                    or item.get("display_name")
                    or item.get("name")
                    or item.get("modelId")
                    or item.get("id")
                )
                if isinstance(disp, str) and disp.strip():
                    parsed = _parse_model_label(disp)
                    if parsed:
                        parsed_models.append(parsed)
                    else:
                        parsed_models.append({"name": disp.strip(), "provider": None, "family": None, "variant": None})
    return parsed_models


def _run_agy_models_command(
    runtime_home: Path,
    agy_binary: str | None = None,
    timeout_seconds: int = 30,
) -> list[dict[str, object]]:
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
    models: list[dict[str, object]] = []
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
) -> dict[str, object]:
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

    models: list[dict[str, object]] = []
    account_data = state.get("accounts", {}).get(account_name, {}) if isinstance(state.get("accounts"), dict) else {}
    proxy_url: str | None = None
    if isinstance(account_data, dict):
        from antigravity_cli_switcher.manager.state import _normalize_proxy_config

        proxy_cfg = _normalize_proxy_config(account_data.get("proxy"))
        if proxy_cfg.get("enabled") and proxy_cfg.get("url"):
            proxy_url = str(proxy_cfg["url"])

    try:
        access_token = _ensure_fresh_access_token(
            source_home, agy_binary=agy_binary, timeout_seconds=timeout_seconds, proxy_url=proxy_url
        )
        cand = account_data.get("project_id") if isinstance(account_data, dict) else None
        project_id = str(cand).strip() if isinstance(cand, str) and cand.strip() else None
        if not project_id:
            account_cache = source_home / ".gemini" / "antigravity-cli" / "cache" / "default_project_id.txt"
            if not account_cache.is_file():
                account_cache = source_home / "antigravity-cli" / "cache" / "default_project_id.txt"
            if account_cache.is_file():
                try:
                    val = account_cache.read_text(encoding="utf-8").strip()
                    if val:
                        project_id = val
                except OSError:
                    pass
        payload: dict[str, object] = {"project": project_id} if project_id else {}
        if proxy_url:
            load_response = _cloudcode_request(access_token, CODE_ASSIST_MODELS_PATH, payload, proxy_url=proxy_url)
        else:
            load_response = _cloudcode_request(access_token, CODE_ASSIST_MODELS_PATH, payload)
        raw_models = load_response.get("models")
        if raw_models is None and isinstance(load_response.get("response"), dict):
            raw_models = load_response["response"].get("models")
        if raw_models is None and isinstance(load_response.get("data"), dict):
            raw_models = load_response["data"].get("models")
        if raw_models is None and isinstance(load_response.get("command"), dict):
            cmd_data = load_response["command"].get("data")
            if isinstance(cmd_data, dict):
                raw_models = cmd_data.get("models")
        parsed_models = _parse_raw_models(raw_models)
        if parsed_models:
            models = parsed_models
    except Exception:
        pass

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

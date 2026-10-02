from __future__ import annotations

from antigravity_cli_switcher.manager.locking import manager_lock
from antigravity_cli_switcher.manager.paths import ManagerPaths
from antigravity_cli_switcher.manager.state import (
    _default_proxy_config,
    _normalize_proxy_config,
    load_state,
    save_state,
    sync_state_from_disk,
)


def get_account_proxy(paths: ManagerPaths, name: str | None = None) -> tuple[str, dict]:
    state = sync_state_from_disk(paths, load_state(paths))
    resolved_name = name or state.get("active")
    if not resolved_name:
        raise ValueError("No active account.")
    meta = state["accounts"].get(resolved_name)
    if meta is None:
        raise ValueError(f"Unknown account: {resolved_name}")
    return resolved_name, _normalize_proxy_config(meta.get("proxy"))


def list_account_proxies(paths: ManagerPaths) -> dict:
    state = sync_state_from_disk(paths, load_state(paths))
    accounts = {}
    for name, meta in sorted(state["accounts"].items()):
        accounts[name] = {
            "active": name == state.get("active"),
            "status": meta.get("status", "standby"),
            "enabled": bool(meta.get("enabled", True)),
            "proxy": _normalize_proxy_config(meta.get("proxy")),
        }
    return {
        "active": state.get("active"),
        "accounts": accounts,
    }


def set_account_proxy(
    paths: ManagerPaths,
    name: str,
    *,
    url: str,
    label: str | None = None,
    enabled: bool = True,
) -> dict:
    proxy_url = str(url).strip()
    if not proxy_url:
        raise ValueError("Proxy URL cannot be empty.")
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        meta = state["accounts"].get(name)
        if meta is None:
            raise ValueError(f"Unknown account: {name}")
        meta["proxy"] = _normalize_proxy_config(
            {
                "enabled": enabled,
                "url": proxy_url,
                "label": label,
            }
        )
        save_state(paths, state)
        return dict(meta["proxy"])


def clear_account_proxy(paths: ManagerPaths, name: str) -> None:
    with manager_lock(paths):
        state = sync_state_from_disk(paths, load_state(paths))
        meta = state["accounts"].get(name)
        if meta is None:
            raise ValueError(f"Unknown account: {name}")
        meta["proxy"] = _default_proxy_config()
        save_state(paths, state)

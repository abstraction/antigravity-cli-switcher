"""Background workers and tasks for ACS Textual application."""

from __future__ import annotations

import socket
import time
from urllib.parse import urlsplit

from antigravity_cli_switcher.manager import (
    ManagerPaths,
    UsageRefreshResult,
    get_status_snapshot,
    refresh_account_usage,
    verify_accounts,
)
from antigravity_cli_switcher.models import SnapshotVerification, StatusSnapshot
from antigravity_cli_switcher.watch import WatchPollResult, poll_quota_logs


def fetch_snapshot_data(paths: ManagerPaths) -> tuple[StatusSnapshot, SnapshotVerification]:
    """Fetch status snapshot and verification data synchronously (runs in worker thread)."""
    raw_snapshot = get_status_snapshot(paths)
    raw_verification = verify_accounts(paths)

    snapshot = StatusSnapshot.model_validate(raw_snapshot)
    verification = SnapshotVerification.model_validate(raw_verification)
    return snapshot, verification


def refresh_quota_data(paths: ManagerPaths, account_name: str) -> UsageRefreshResult:
    """Refresh account quota via agy task while protecting the global keyring.

    Runs in a worker thread. Keyring isolation is enforced inside refresh_account_usage.
    """
    return refresh_account_usage(paths, account_name)


def poll_logs(paths: ManagerPaths, started_at: float | None = None) -> WatchPollResult:
    """Poll quota logs for quota events and rotation triggers (runs in worker thread)."""
    return poll_quota_logs(paths, started_at=started_at, rotate=True)


def check_proxy_health(url: str, timeout_seconds: float = 3.0) -> tuple[bool, str]:
    """Test proxy reachability via socket connect."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
        if not host:
            return False, "invalid-url"
        start = time.perf_counter()
        sock = socket.create_connection((host, port), timeout=timeout_seconds)
        latency_ms = (time.perf_counter() - start) * 1000
        sock.close()
        return True, f"{latency_ms:.0f}ms"
    except Exception as exc:
        return False, f"err: {exc}"

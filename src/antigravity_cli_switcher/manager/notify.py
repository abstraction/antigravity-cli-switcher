"""Freedesktop desktop notification helpers for stealth quiescence and account arming."""

from __future__ import annotations

import os
import shutil
import subprocess
from math import ceil


def is_notify_available() -> bool:
    """Return True if notify-send binary is executable on the system."""
    from antigravity_cli_switcher.manager.keyring import _is_test_environment

    if _is_test_environment() and os.getenv("ACS_FORCE_NOTIFY") != "1":
        return False
    return os.access("/usr/bin/notify-send", os.X_OK) or shutil.which("notify-send") is not None


def emit_desktop_notification(
    title: str,
    message: str,
    *,
    urgency: str = "normal",
    app_name: str = "antigravity-cli-switcher",
) -> bool:
    """Emit a non-blocking desktop notification via notify-send if available."""
    from antigravity_cli_switcher.manager.keyring import _is_test_environment

    if _is_test_environment() and os.getenv("ACS_FORCE_NOTIFY") != "1":
        return False

    binary = "/usr/bin/notify-send" if os.access("/usr/bin/notify-send", os.X_OK) else shutil.which("notify-send")
    if not binary:
        return False
    try:
        subprocess.Popen(
            [binary, "-a", app_name, "-u", urgency, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def notify_draining_started(target_account: str, remaining_seconds: float) -> bool:
    """Emit desktop notification when stealth quiescence draining starts."""
    rem_int = max(1, ceil(remaining_seconds))
    return emit_desktop_notification(
        title="ACS: Quiescence Draining",
        message=f"Holding credentials for '{target_account}' ({rem_int}s remaining for stealth safety)...",
        urgency="normal",
    )


def notify_account_armed(target_account: str) -> bool:
    """Emit desktop notification when account switch completes and is armed."""
    return emit_desktop_notification(
        title="ACS: Account Armed",
        message=f"Switched to '{target_account}'. Safe to launch agy.",
        urgency="normal",
    )

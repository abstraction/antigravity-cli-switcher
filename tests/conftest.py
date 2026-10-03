"""Pytest configuration and global fixtures for antigravity-cli-switcher tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from antigravity_cli_switcher.tui.app import ACSApp

ORIGINAL_START_LOG_WATCHER = ACSApp.start_log_watcher
ORIGINAL_START_DUE_WATCHER = ACSApp.start_due_watcher


@pytest.fixture(autouse=True)
def disable_tui_background_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent infinite thread sleeping in TUI pilot tests."""
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.ACSApp.start_log_watcher",
        lambda self: None,
    )
    monkeypatch.setattr(
        "antigravity_cli_switcher.tui.app.ACSApp.start_due_watcher",
        lambda self: None,
    )


@pytest.fixture(autouse=True)
def isolate_test_logger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate logging handler so tests never write to user ~/.antigravity-cli-switcher logs."""
    import antigravity_cli_switcher.log as log_mod

    test_root = tmp_path / "acs_root"
    monkeypatch.setenv("ACS_ROOT", str(test_root))
    monkeypatch.setattr(log_mod, "_logger", None)

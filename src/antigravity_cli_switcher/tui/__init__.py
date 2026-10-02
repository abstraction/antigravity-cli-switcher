"""ACS Textual TUI package."""

from __future__ import annotations

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.tui.app import ACSApp

__all__ = ["ACSApp", "run_tui"]


def run_tui(paths: ManagerPaths, initial_tab: str = "tab-accounts") -> int:
    """Run the modern Textual terminal interface."""
    app = ACSApp(paths=paths, initial_tab=initial_tab)
    app.run()
    return 0

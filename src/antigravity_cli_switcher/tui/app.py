"""Main Textual application class for antigravity-cli-switcher (ACS)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from textual import work
from textual.app import App
from textual.message import Message
from textual.timer import Timer

from antigravity_cli_switcher.manager import ManagerPaths
from antigravity_cli_switcher.manager.routing import pick_due_refresh_account
from antigravity_cli_switcher.tui.messages import SnapshotUpdated, StatusMessage
from antigravity_cli_switcher.tui.screens.dashboard import DashboardScreen
from antigravity_cli_switcher.tui.workers import (
    fetch_snapshot_data,
    poll_logs,
    refresh_quota_data,
)


class ACSApp(App[int]):
    """Antigravity CLI Switcher Textual Dashboard."""

    CSS_PATH = Path(__file__).parent / "theme.tcss"

    def __init__(
        self,
        paths: ManagerPaths,
        *,
        initial_tab: str = "tab-accounts",
    ) -> None:
        super().__init__()
        self.paths = paths
        self.initial_tab = initial_tab
        self.refresh_interval_seconds: int = 5
        self.due_check_interval_seconds: float = 5.0
        self._refresh_timer: Timer | None = None
        self._log_watch_started_at: float = time.time()
        self._refreshing_accounts: set[str] = set()
        self._exit_event: threading.Event = threading.Event()
        self._pause_background: threading.Event = threading.Event()

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(initial_tab=self.initial_tab))
        self.refresh_snapshot()
        self._refresh_timer = self.set_interval(float(self.refresh_interval_seconds), self.refresh_snapshot)
        self.start_log_watcher()
        self.start_due_watcher()

    def on_unmount(self) -> None:
        self._exit_event.set()
        self._pause_background.set()
        if self._refresh_timer:
            self._refresh_timer.stop()

    def post_dashboard_message(self, message: Message) -> None:
        """Deliver message directly to DashboardScreen without bubbling loops."""
        if self._pause_background.is_set():
            return
        for s in self.screen_stack:
            if isinstance(s, DashboardScreen):
                s.post_message(message)

    def set_refresh_interval(self, interval_seconds: int) -> None:
        """Dynamically update the UI snapshot refresh interval."""
        self.refresh_interval_seconds = interval_seconds
        if self._refresh_timer:
            self._refresh_timer.stop()
        self._refresh_timer = self.set_interval(float(interval_seconds), self.refresh_snapshot)

    @contextmanager
    def suspend_terminal(self) -> Iterator[None]:
        """Suspend TUI, pausing all background workers and silencing driver writes to prevent deadlocks."""
        self._pause_background.set()
        if self._refresh_timer:
            self._refresh_timer.stop()
        try:
            self.workers.cancel_group(self, "snapshot")
        except Exception:
            pass

        wait_start = time.time()
        while self._refreshing_accounts and time.time() - wait_start < 10.0:
            time.sleep(0.05)

        can_suspend = bool(self._driver is not None and getattr(self._driver, "can_suspend", False))
        driver = self._driver
        try:
            if can_suspend and driver is not None:
                orig_write = getattr(driver, "write", None)
                orig_flush = getattr(driver, "flush", None)
                with self.suspend():
                    driver.write = lambda *args, **kwargs: None  # type: ignore[method-assign]
                    if orig_flush is not None:
                        driver.flush = lambda *args, **kwargs: None  # type: ignore[method-assign]
                    try:
                        yield
                    finally:
                        if orig_write is not None:
                            driver.write = orig_write  # type: ignore[method-assign]
                        if orig_flush is not None:
                            driver.flush = orig_flush  # type: ignore[method-assign]
            else:
                yield
        finally:
            self._pause_background.clear()
            self._refresh_timer = self.set_interval(float(self.refresh_interval_seconds), self.refresh_snapshot)
            self.refresh_snapshot()
            self.refresh(layout=True)

    @work(thread=True, exclusive=True, group="snapshot", exit_on_error=False)
    def refresh_snapshot(self) -> None:
        """Fetch latest system snapshot and post to reactive UI."""
        if self._pause_background.is_set():
            return
        try:
            snapshot, verification = fetch_snapshot_data(self.paths)
            self.post_dashboard_message(SnapshotUpdated(snapshot, verification))
        except Exception as exc:
            self.post_dashboard_message(StatusMessage(f"Snapshot read failed: {exc}"))

    def trigger_account_quota_refresh(self, account_name: str) -> None:
        """Start background quota refresh for an account if not already in progress."""
        if self._pause_background.is_set():
            return
        if account_name in self._refreshing_accounts:
            self.post_dashboard_message(StatusMessage(f"Quota check already running for {account_name}."))
            return
        self._refresh_account_worker(account_name)

    @work(thread=True, group="quota-refresh", exit_on_error=False)
    def _refresh_account_worker(self, account_name: str) -> None:
        """Background worker to refresh account quota using keyring isolation."""
        if account_name in self._refreshing_accounts or self._pause_background.is_set():
            return
        self._refreshing_accounts.add(account_name)
        self.post_dashboard_message(StatusMessage(f"Checking quota for {account_name}...", worker_status="CHECKING"))
        try:
            result = refresh_quota_data(self.paths, account_name)
            msg = f"Quota updated for {account_name}: Gemini {result.short_usage_status} / {result.weekly_usage_status}"
            self.post_dashboard_message(StatusMessage(msg, worker_status=None))
            self.refresh_snapshot()
        except Exception as exc:
            self.post_dashboard_message(
                StatusMessage(f"Quota check failed for {account_name}: {exc}", worker_status="ERROR")
            )
        finally:
            self._refreshing_accounts.discard(account_name)

    @work(thread=True, group="log-watcher", exit_on_error=False)
    def start_log_watcher(self) -> None:
        """Background worker continually polling quota logs for events."""
        last_snapshot_refresh = 0.0
        while not self.is_headless and not self._exit_event.is_set():
            if self._pause_background.is_set():
                time.sleep(0.2)
                continue
            try:
                result = poll_logs(self.paths, started_at=self._log_watch_started_at)
                if result.events or result.rotated:
                    summary = f"Log: {len(result.events)} event(s)"
                    if result.rotated and result.rotation:
                        summary += f" (switched to {result.rotation.switched_to})"
                    self.post_dashboard_message(StatusMessage(summary))
                    now = time.time()
                    if now - last_snapshot_refresh >= 3.0:
                        last_snapshot_refresh = now
                        self.refresh_snapshot()
            except Exception:
                pass
            if self._exit_event.wait(1.0):
                break

    def check_due_refresh(self) -> str | None:
        """Check if an account is due for refresh and trigger worker if found."""
        if self._refreshing_accounts or self._pause_background.is_set():
            return None
        target = pick_due_refresh_account(self.paths, exclude=self._refreshing_accounts)
        if target:
            self.trigger_account_quota_refresh(target)
        return target

    @work(thread=True, group="due-watcher", exit_on_error=False)
    def start_due_watcher(self) -> None:
        """Background worker periodically checking and refreshing due accounts."""
        last_due_check = 0.0
        while not self.is_headless and not self._exit_event.is_set():
            if self._pause_background.is_set():
                time.sleep(0.2)
                continue
            now = time.time()
            if now - last_due_check >= self.due_check_interval_seconds:
                last_due_check = now
                try:
                    self.check_due_refresh()
                except Exception:
                    pass
            if self._exit_event.wait(1.0):
                break

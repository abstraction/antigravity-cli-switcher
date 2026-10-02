"""Textual message classes for ACS TUI."""

from __future__ import annotations

from textual.message import Message

from antigravity_cli_switcher.models import SnapshotVerification, StatusSnapshot


class SnapshotUpdated(Message, bubble=False):
    """Posted when new snapshot and verification data is retrieved."""

    def __init__(self, snapshot: StatusSnapshot, verification: SnapshotVerification) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.verification = verification


class StatusMessage(Message):
    """Posted to display an ephemeral status message in the status bar."""

    def __init__(self, message: str, worker_status: str | None = None) -> None:
        super().__init__()
        self.message = message
        self.worker_status = worker_status

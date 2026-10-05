"""Keyboard shortcuts help modal dialog."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class HelpModal(ModalScreen[None]):
    """Modal dialog displaying keyboard shortcuts reference."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_help", "Close"),
        Binding("q", "dismiss_help", "Close", show=False),
        Binding("question_mark", "dismiss_help", "Close", show=False),
        Binding("h", "dismiss_help", "Close", show=False),
        Binding("enter", "dismiss_help", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="help-modal-dialog"):
            yield Static("Keyboard Shortcuts Reference", classes="modal-title")
            yield Static(self._build_help_text(), id="help-content")
            with Horizontal(classes="modal-buttons"):
                yield Button("Close (Esc)", id="btn-close", variant="primary")

    def _build_help_text(self) -> Text:
        t = Text()

        sections: list[tuple[str, list[tuple[str, str]]]] = [
            (
                "Account Management",
                [
                    ("Enter", "Switch to highlighted account"),
                    ("r", "Switch to next available account"),
                    ("l", "Relogin / fix OAuth credentials"),
                    ("n", "Login new account"),
                    ("i", "Import active agy profile"),
                    ("e", "Toggle enable / disable status"),
                    ("d", "Delete account"),
                    ("f2 / v", "Rename account"),
                ],
            ),
            (
                "Health & Hygiene",
                [
                    ("u", "Refresh quota usage"),
                    ("t / F5", "Refresh account data"),
                    ("c", "Clear bad / broken flag"),
                    ("m", "Flag as broken (cooldown)"),
                    ("@", "Edit expected email"),
                    ("p", "Configure auto-switch policy"),
                ],
            ),
            (
                "Navigation & View",
                [
                    ("1-6", "Jump to tab (Accounts, Logs, History, Hygiene, Proxies, Fleet)"),
                    ("[ / ]", "Previous / next tab"),
                    ("s", "Cycle sort order (name, state, health, usage)"),
                    ("o", "Toggle Details overview panel"),
                    ("T", "Cycle auto-refresh interval (5s, 10s, 15s, 30s)"),
                    ("w", "Toggle switch mode (manual / auto)"),
                    ("? / h", "Show this help screen"),
                    ("q", "Quit ACS"),
                ],
            ),
        ]

        for sec_title, items in sections:
            t.append(f"\n{sec_title}\n", style="bold cyan")
            for key, desc in items:
                t.append(f"  {key:<12}", style="bold yellow")
                t.append(f"{desc}\n", style="white")

        return t

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close":
            self.dismiss(None)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

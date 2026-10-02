"""Rename account modal dialog."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class RenameModal(ModalScreen[str | None]):
    """Modal dialog prompting for a new account name."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        current_name: str,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(f"Rename account: {self.current_name}", classes="modal-title")
            yield Input(placeholder="New account name", id="rename-input", value=self.current_name)
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Rename", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            val = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(val if val else None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

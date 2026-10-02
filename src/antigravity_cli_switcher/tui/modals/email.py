"""Modal dialog for setting or clearing expected account email."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class EmailModal(ModalScreen[str | None]):
    """Modal dialog prompting for expected account email."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        account_name: str,
        current_email: str | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.account_name = account_name
        self.current_email = current_email or ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(f"Expected email: {self.account_name}", classes="modal-title")
            yield Input(
                placeholder="user@example.com (empty to clear)",
                id="email-input",
                value=self.current_email,
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#email-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            val = self.query_one("#email-input", Input).value.strip()
            self.dismiss(val)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

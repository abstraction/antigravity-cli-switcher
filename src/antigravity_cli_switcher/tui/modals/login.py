"""New login modal dialog."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class LoginModal(ModalScreen[tuple[str, str | None] | None]):
    """Modal dialog collecting parameters for adding or logging into an account."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        initial_name: str = "",
        is_relogin: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.initial_name = initial_name
        self.is_relogin = is_relogin

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            title = (
                f"Fix / Relog in: {self.initial_name}" if self.is_relogin and self.initial_name else "Log in account"
            )
            yield Static(title, classes="modal-title")
            yield Label("Account name:")
            yield Input(
                value=self.initial_name,
                placeholder="e.g. work, personal, primary",
                id="login-name",
            )
            yield Label("Custom agy binary (optional):")
            yield Input(placeholder="Blank to auto-detect agy", id="login-binary")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                submit_label = "Relog in" if self.is_relogin else "Log in"
                yield Button(submit_label, id="btn-submit", variant="warning" if self.is_relogin else "success")

    def on_mount(self) -> None:
        self.query_one("#login-name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        name = self.query_one("#login-name", Input).value.strip()
        binary = self.query_one("#login-binary", Input).value.strip() or None
        if not name:
            return
        self.dismiss((name, binary))

    def action_cancel(self) -> None:
        self.dismiss(None)

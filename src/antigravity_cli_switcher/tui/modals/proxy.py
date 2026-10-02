"""Proxy configuration modal dialog."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static

from antigravity_cli_switcher.models import ProxyConfig


class ProxyModal(ModalScreen[tuple[str, str | None, bool] | None]):
    """Modal dialog for setting or updating an account proxy."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        account_name: str,
        proxy: ProxyConfig | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.account_name = account_name
        self.proxy = proxy or ProxyConfig()

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(f"Configure proxy: {self.account_name}", classes="modal-title")
            yield Label("Proxy URL:")
            yield Input(
                placeholder="http://host:port or socks5://host:port",
                id="proxy-url",
                value=self.proxy.url or "",
            )
            yield Label("Label (optional):")
            yield Input(
                placeholder="e.g. US Residential, Office VPN",
                id="proxy-label",
                value=self.proxy.label or "",
            )
            yield Checkbox("Enabled", value=self.proxy.enabled, id="proxy-enabled")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#proxy-url", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        url = self.query_one("#proxy-url", Input).value.strip()
        label = self.query_one("#proxy-label", Input).value.strip() or None
        enabled = self.query_one("#proxy-enabled", Checkbox).value
        if not url:
            self.dismiss(None)
            return
        self.dismiss((url, label, enabled))

    def action_cancel(self) -> None:
        self.dismiss(None)

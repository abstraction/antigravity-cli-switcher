"""Stealth account switch cooldown modal dialog."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from antigravity_cli_switcher.tui.theme import render_meter_bar


class StealthCooldownModal(ModalScreen[str]):
    """Modal displaying countdown when switching accounts during active stealth cooldown."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "wait_and_switch", "Wait & Switch"),
        Binding("f", "force_switch", "Force Switch"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        target_account: str,
        remaining_seconds: float,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.target_account = target_account
        self.remaining_seconds = max(0.0, remaining_seconds)
        self.initial_seconds = max(60.0, remaining_seconds)
        self.waiting_to_switch = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("⏳ Stealth Switch Cooldown", classes="modal-title")
            yield Static(
                f"Target: [bold #77ca9b]{self.target_account}[/]\n"
                "Google telemetry monitors rapid multi-account rotation from the same machine.\n"
                "A 60s cooldown ensures session header draining and Unleash cohort stability.",
                id="cooldown-explanation",
            )
            yield Static(id="cooldown-timer-display")
            with Horizontal(classes="modal-buttons"):
                yield Button(r"[bold #77ca9b]\[Enter][/] Wait & Switch", id="btn-wait", variant="success")
                yield Button(r"[bold #dc4c4c]\[f][/] Force Switch Now", id="btn-force", variant="error")
                yield Button(r"[bold #cccccc]\[Esc][/] Cancel", id="btn-cancel", variant="default")

    def on_mount(self) -> None:
        self._refresh_timer_display()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self.remaining_seconds = max(0.0, self.remaining_seconds - 1.0)
        self._refresh_timer_display()
        if self.remaining_seconds <= 0.0 and self.waiting_to_switch:
            self.dismiss("switch")

    def _refresh_timer_display(self) -> None:
        try:
            display = self.query_one("#cooldown-timer-display", Static)
        except Exception:
            return

        pct = max(0.0, min(100.0, (1.0 - (self.remaining_seconds / self.initial_seconds)) * 100.0))
        text = Text()
        text.append("\nCooldown: ", style="#888888")
        rem_str = f"{int(self.remaining_seconds)}s remaining"
        text.append(rem_str, style="bold #e06c75" if self.remaining_seconds > 0 else "bold #77ca9b")
        text.append("  ")
        text.append_text(render_meter_bar(pct, width=24))

        if self.waiting_to_switch:
            text.append("\n● Auto-switching as soon as countdown reaches 0s...", style="italic #cbc06c")
        elif self.remaining_seconds <= 0.0:
            text.append("\n● Cooldown expired! Press [Enter] to switch now.", style="bold #77ca9b")

        display.update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-force":
            self.action_force_switch()
        elif event.button.id == "btn-wait":
            self.action_wait_and_switch()
        else:
            self.action_cancel()

    def action_force_switch(self) -> None:
        self.dismiss("force")

    def action_wait_and_switch(self) -> None:
        self.waiting_to_switch = True
        if self.remaining_seconds <= 0.0:
            self.dismiss("switch")
            return
        self._refresh_timer_display()

    def action_cancel(self) -> None:
        self.dismiss("cancel")

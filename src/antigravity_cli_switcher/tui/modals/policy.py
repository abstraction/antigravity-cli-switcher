"""Switch policy editing modal dialog."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from antigravity_cli_switcher.models import SwitchPolicy, SwitchPolicyUpdate


class PolicyModal(ModalScreen[SwitchPolicyUpdate | None]):
    """Modal dialog for interactive adjustment of auto-failover and routing policies."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CANDIDATE_STRATEGIES: ClassVar[list[tuple[str, str]]] = [
        ("Balanced (headroom & load)", "balanced"),
        ("Highest Short Quota", "highest-short"),
        ("Round Robin", "round-robin"),
    ]

    FAMILY_FALLBACK_STRATEGIES: ClassVar[list[tuple[str, str]]] = [
        ("Same Family First", "same-family-first"),
        ("Same Account First", "same-account-first"),
        ("Strict Family (no cross-family)", "strict-family"),
    ]

    def __init__(
        self,
        current_policy: SwitchPolicy | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.current_policy = current_policy or SwitchPolicy()

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Edit switch policy", classes="modal-title")

            yield Label("Short usage threshold (%) [0-100]:")
            yield Input(
                str(self.current_policy.short_usage_threshold_percent),
                placeholder="e.g. 10.0 or 80.0",
                id="input-short-threshold",
            )

            yield Label("Failure threshold (>= 1):")
            yield Input(
                str(self.current_policy.refresh_failure_threshold),
                placeholder="e.g. 2 or 3",
                id="input-failure-threshold",
            )

            cand_val = self.current_policy.candidate_strategy
            if cand_val not in ("balanced", "highest-short", "round-robin"):
                cand_val = "balanced"
            yield Label("Candidate strategy:")
            yield Select[str](
                options=self.CANDIDATE_STRATEGIES,
                value=cand_val,
                allow_blank=False,
                id="select-candidate-strategy",
            )

            fallback_val = self.current_policy.family_fallback_strategy
            if fallback_val not in ("same-family-first", "same-account-first", "strict-family"):
                fallback_val = "same-family-first"
            yield Label("Family fallback strategy:")
            yield Select[str](
                options=self.FAMILY_FALLBACK_STRATEGIES,
                value=fallback_val,
                allow_blank=False,
                id="select-fallback-strategy",
            )

            yield Static(id="policy-error", classes="error-label")

            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#input-short-threshold", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        error_label = self.query_one("#policy-error", Static)
        try:
            short_raw = self.query_one("#input-short-threshold", Input).value.strip()
            short_val = float(short_raw)
            if not 0.0 <= short_val <= 100.0:
                raise ValueError("Short threshold must be between 0.0 and 100.0")
        except ValueError as exc:
            error_label.update(f"Error: {exc}")
            self.query_one("#input-short-threshold", Input).focus()
            return

        try:
            fail_raw = self.query_one("#input-failure-threshold", Input).value.strip()
            fail_val = int(fail_raw)
            if fail_val < 1:
                raise ValueError("Failure threshold must be at least 1")
        except ValueError as exc:
            error_label.update(f"Error: {exc}")
            self.query_one("#input-failure-threshold", Input).focus()
            return

        cand_sel = self.query_one("#select-candidate-strategy", Select)
        cand_val = str(cand_sel.value) if cand_sel.value != Select.BLANK else "balanced"

        fallback_sel = self.query_one("#select-fallback-strategy", Select)
        fallback_val = str(fallback_sel.value) if fallback_sel.value != Select.BLANK else "same-family-first"

        self.dismiss(
            SwitchPolicyUpdate(
                short_usage_threshold_percent=short_val,
                refresh_failure_threshold=fail_val,
                candidate_strategy=cand_val,
                family_fallback_strategy=fallback_val,
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

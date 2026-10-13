from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from antigravity_cli_switcher.manager.accounts import switch_account
from antigravity_cli_switcher.manager.failover import rotate_after_failure_locked
from antigravity_cli_switcher.manager.paths import ManagerPaths, build_paths, ensure_layout
from antigravity_cli_switcher.manager.profiles import save_account_profile
from antigravity_cli_switcher.manager.state import load_state, save_state


def _setup_accounts(tmp_path: Path) -> tuple[ManagerPaths, Path, Path]:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    for src, email in ((src1, "alpha@example.com"), (src2, "beta@example.com")):
        cli_dir = src / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        (cli_dir / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": f"tok_{email}"}}), encoding="utf-8"
        )
        (cli_dir / "installation_id").write_text("11111111-1111-4111-8111-111111111111", encoding="utf-8")
        (cli_dir / "jetski_state.pbtxt").write_text(
            'installation_uuid: "11111111-1111-4111-8111-111111111111"\n', encoding="utf-8"
        )

    save_account_profile(paths, "acc1", src1)
    save_account_profile(paths, "acc2", src2)

    state = load_state(paths)
    live_dir = tmp_path / "live_gemini" / ".gemini"
    live_dir.mkdir(parents=True, exist_ok=True)
    state["live_dir"] = str(live_dir)
    state["active"] = "acc1"
    save_state(paths, state)
    return paths, src1, src2


def test_switch_terminates_active_process_without_deadlock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, _ = _setup_accounts(tmp_path)
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    kill_calls: list[str] = []

    def mock_safe_switch() -> list[int]:
        kill_calls.append("killed")
        return [88888]

    monkeypatch.setattr(
        "antigravity_cli_switcher.manager.accounts._ensure_safe_account_switch",
        mock_safe_switch,
    )

    # Calling switch_account with force=False must succeed without raising 60s cooldown RuntimeError
    switch_account(paths, "acc2", force=False)

    assert len(kill_calls) == 1
    loaded = load_state(paths)
    assert loaded["active"] == "acc2"
    runtime = loaded.get("switch_runtime", {})
    assert runtime.get("last_process_kill_at") is not None


def test_subsequent_immediate_switch_enforces_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, _ = _setup_accounts(tmp_path)
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    # Initial switch succeeds
    switch_account(paths, "acc2", force=False)
    assert load_state(paths)["active"] == "acc2"

    # Immediately attempting another switch must hit stealth cooldown
    with pytest.raises(RuntimeError, match="Stealth cooldown active"):
        switch_account(paths, "acc1", force=False)


def test_failover_not_blocked_by_active_agy_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, _ = _setup_accounts(tmp_path)
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    # Mock external agy processes returning active PID
    with patch(
        "antigravity_cli_switcher.manager.stealth.get_external_agy_processes",
        return_value=[99999],
    ):
        with patch(
            "antigravity_cli_switcher.manager.login._ensure_safe_account_switch",
            return_value=[99999],
        ):
            # Automatic failover should rotate despite active agy process
            result = rotate_after_failure_locked(
                paths,
                reason="quota",
                force_switch=True,
                trigger="log-watch",
            )
            assert result.outcome == "switched"
            assert result.switched_to == "acc2"
            assert load_state(paths)["active"] == "acc2"

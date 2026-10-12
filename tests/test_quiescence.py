"""Unit and integration tests for dynamic quiescence tracking, holding pattern, and notifications."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from antigravity_cli_switcher.cli.interactive import run_menu
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.cli.switch import (
    cmd_switch,
    render_draining_countdown,
)
from antigravity_cli_switcher.manager.accounts import switch_account
from antigravity_cli_switcher.manager.notify import (
    emit_desktop_notification,
)
from antigravity_cli_switcher.manager.paths import build_paths, ensure_layout
from antigravity_cli_switcher.manager.state import (
    load_state,
    save_state,
)
from antigravity_cli_switcher.manager.stealth import (
    STEALTH_SWITCH_COOLDOWN_SECONDS,
    get_agy_quiescence_remaining,
)


def test_quiescence_remaining_inactive_zero(tmp_path: Path) -> None:
    live_dir = tmp_path / ".gemini"
    agy_dir = live_dir / "antigravity-cli"
    agy_dir.mkdir(parents=True)
    old_time = time.time() - 200.0

    cli_log = agy_dir / "cli.log"
    cli_log.write_text("old session log\n", encoding="utf-8")
    os.utime(cli_log, (old_time, old_time))

    state: dict[str, object] = {"switch_runtime": {"last_process_kill_at": None}}
    rem = get_agy_quiescence_remaining(state=state, live_dir=live_dir)
    assert rem == 0.0


def test_quiescence_remaining_active_process() -> None:
    fake_procs = [98765]
    with patch(
        "antigravity_cli_switcher.manager.stealth.get_external_agy_processes",
        return_value=fake_procs,
    ):
        rem = get_agy_quiescence_remaining()
        assert rem == float(STEALTH_SWITCH_COOLDOWN_SECONDS)


def test_quiescence_remaining_recent_process_kill() -> None:
    now = time.time()
    from datetime import datetime, timezone

    kill_dt = datetime.fromtimestamp(now - 25.0, tz=timezone.utc)
    state: dict[str, object] = {
        "switch_runtime": {"last_process_kill_at": kill_dt.isoformat()},
    }
    rem = get_agy_quiescence_remaining(state=state, now=now)
    assert 34.0 <= rem <= 36.0


def test_quiescence_remaining_recent_artifacts(tmp_path: Path) -> None:
    live_dir = tmp_path / ".gemini"
    agy_dir = live_dir / "antigravity-cli"
    agy_dir.mkdir(parents=True)
    log_dir = agy_dir / "log"
    log_dir.mkdir(parents=True)

    now = time.time()
    recent_time = now - 15.0

    wal = agy_dir / "conversation_summaries.db-wal"
    wal.write_text("wal data", encoding="utf-8")
    os.utime(wal, (recent_time, recent_time))

    rem = get_agy_quiescence_remaining(live_dir=live_dir, now=now)
    assert 44.0 <= rem <= 46.0


def test_switch_account_enforces_quiescence_holding_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc1 = "user_alpha"
    acc2 = "user_beta"
    d1 = paths.accounts_dir / acc1
    d2 = paths.accounts_dir / acc2
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    for d in (d1, d2):
        tok_dir = d / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "test"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    save_state(paths, state)

    live_dir = paths.root / ".gemini" / "antigravity-cli"
    live_dir.mkdir(parents=True, exist_ok=True)
    recent_log = live_dir / "cli.log"
    recent_log.write_text("active output", encoding="utf-8")
    recent_time = time.time() - 10.0
    os.utime(recent_log, (recent_time, recent_time))

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    with pytest.raises(RuntimeError, match="Stealth cooldown active"):
        switch_account(paths, acc2, force=False)

    loaded = load_state(paths)
    assert loaded["active"] == acc1

    switch_account(paths, acc2, force=True)
    loaded_after = load_state(paths)
    assert loaded_after["active"] == acc2


def test_interactive_menu_catches_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    parser = build_parser()

    # Inputs: "4" (switch account), "acc_target", then "0" (exit)
    inputs = iter(["4", "acc_target", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    with patch(
        "antigravity_cli_switcher.cli.interactive.switch_account",
        side_effect=RuntimeError("Stealth cooldown active: 45s remaining."),
    ):
        code = run_menu(paths, parser)
        assert code == 0

    captured = capsys.readouterr()
    assert "error: Stealth cooldown active: 45s remaining." in captured.out


def test_desktop_notifications_suppressed_in_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # In test environment, emit_desktop_notification returns False
    res = emit_desktop_notification("Title", "Message")
    assert res is False

    # When forced via ACS_FORCE_NOTIFY, launches Popen
    monkeypatch.setenv("ACS_FORCE_NOTIFY", "1")
    with patch("shutil.which", return_value="/usr/bin/notify-send"):
        with patch("subprocess.Popen") as mock_popen:
            res_forced = emit_desktop_notification("Title", "Message")
            assert res_forced is True
            mock_popen.assert_called_once()


def test_render_draining_countdown_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)
    ok = render_draining_countdown("acc_test", remaining_seconds=2.0)
    assert ok is True

    # User cancel with Ctrl+C
    def raise_interrupt(s: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", raise_interrupt)
    cancelled = render_draining_countdown("acc_test", remaining_seconds=2.0)
    assert cancelled is False


def test_cli_switch_no_wait_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    parser = build_parser()

    acc = "acc_standby"
    (paths.accounts_dir / acc).mkdir(parents=True)
    state = load_state(paths)
    state["accounts"] = {acc: {"enabled": True, "status": "standby"}}
    state["active"] = "acc_other"
    from datetime import datetime, timezone

    state["switch_runtime"] = {"last_account_switch_at": datetime.now(timezone.utc).isoformat()}
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")
    args = parser.parse_args(["switch", acc, "--no-wait"])
    assert args.no_wait is True

    with patch(
        "antigravity_cli_switcher.cli.switch.switch_account",
        side_effect=RuntimeError("Stealth cooldown active: 50s"),
    ):
        code = cmd_switch(paths, args)
        assert code == 1


def test_quiescence_clock_skew_bounded(tmp_path: Path) -> None:
    live_dir = tmp_path / ".gemini"
    agy_dir = live_dir / "antigravity-cli"
    agy_dir.mkdir(parents=True)

    now = time.time()
    # File with mtime 500s in the future (NTP jump / clock skew)
    future_time = now + 500.0

    cli_log = agy_dir / "cli.log"
    cli_log.write_text("skew log", encoding="utf-8")
    os.utime(cli_log, (future_time, future_time))

    rem = get_agy_quiescence_remaining(live_dir=live_dir, now=now)
    assert rem == float(STEALTH_SWITCH_COOLDOWN_SECONDS)


def test_cli_switch_terminates_processes_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    parser = build_parser()

    acc1 = "acc_current"
    acc2 = "acc_standby"
    for acc in (acc1, acc2):
        d = paths.accounts_dir / acc / ".gemini" / "antigravity-cli"
        d.mkdir(parents=True)
        (d / "antigravity-oauth-token").write_text('{"token": "test"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    save_state(paths, state)

    kill_calls: list[str] = []

    def mock_ensure_safe() -> list[int]:
        kill_calls.append("killed")
        return [12345]

    monkeypatch.setattr(
        "antigravity_cli_switcher.cli.switch._ensure_safe_account_switch",
        mock_ensure_safe,
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    args = parser.parse_args(["switch", acc2])
    code = cmd_switch(paths, args)
    assert code == 0
    assert len(kill_calls) == 1

    loaded = load_state(paths)
    assert loaded["active"] == acc2
    runtime = loaded.get("switch_runtime", {})
    assert runtime.get("last_process_kill_at") is not None


def test_cli_switch_same_account_clean_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    parser = build_parser()

    acc1 = "acc_active"
    (paths.accounts_dir / acc1).mkdir(parents=True)
    tok_dir = paths.accounts_dir / acc1 / ".gemini" / "antigravity-cli"
    tok_dir.mkdir(parents=True, exist_ok=True)
    (tok_dir / "antigravity-oauth-token").write_text('{"token": "test"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {acc1: {"enabled": True, "status": "active"}}
    state["active"] = acc1
    save_state(paths, state)

    with patch("antigravity_cli_switcher.cli.switch.notify_account_armed") as mock_notify:
        args = parser.parse_args(["switch", acc1])
        code = cmd_switch(paths, args)
        assert code == 0
        mock_notify.assert_not_called()

    captured = capsys.readouterr()
    assert "switched: acc_active" in captured.out


def test_cli_rotate_json_stream_separation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from antigravity_cli_switcher.cli.switch import cmd_rotate

    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    parser = build_parser()

    acc1 = "acc_one"
    acc2 = "acc_two"
    for acc in (acc1, acc2):
        d = paths.accounts_dir / acc / ".gemini" / "antigravity-cli"
        d.mkdir(parents=True)
        (d / "antigravity-oauth-token").write_text('{"token": "test"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    from datetime import datetime, timezone

    state["switch_runtime"] = {"last_account_switch_at": datetime.now(timezone.utc).isoformat()}
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    args = parser.parse_args(["rotate", "--json"])
    code = cmd_rotate(paths, args)
    assert code == 0

    captured = capsys.readouterr()
    # Draining countdown must be printed to stderr, NOT stdout
    assert "[STEALTH] Draining quiescence" in captured.err
    # stdout must parse cleanly as JSON
    import json

    data = json.loads(captured.out)
    assert data["active"] == acc2


def test_tui_app_cooldown_includes_quiescence(tmp_path: Path) -> None:
    from antigravity_cli_switcher.tui.app import ACSApp

    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    app = ACSApp(paths)

    # With no activity, cooldown is 0.0
    assert app.get_switch_cooldown_remaining() == 0.0

    # Write recent WAL file to live directory
    live_dir = paths.root / ".gemini" / "antigravity-cli"
    live_dir.mkdir(parents=True, exist_ok=True)
    wal = live_dir / "conversation_summaries.db-wal"
    wal.write_text("wal data", encoding="utf-8")
    recent = time.time() - 10.0
    os.utime(wal, (recent, recent))

    rem = app.get_switch_cooldown_remaining()
    assert 48.0 <= rem <= 52.0

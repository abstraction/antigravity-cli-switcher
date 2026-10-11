"""Unit tests for stealth architecture, installation ID server parity, and cooldown pacing."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antigravity_cli_switcher.manager.accounts import (
    STEALTH_SWITCH_COOLDOWN_SECONDS,
    get_switch_cooldown_remaining,
    switch_account,
    switch_next,
)
from antigravity_cli_switcher.manager.hygiene import check_hygiene, fix_hygiene
from antigravity_cli_switcher.manager.paths import build_paths, ensure_layout
from antigravity_cli_switcher.manager.state import (
    load_state,
    save_state,
    sync_state_from_disk,
)
from antigravity_cli_switcher.manager.stealth import (
    backfill_account_identities,
    ensure_account_identity_files,
    extract_jetski_uuid,
    get_ancestor_pids,
    get_external_agy_processes,
    is_valid_uuid_string,
    read_canonical_uuid_file,
    terminate_external_agy_processes,
    write_canonical_uuid_file,
)


def test_uuid_validation_and_canonical_formatting(tmp_path: Path) -> None:
    valid_uuid = "c3f80877-e85d-4f16-9dc4-839f375f483c"
    assert is_valid_uuid_string(valid_uuid)
    assert not is_valid_uuid_string("invalid-uuid-string")
    assert not is_valid_uuid_string(valid_uuid + "\n")
    assert not is_valid_uuid_string(valid_uuid[:-1])

    target = tmp_path / "antigravity-cli" / "installation_id"
    written = write_canonical_uuid_file(target, valid_uuid)
    assert written == valid_uuid

    # Parity check: exactly 36 lowercase hex ASCII bytes, zero trailing newline
    raw_bytes = target.read_bytes()
    assert len(raw_bytes) == 36
    assert b"\n" not in raw_bytes
    assert b"\r" not in raw_bytes
    assert raw_bytes == valid_uuid.encode("ascii")

    # Read back canonical file
    read_val = read_canonical_uuid_file(target)
    assert read_val == valid_uuid

    # Invalid file length returns None
    target.write_bytes(raw_bytes + b"\n")
    assert read_canonical_uuid_file(target) is None


def test_extract_jetski_uuid() -> None:
    uuid_str = "a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d"
    pbtxt_content = f'installation_uuid:  "{uuid_str}"\n'
    assert extract_jetski_uuid(pbtxt_content) == uuid_str

    malformed = 'installation_uuid:  "not-a-valid-uuid"\n'
    assert extract_jetski_uuid(malformed) is None
    assert extract_jetski_uuid("empty") is None


def test_ensure_account_identity_files_creates_all_three(tmp_path: Path) -> None:
    profile_dir = tmp_path / "test_account_a"
    res = ensure_account_identity_files(profile_dir)

    cli_path = profile_dir / "antigravity-cli" / "installation_id"
    root_path = profile_dir / "installation_id"
    jetski_path = profile_dir / "antigravity-cli" / "jetski_state.pbtxt"

    assert cli_path.is_file()
    assert root_path.is_file()
    assert jetski_path.is_file()

    assert is_valid_uuid_string(res["installation_id"])
    assert is_valid_uuid_string(res["root_installation_id"])
    assert is_valid_uuid_string(res["jetski_uuid"])

    # Parity check on byte level
    assert len(cli_path.read_bytes()) == 36
    assert len(root_path.read_bytes()) == 36

    # Calling again preserves existing IDs without churning
    res2 = ensure_account_identity_files(profile_dir)
    assert res2["installation_id"] == res["installation_id"]
    assert res2["root_installation_id"] == res["root_installation_id"]
    assert res2["jetski_uuid"] == res["jetski_uuid"]


def test_backfill_account_identities_and_ensure_layout(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    acc1_dir = paths.accounts_dir / "account_alpha"
    acc2_dir = paths.accounts_dir / "account_beta"
    acc1_dir.mkdir(parents=True)
    acc2_dir.mkdir(parents=True)

    # Initially empty, run backfill
    count = backfill_account_identities(paths)
    assert count == 2

    assert (acc1_dir / "antigravity-cli" / "installation_id").is_file()
    assert (acc2_dir / "installation_id").is_file()
    assert (acc2_dir / "antigravity-cli" / "jetski_state.pbtxt").is_file()

    # ensure_layout also triggers backfill cleanly
    ensure_layout(paths)
    assert (paths.accounts_dir / "account_alpha" / "antigravity-cli" / "installation_id").is_file()


def test_ancestor_process_protection_and_termination() -> None:
    # 1. Self PID must be in ancestor chain
    ancestors = get_ancestor_pids()
    assert os.getpid() in ancestors
    assert os.getppid() in ancestors

    # 2. Mock external process scanning
    fake_pid = 999999
    with patch("antigravity_cli_switcher.manager.stealth.get_ancestor_pids", return_value={1, 100, os.getpid()}):
        with patch("pathlib.Path.is_dir", return_value=True):
            with patch("pathlib.Path.iterdir") as mock_iterdir:
                entry1 = MagicMock()
                entry1.name = str(fake_pid)
                stat_mock = MagicMock()
                stat_mock.st_uid = os.getuid()
                entry1.stat.return_value = stat_mock

                ancestor_entry = MagicMock()
                ancestor_entry.name = "100"

                mock_iterdir.return_value = [entry1, ancestor_entry]

                with patch("antigravity_cli_switcher.manager.stealth.is_canonical_agy_binary", return_value=True):
                    procs = get_external_agy_processes()
                    # fake_pid is included, ancestor 100 is protected and excluded
                    assert procs == [fake_pid]

    # 3. Test termination: SIGTERM followed by wait
    with patch("antigravity_cli_switcher.manager.stealth.get_external_agy_processes", return_value=[fake_pid]):
        with patch("os.kill") as mock_kill:
            # First kill(pid, SIGTERM) succeeds
            # kill(pid, 0) raises OSError (process exited cleanly after SIGTERM)
            def kill_side_effect(pid: int, sig: int) -> None:
                if sig == 0:
                    raise OSError("Process terminated")

            mock_kill.side_effect = kill_side_effect

            terminated = terminate_external_agy_processes()
            assert terminated == [fake_pid]
            mock_kill.assert_any_call(fake_pid, signal.SIGTERM)


def test_switch_cooldown_enforcement_and_force_bypass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc1 = "user_one"
    acc2 = "user_two"
    d1 = paths.accounts_dir / acc1
    d2 = paths.accounts_dir / acc2
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    for d in (d1, d2):
        tok_dir = d / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")

    # Initialize state with two synthetic accounts
    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "standby"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    save_state(paths, state)

    # Verify initial cooldown is 0
    assert get_switch_cooldown_remaining(state) == 0.0

    # Enable cooldown enforcement in test environment
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    # First switch succeeds
    switch_account(paths, acc2)
    state = sync_state_from_disk(paths, load_state(paths))
    assert state["active"] == acc2
    assert state["switch_runtime"]["last_account_switch_at"] is not None

    cooldown_rem = get_switch_cooldown_remaining(state)
    assert 0 < cooldown_rem <= STEALTH_SWITCH_COOLDOWN_SECONDS

    # Immediate second switch without force raises RuntimeError
    with pytest.raises(RuntimeError, match="Stealth cooldown active"):
        switch_account(paths, acc1, force=False)

    # Immediate switch with force=True succeeds
    switch_account(paths, acc1, force=True)
    state = sync_state_from_disk(paths, load_state(paths))
    assert state["active"] == acc1

    # switch_next also obeys cooldown and force flag
    with pytest.raises(RuntimeError, match="Stealth cooldown active"):
        switch_next(paths, force=False)

    target = switch_next(paths, force=True)
    assert target == acc2


def test_hygiene_installation_id_audit_and_fix(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc1 = "acc_alpha"
    acc2 = "acc_beta"
    d1 = paths.accounts_dir / acc1
    d2 = paths.accounts_dir / acc2
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    for d in (d1, d2):
        tok_dir = d / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    save_state(paths, state)

    # Induce collision: write identical installation ID to both accounts
    shared_uuid = "e0e0e0e0-1111-2222-3333-444444444444"
    for d in (d1, d2):
        gdir = d / ".gemini" if (d / ".gemini").is_dir() else d
        write_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id", shared_uuid)

    # Audit detects collision
    audit_results = check_hygiene(paths)
    collisions = [r for r in audit_results if r.category == "installation_id" and "collision" in r.detail.lower()]
    assert len(collisions) >= 2

    # Fix resolves collisions and assigns unique IDs
    actions = fix_hygiene(paths)
    assert any("unique" in a.lower() for a in actions)

    id1 = read_canonical_uuid_file(
        (d1 / ".gemini" if (d1 / ".gemini").is_dir() else d1) / "antigravity-cli" / "installation_id"
    )
    id2 = read_canonical_uuid_file(
        (d2 / ".gemini" if (d2 / ".gemini").is_dir() else d2) / "antigravity-cli" / "installation_id"
    )
    assert id1 is not None and is_valid_uuid_string(id1)
    assert id2 is not None and is_valid_uuid_string(id2)
    assert id1 != id2

    # Re-audit confirms collisions are gone
    re_audit = check_hygiene(paths)
    remaining_collisions = [r for r in re_audit if r.category == "installation_id" and "collision" in r.detail.lower()]
    assert len(remaining_collisions) == 0


def test_automated_failover_cooldown_enforcement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_cli_switcher.manager.failover import rotate_after_failure
    from antigravity_cli_switcher.manager.policy import set_switch_mode

    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc1 = "acc_alpha"
    acc2 = "acc_beta"
    d1 = paths.accounts_dir / acc1
    d2 = paths.accounts_dir / acc2
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    for d in (d1, d2):
        tok_dir = d / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    set_switch_mode(paths, "auto")
    save_state(paths, state)

    # Enable cooldown enforcement
    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    # Switch once to trigger cooldown
    switch_account(paths, acc2, force=True)

    # Automated failover without force_switch is blocked by stealth cooldown
    res_blocked = rotate_after_failure(paths, reason="quota limit", force_switch=False)
    assert res_blocked.outcome == "cooldown_blocked"
    assert res_blocked.switched_to is None

    # Re-activate acc2 so active account is set
    switch_account(paths, acc2, force=True)

    # Automated failover with force_switch=True bypasses cooldown
    res_forced = rotate_after_failure(paths, reason="quota limit", force_switch=True)
    assert res_forced.outcome == "switched"
    assert res_forced.switched_to == acc1


def test_switch_same_account_bypasses_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc1 = "same_acc_one"
    acc2 = "same_acc_two"
    d1 = paths.accounts_dir / acc1
    d2 = paths.accounts_dir / acc2
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)

    for d in (d1, d2):
        tok_dir = d / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")

    state = load_state(paths)
    state["accounts"] = {
        acc1: {"enabled": True, "status": "active"},
        acc2: {"enabled": True, "status": "standby"},
    }
    state["active"] = acc1
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    # Switch to acc2
    switch_account(paths, acc2, force=True)
    state = load_state(paths)
    orig_ts = state["switch_runtime"]["last_account_switch_at"]

    # Re-switching to acc2 does NOT raise cooldown error and does NOT overwrite switch timestamp
    switch_account(paths, acc2, force=False)
    state2 = load_state(paths)
    assert state2["active"] == acc2
    assert state2["switch_runtime"]["last_account_switch_at"] == orig_ts


def test_concurrent_switch_account_serialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import concurrent.futures

    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    acc_names = [f"conc_acc_{i}" for i in range(4)]
    accounts_dict = {}
    for name in acc_names:
        adir = paths.accounts_dir / name
        adir.mkdir(parents=True)
        tok_dir = adir / ".gemini" / "antigravity-cli"
        tok_dir.mkdir(parents=True, exist_ok=True)
        (tok_dir / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")
        accounts_dict[name] = {"enabled": True, "status": "standby"}

    state = load_state(paths)
    state["accounts"] = accounts_dict
    state["active"] = acc_names[0]
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    successes: list[str] = []
    cooldown_errors: list[str] = []

    def attempt_switch(target: str) -> None:
        try:
            switch_account(paths, target, force=False)
            successes.append(target)
        except RuntimeError as exc:
            cooldown_errors.append(str(exc))

    # Concurrently try switching to accounts 1, 2, 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(attempt_switch, acc_names[i]) for i in range(1, 4)]
        concurrent.futures.wait(futures)

    # Exactly 1 must succeed; the remaining 2 must be blocked by the 60s cooldown
    assert len(successes) == 1
    assert len(cooldown_errors) == 2
    assert all("Stealth cooldown active" in err for err in cooldown_errors)


def test_is_canonical_agy_binary_with_deleted_suffix() -> None:
    from antigravity_cli_switcher.manager.stealth import is_canonical_agy_binary

    with patch("pathlib.Path.resolve") as mock_resolve:
        mock_path = MagicMock()
        mock_path.name = "antigravity (deleted)"
        mock_resolve.return_value = mock_path
        assert is_canonical_agy_binary(12345)

        mock_path.name = "agy (deleted)"
        assert is_canonical_agy_binary(12345)

        mock_path.name = "python3"
        assert not is_canonical_agy_binary(12345)


def test_terminate_processes_handles_permission_error_correctly() -> None:
    fake_pid = 777777
    with patch("antigravity_cli_switcher.manager.stealth.get_external_agy_processes", return_value=[fake_pid]):
        with patch("os.kill") as mock_kill:
            # First SIGTERM succeeds
            # In wait loop, kill(pid, 0) raises PermissionError (process is alive but owned by other user)
            def kill_effect(pid: int, sig: int) -> None:
                if sig == signal.SIGTERM:
                    return
                if sig == 0:
                    raise PermissionError("EPERM")

            mock_kill.side_effect = kill_effect
            terminated = terminate_external_agy_processes()
            # Because process is still alive (PermissionError), it is NOT in terminated list
            assert fake_pid not in terminated

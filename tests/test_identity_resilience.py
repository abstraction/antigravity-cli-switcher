"""Tests for identity resilience, backup snapshots, and hybrid dual-identity architecture."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from antigravity_cli_switcher.cli.commands import dispatch_command
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.manager.accounts import switch_account
from antigravity_cli_switcher.manager.hygiene import check_hygiene, fix_hygiene
from antigravity_cli_switcher.manager.paths import (
    backup_file_with_rotation,
    build_paths,
    ensure_layout,
    prune_file_backups,
    read_acs_id,
)
from antigravity_cli_switcher.manager.profiles import (
    delete_account,
    rename_account,
    save_account_profile,
)
from antigravity_cli_switcher.manager.state import (
    load_state,
    save_state,
    sync_state_from_disk,
)
from antigravity_cli_switcher.manager.stealth import (
    extract_jetski_uuid,
    is_valid_uuid_string,
    read_canonical_uuid_file,
    write_canonical_uuid_file,
    write_jetski_uuid_file,
)
from antigravity_cli_switcher.manager.verification import verify_account
from antigravity_cli_switcher.models import AccountMeta


def _create_synthetic_login_artifacts(home_dir: Path, email: str = "test_user@example.com") -> None:
    gemini_cli = home_dir / ".gemini" / "antigravity-cli"
    gemini_cli.mkdir(parents=True, exist_ok=True)
    token_file = gemini_cli / "antigravity-oauth-token"
    token_data = {
        "token": {
            "access_token": "ya29.synthetic_access_token_fixture",
            "refresh_token": "1//synthetic_refresh_token_fixture",
            "expiry": "2099-01-01T00:00:00Z",
        },
        "id_token": "header.payload.signature",
        "email": email,
    }
    token_file.write_text(json.dumps(token_data), encoding="utf-8")


def test_whitespace_tolerant_reading_and_atomic_normalization(tmp_path: Path) -> None:
    test_uuid = "3f6d9554-1237-438e-b6ef-58d6a2b14445"
    target = tmp_path / "installation_id"

    # Write file with leading/trailing whitespace, uppercase, and newlines
    dirty_content = f"  \r\n {test_uuid.upper()} \n  \r\n"
    target.write_text(dirty_content, encoding="utf-8")

    # read_canonical_uuid_file should strip, validate, return lowercase, and re-save cleanly
    result = read_canonical_uuid_file(target)
    assert result == test_uuid
    assert is_valid_uuid_string(result)

    # File on disk must now be strictly 36 ASCII bytes
    normalized_bytes = target.read_bytes()
    assert len(normalized_bytes) == 36
    assert normalized_bytes == test_uuid.encode("ascii")

    # A backup of the dirty file should have been created before normalization
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("installation_id.bak")]
    assert len(backups) >= 1
    assert backups[0].read_bytes() == dirty_content.encode("utf-8")


def test_pre_mutation_backup_snapshots_and_rotation_limit(tmp_path: Path) -> None:
    target = tmp_path / "installation_id"
    initial_uuid = write_canonical_uuid_file(target)

    # Empty or missing file backup returns None
    non_existent = tmp_path / "non_existent"
    assert backup_file_with_rotation(non_existent) is None

    empty_file = tmp_path / "empty_file"
    empty_file.touch()
    assert backup_file_with_rotation(empty_file) is None

    # Writing identical content does not create spurious backups
    assert write_canonical_uuid_file(target, initial_uuid) == initial_uuid
    initial_backups = [p for p in tmp_path.iterdir() if p.name.startswith("installation_id.bak")]
    assert len(initial_backups) == 0

    # Perform 7 distinct mutations
    for _ in range(7):
        new_uuid = str(uuid.uuid4()).lower()
        write_canonical_uuid_file(target, new_uuid)

    # Max backups retained must be capped at 5
    current_backups = [p for p in tmp_path.iterdir() if p.name.startswith("installation_id.bak")]
    assert len(current_backups) == 5

    # Manual prune helper check
    pruned = prune_file_backups(target, max_backups=3)
    assert len(pruned) == 2
    remaining_backups = [p for p in tmp_path.iterdir() if p.name.startswith("installation_id.bak")]
    assert len(remaining_backups) == 3


def test_jetski_uuid_atomic_writing_and_backup(tmp_path: Path) -> None:
    jetski_file = tmp_path / "jetski_state.pbtxt"
    id_1 = str(uuid.uuid4()).lower()
    written_1 = write_jetski_uuid_file(jetski_file, id_1)
    assert written_1 == id_1
    assert extract_jetski_uuid(jetski_file.read_text(encoding="utf-8")) == id_1

    # Overwrite with id_2 creates backup and updates content
    id_2 = str(uuid.uuid4()).lower()
    written_2 = write_jetski_uuid_file(jetski_file, id_2)
    assert written_2 == id_2
    assert extract_jetski_uuid(jetski_file.read_text(encoding="utf-8")) == id_2

    backups = [p for p in tmp_path.iterdir() if p.name.startswith("jetski_state.pbtxt.bak")]
    assert len(backups) == 1
    assert extract_jetski_uuid(backups[0].read_text(encoding="utf-8")) == id_1


def test_save_account_profile_preserves_established_installation_ids(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    source_initial = tmp_path / "source_initial"
    _create_synthetic_login_artifacts(source_initial, "user1@example.com")
    save_account_profile(paths, "test_account_a", source_initial, overwrite=False)

    acc_dir = paths.accounts_dir / "test_account_a"
    gdir = acc_dir / ".gemini"

    # Established IDs
    orig_cli = read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id")
    orig_root = read_canonical_uuid_file(gdir / "installation_id")
    orig_jetski = extract_jetski_uuid((gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8"))
    orig_acs_id = read_acs_id(acc_dir)

    assert orig_cli is not None
    assert orig_root is not None
    assert orig_jetski is not None
    assert orig_acs_id is not None

    # Simulate re-login with overwrite=True from a different source dir
    source_relogin = tmp_path / "source_relogin"
    _create_synthetic_login_artifacts(source_relogin, "user1@example.com")
    save_account_profile(paths, "test_account_a", source_relogin, overwrite=True)

    # IDs must be preserved across overwrite
    new_cli = read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id")
    new_root = read_canonical_uuid_file(gdir / "installation_id")
    new_jetski = extract_jetski_uuid((gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8"))
    new_acs_id = read_acs_id(acc_dir)

    assert new_cli == orig_cli
    assert new_root == orig_root
    assert new_jetski == orig_jetski
    assert new_acs_id == orig_acs_id


def test_check_hygiene_detects_triplet_collisions(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")

    save_account_profile(paths, "test_account_1", src1)
    save_account_profile(paths, "test_account_2", src2)

    # Clean initial state has no collisions
    results = check_hygiene(paths)
    collisions = [r for r in results if "collision" in r.detail.lower()]
    assert len(collisions) == 0

    # Collide root installation_id
    acc1_gdir = paths.accounts_dir / "test_account_1" / ".gemini"
    acc2_gdir = paths.accounts_dir / "test_account_2" / ".gemini"
    shared_root_id = read_canonical_uuid_file(acc1_gdir / "installation_id")
    assert shared_root_id is not None
    write_canonical_uuid_file(acc2_gdir / "installation_id", shared_root_id)

    root_results = check_hygiene(paths)
    root_collisions = [r for r in root_results if "root installation id collision" in r.detail.lower()]
    assert len(root_collisions) == 2

    # Collide jetski installation_uuid
    shared_jetski_uuid = extract_jetski_uuid(
        (acc1_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8")
    )
    assert shared_jetski_uuid is not None
    write_jetski_uuid_file(acc2_gdir / "antigravity-cli" / "jetski_state.pbtxt", shared_jetski_uuid)

    jetski_results = check_hygiene(paths)
    jetski_collisions = [r for r in jetski_results if "jetski installation_uuid collision" in r.detail.lower()]
    assert len(jetski_collisions) == 2


def test_fix_hygiene_age_based_priority_deduplication(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")

    save_account_profile(paths, "account_older", src1)
    save_account_profile(paths, "account_newer", src2)

    # Force timestamps: account_older is older than account_newer
    state = load_state(paths)
    state["accounts"]["account_older"]["created_at"] = "2025-01-01T00:00:00Z"
    state["accounts"]["account_newer"]["created_at"] = "2026-01-01T00:00:00Z"
    save_state(paths, state)

    older_gdir = paths.accounts_dir / "account_older" / ".gemini"
    newer_gdir = paths.accounts_dir / "account_newer" / ".gemini"

    # Make newer account share older account's IDs (CLI, root, jetski)
    orig_cli = read_canonical_uuid_file(older_gdir / "antigravity-cli" / "installation_id")
    orig_root = read_canonical_uuid_file(older_gdir / "installation_id")
    orig_jetski = extract_jetski_uuid(
        (older_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8")
    )

    assert orig_cli is not None and orig_root is not None and orig_jetski is not None
    write_canonical_uuid_file(newer_gdir / "antigravity-cli" / "installation_id", orig_cli)
    write_canonical_uuid_file(newer_gdir / "installation_id", orig_root)
    write_jetski_uuid_file(newer_gdir / "antigravity-cli" / "jetski_state.pbtxt", orig_jetski)

    # Confirm collisions exist
    assert len([r for r in check_hygiene(paths) if "collision" in r.detail.lower()]) > 0

    # Run fix_hygiene
    actions = fix_hygiene(paths)
    assert any("Assigned unique CLI installation_id for 'account_newer'" in a for a in actions)
    assert any("Assigned unique root installation_id for 'account_newer'" in a for a in actions)
    assert any("Assigned unique jetski installation_uuid for 'account_newer'" in a for a in actions)

    # Older account preserves its original IDs
    assert read_canonical_uuid_file(older_gdir / "antigravity-cli" / "installation_id") == orig_cli
    assert read_canonical_uuid_file(older_gdir / "installation_id") == orig_root
    assert (
        extract_jetski_uuid((older_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8"))
        == orig_jetski
    )

    # Newer account received fresh, distinct IDs
    new_cli = read_canonical_uuid_file(newer_gdir / "antigravity-cli" / "installation_id")
    new_root = read_canonical_uuid_file(newer_gdir / "installation_id")
    new_jetski = extract_jetski_uuid(
        (newer_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8")
    )

    assert new_cli != orig_cli
    assert new_root != orig_root
    assert new_jetski != orig_jetski

    # No collisions remain
    assert len([r for r in check_hygiene(paths) if "collision" in r.detail.lower()]) == 0


def test_verification_inverted_logic_fix(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")

    save_account_profile(paths, "active_acc", src1)
    save_account_profile(paths, "standby_acc", src2)

    state = load_state(paths)
    state["active"] = "active_acc"
    save_state(paths, state)

    live_dir = paths.root / ".gemini"
    act_cli = read_canonical_uuid_file(
        paths.accounts_dir / "active_acc" / ".gemini" / "antigravity-cli" / "installation_id"
    )
    assert act_cli is not None
    write_canonical_uuid_file(live_dir / "antigravity-cli" / "installation_id", act_cli)

    # 1. Active account matching live ID -> MATCH
    active_meta = AccountMeta(enabled=True, status="active")
    ver_active = verify_account(paths, "active_acc", active_meta, state=state)
    assert ver_active.installation_id_status == "MATCH"

    # 2. Standby account with its own isolated ID -> SAVED
    standby_meta = AccountMeta(enabled=True, status="standby")
    ver_standby_isolated = verify_account(paths, "standby_acc", standby_meta, state=state)
    assert ver_standby_isolated.installation_id_status == "SAVED"

    # 3. Standby account polluted with the active account's live ID -> COLLISION (NOT MATCH!)
    standby_cli_path = paths.accounts_dir / "standby_acc" / ".gemini" / "antigravity-cli" / "installation_id"
    write_canonical_uuid_file(standby_cli_path, act_cli)
    ver_standby_polluted = verify_account(paths, "standby_acc", standby_meta, state=state)
    assert ver_standby_polluted.installation_id_status == "COLLISION"
    assert "collides with active live session" in ver_standby_polluted.summary
    assert ver_standby_polluted.recommended_action == "hygiene_fix"


def test_hybrid_dual_identity_and_disk_tag_migration(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src = tmp_path / "src"
    _create_synthetic_login_artifacts(src, "user1@example.com")
    save_account_profile(paths, "account_orig", src)

    acc_dir = paths.accounts_dir / "account_orig"
    orig_acs_id = read_acs_id(acc_dir)
    assert orig_acs_id is not None

    state = load_state(paths)
    assert state["accounts"]["account_orig"]["id"] == orig_acs_id
    state["active"] = "account_orig"
    state["switch_history"] = [{"previous_active": "prior", "switched_to": "account_orig", "active": "account_orig"}]
    state["switch_runtime"] = {"active": "account_orig", "previous_active": "prior"}
    save_state(paths, state)

    # Simulate external `mv account_orig account_renamed` on disk
    new_dir = paths.accounts_dir / "account_renamed"
    shutil.move(str(acc_dir), str(new_dir))

    # Sync state from disk should match by .acs_id and migrate without losing history
    new_state = sync_state_from_disk(paths, load_state(paths))

    assert "account_orig" not in new_state["accounts"]
    assert "account_renamed" in new_state["accounts"]
    assert new_state["accounts"]["account_renamed"]["id"] == orig_acs_id
    assert new_state["active"] == "account_renamed"
    assert new_state["switch_history"][0]["switched_to"] == "account_renamed"
    assert new_state["switch_history"][0]["active"] == "account_renamed"
    assert new_state["switch_runtime"]["active"] == "account_renamed"


def test_cascade_rename_account(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src = tmp_path / "src"
    _create_synthetic_login_artifacts(src, "user1@example.com")
    save_account_profile(paths, "alpha_account", src)

    state = load_state(paths)
    state["active"] = "alpha_account"
    state["switch_history"] = [
        {"previous_active": "beta", "switched_to": "alpha_account", "active": "alpha_account"},
        {"previous_active": "alpha_account", "switched_to": "gamma", "active": "gamma"},
    ]
    state["switch_runtime"] = {"active": "alpha_account", "previous_active": "beta"}
    save_state(paths, state)

    rename_account(paths, "alpha_account", "omega_account")

    renamed_state = load_state(paths)
    assert "alpha_account" not in renamed_state["accounts"]
    assert "omega_account" in renamed_state["accounts"]
    assert renamed_state["active"] == "omega_account"

    # History cascade
    h1 = renamed_state["switch_history"][0]
    assert h1["switched_to"] == "omega_account"
    assert h1["active"] == "omega_account"
    h2 = renamed_state["switch_history"][1]
    assert h2["previous_active"] == "omega_account"

    # Runtime cascade
    assert renamed_state["switch_runtime"]["active"] == "omega_account"


def test_cli_numeric_index_resolution(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")

    save_account_profile(paths, "acc_a", src1)
    save_account_profile(paths, "acc_b", src2)

    parser = build_parser()

    # Numeric index '1' resolves to 'acc_a' for proxy-set
    args_proxy_set = parser.parse_args(["proxy-set", "1", "http://127.0.0.1:8080", "--label", "testproxy", "--json"])
    assert dispatch_command(paths, args_proxy_set, parser) == 0
    state = load_state(paths)
    assert state["accounts"]["acc_a"]["proxy"]["url"] == "http://127.0.0.1:8080"

    # Numeric index '1' resolves for proxy-clear
    args_proxy_clear = parser.parse_args(["proxy-clear", "1", "--json"])
    assert dispatch_command(paths, args_proxy_clear, parser) == 0
    state = load_state(paths)
    assert state["accounts"]["acc_a"]["proxy"]["url"] is None

    # Numeric index '1' resolves for rename
    args_rename = parser.parse_args(["rename", "1", "acc_a_renamed", "--json"])
    assert dispatch_command(paths, args_rename, parser) == 0
    state = load_state(paths)
    assert "acc_a" not in state["accounts"]
    assert "acc_a_renamed" in state["accounts"]

    # Numeric index '1' resolves for delete
    args_delete = parser.parse_args(["delete", "1", "--json"])
    assert dispatch_command(paths, args_delete, parser) == 0
    state = load_state(paths)
    assert "acc_a_renamed" not in state["accounts"]


def test_read_canonical_uuid_file_utf8_bom(tmp_path: Path) -> None:
    test_uuid = "3f6d9554-1237-438e-b6ef-58d6a2b14445"
    target = tmp_path / "installation_id"
    # Write UTF-8 BOM + whitespace + uppercase UUID + newline
    raw_bom = b"\xef\xbb\xbf  \r\n " + test_uuid.upper().encode("ascii") + b" \n\r\n"
    target.write_bytes(raw_bom)

    result = read_canonical_uuid_file(target)
    assert result == test_uuid
    assert target.read_bytes() == test_uuid.encode("ascii")

    # Verify pre-mutation backup was created
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("installation_id.bak")]
    assert len(backups) >= 1
    assert backups[0].read_bytes() == raw_bom


def test_write_jetski_uuid_file_handles_empty_quotes_and_unquoted(tmp_path: Path) -> None:
    jetski_file = tmp_path / "jetski_state.pbtxt"
    # Case 1: empty quotes
    jetski_file.write_text('post_onboarding: {}\ninstallation_uuid: ""\nmigrations: {}\n', encoding="utf-8")
    new_uuid_1 = "11111111-2222-3333-4444-555555555555"
    assert write_jetski_uuid_file(jetski_file, new_uuid_1) == new_uuid_1
    assert extract_jetski_uuid(jetski_file.read_text(encoding="utf-8")) == new_uuid_1

    # Case 2: unquoted
    jetski_file.write_text("post_onboarding: {}\ninstallation_uuid: old-malformed\nmigrations: {}\n", encoding="utf-8")
    new_uuid_2 = "22222222-3333-4444-5555-666666666666"
    assert write_jetski_uuid_file(jetski_file, new_uuid_2) == new_uuid_2
    assert extract_jetski_uuid(jetski_file.read_text(encoding="utf-8")) == new_uuid_2


def test_programmatic_rename_and_delete_resolve_indices(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")
    save_account_profile(paths, "alpha_acc", src1)
    save_account_profile(paths, "beta_acc", src2)

    # Calling rename_account directly with numeric index "1"
    rename_account(paths, "1", "alpha_renamed")
    state = load_state(paths)
    assert "alpha_acc" not in state["accounts"]
    assert "alpha_renamed" in state["accounts"]

    # Calling delete_account directly with numeric index "2" (beta_acc is 2nd sorted)
    delete_account(paths, "2")
    state_after_delete = load_state(paths)
    assert "beta_acc" not in state_after_delete["accounts"]


def test_fix_hygiene_persists_state_and_uses_mtime_fallback(tmp_path: Path) -> None:
    import os
    import time

    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")
    save_account_profile(paths, "older_dir", src1)
    save_account_profile(paths, "newer_dir", src2)

    # Remove created_at from state to test mtime fallback
    state = load_state(paths)
    state["accounts"]["older_dir"]["created_at"] = None
    state["accounts"]["newer_dir"]["created_at"] = None
    save_state(paths, state)

    older_path = paths.accounts_dir / "older_dir"
    newer_path = paths.accounts_dir / "newer_dir"
    now = time.time()
    os.utime(older_path, (now - 500, now - 500))
    os.utime(newer_path, (now - 100, now - 100))

    # Collide their root installation_ids
    shared_id = read_canonical_uuid_file(older_path / ".gemini" / "installation_id")
    assert shared_id is not None
    write_canonical_uuid_file(newer_path / ".gemini" / "installation_id", shared_id)

    actions = fix_hygiene(paths)
    assert any("Assigned unique root installation_id for 'newer_dir'" in a for a in actions)

    # Older account preserved ID
    assert read_canonical_uuid_file(older_path / ".gemini" / "installation_id") == shared_id
    # Newer account got fresh ID
    assert read_canonical_uuid_file(newer_path / ".gemini" / "installation_id") != shared_id


def test_switch_account_does_not_kill_process_when_cooldown_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    _create_synthetic_login_artifacts(src2, "user2@example.com")
    save_account_profile(paths, "acc_active", src1)
    save_account_profile(paths, "acc_standby", src2)

    state = load_state(paths)
    state["active"] = "acc_active"
    from datetime import datetime, timezone

    state["switch_runtime"] = {"last_account_switch_at": datetime.now(timezone.utc).isoformat()}
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    kill_calls: list[str] = []

    def _mock_safe_switch() -> list[int]:
        kill_calls.append("killed")
        return [999]

    monkeypatch.setattr(
        "antigravity_cli_switcher.manager.accounts._ensure_safe_account_switch",
        _mock_safe_switch,
    )

    # Should raise cooldown exception WITHOUT calling _ensure_safe_account_switch
    with pytest.raises(RuntimeError, match="Stealth cooldown active"):
        switch_account(paths, "acc_standby", force=False)

    assert len(kill_calls) == 0

    # With force=True, process termination is executed and switch succeeds
    switch_account(paths, "acc_standby", force=True)
    assert len(kill_calls) == 1
    assert load_state(paths)["active"] == "acc_standby"

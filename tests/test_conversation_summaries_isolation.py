from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from antigravity_cli_switcher.manager.accounts import switch_account
from antigravity_cli_switcher.manager.paths import (
    MANAGED_PROFILE_FILES,
    _copy_managed_profile_files,
    _remove_managed_profile_files,
    build_paths,
    ensure_layout,
)
from antigravity_cli_switcher.manager.profiles import save_account_profile
from antigravity_cli_switcher.manager.state import load_state, save_state


def test_managed_profile_files_contains_conversation_summaries() -> None:
    assert "antigravity-cli/conversation_summaries.db" in MANAGED_PROFILE_FILES


def test_conversation_summaries_isolated_per_profile(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    src3 = tmp_path / "src3"

    for src, email in ((src1, "alpha@example.com"), (src2, "beta@example.com"), (src3, "gamma@example.com")):
        cli_dir = src / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        (cli_dir / "antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": f"tok_{email}"}}), encoding="utf-8"
        )
        (cli_dir / "installation_id").write_text("11111111-1111-4111-8111-111111111111", encoding="utf-8")
        (cli_dir / "jetski_state.pbtxt").write_text(
            'installation_uuid: "11111111-1111-4111-8111-111111111111"\n', encoding="utf-8"
        )

    # Set up conversation_summaries.db for account 1
    db1_path = src1 / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
    with sqlite3.connect(str(db1_path)) as con:
        con.execute("CREATE TABLE summaries (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO summaries VALUES (1, 'Alpha Conversation')")
        con.commit()

    # Set up conversation_summaries.db for account 2
    db2_path = src2 / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
    with sqlite3.connect(str(db2_path)) as con:
        con.execute("CREATE TABLE summaries (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO summaries VALUES (2, 'Beta Conversation')")
        con.commit()

    # Account 3 has no conversation_summaries.db

    save_account_profile(paths, "acc1", src1)
    save_account_profile(paths, "acc2", src2)
    save_account_profile(paths, "acc3", src3)

    state = load_state(paths)
    live_dir = tmp_path / "live_gemini" / ".gemini"
    state["live_dir"] = str(live_dir)
    save_state(paths, state)

    live_db = live_dir / "antigravity-cli" / "conversation_summaries.db"

    # Switch to acc1
    switch_account(paths, "acc1", force=True)
    assert live_db.is_file()
    with sqlite3.connect(str(live_db)) as con:
        row = con.execute("SELECT title FROM summaries WHERE id = 1").fetchone()
        assert row is not None
        assert row[0] == "Alpha Conversation"

    # Switch to acc2 -> must swap to acc2's DB
    switch_account(paths, "acc2", force=True)
    assert live_db.is_file()
    with sqlite3.connect(str(live_db)) as con:
        row = con.execute("SELECT title FROM summaries WHERE id = 2").fetchone()
        assert row is not None
        assert row[0] == "Beta Conversation"
        # Acc1 data must not exist in Acc2 DB
        assert con.execute("SELECT title FROM summaries WHERE id = 1").fetchone() is None

    # Switch to acc3 (no summaries db) -> must unlink live DB rather than retaining acc2's DB
    switch_account(paths, "acc3", force=True)
    assert not live_db.exists()


def test_wal_checkpoint_truncate_and_sidecar_cleanup(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    cli_dir = source_dir / "antigravity-cli"
    cli_dir.mkdir(parents=True, exist_ok=True)

    db_path = cli_dir / "conversation_summaries.db"
    # Create DB and put it in WAL mode
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE summaries (id INTEGER, title TEXT)")
    con.execute("INSERT INTO summaries VALUES (42, 'WAL Test')")
    con.commit()

    wal_path = cli_dir / "conversation_summaries.db-wal"
    assert wal_path.exists()

    # Pre-populate target with stale WAL and SHM files to verify cleanup
    target_cli = target_dir / "antigravity-cli"
    target_cli.mkdir(parents=True, exist_ok=True)
    stale_wal = target_cli / "conversation_summaries.db-wal"
    stale_shm = target_cli / "conversation_summaries.db-shm"
    stale_wal.write_text("stale_wal_data", encoding="utf-8")
    stale_shm.write_text("stale_shm_data", encoding="utf-8")

    # Copy managed profile files
    _copy_managed_profile_files(source_dir, target_dir)

    # Destination stale WAL/SHM files must have been unlinked
    assert not stale_wal.exists()
    assert not stale_shm.exists()

    # Destination DB should exist and contain the data
    dst_db = target_cli / "conversation_summaries.db"
    assert dst_db.is_file()
    con_dst = sqlite3.connect(str(dst_db))
    row = con_dst.execute("SELECT title FROM summaries WHERE id = 42").fetchone()
    assert row is not None
    assert row[0] == "WAL Test"
    con_dst.close()

    con.close()


def test_remove_managed_profile_files_cleans_db_and_wal(tmp_path: Path) -> None:
    target = tmp_path / ".gemini"
    agy_dir = target / "antigravity-cli"
    agy_dir.mkdir(parents=True, exist_ok=True)

    db = agy_dir / "conversation_summaries.db"
    wal = agy_dir / "conversation_summaries.db-wal"
    shm = agy_dir / "conversation_summaries.db-shm"
    db.write_text("db_data", encoding="utf-8")
    wal.write_text("wal_data", encoding="utf-8")
    shm.write_text("shm_data", encoding="utf-8")

    assert db.is_file()
    assert wal.is_file()
    assert shm.is_file()

    _remove_managed_profile_files(target)

    assert not db.exists()
    assert not wal.exists()
    assert not shm.exists()


def test_wal_checkpoint_locked_db_graceful_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    cli_dir = source_dir / "antigravity-cli"
    cli_dir.mkdir(parents=True, exist_ok=True)

    db_path = cli_dir / "conversation_summaries.db"
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE summaries (id INTEGER, title TEXT)")
    con.execute("INSERT INTO summaries VALUES (99, 'Locked DB')")
    con.commit()

    wal_path = cli_dir / "conversation_summaries.db-wal"
    assert wal_path.exists()

    # Simulate checkpoint failure (e.g. database locked or timeout)
    def fake_checkpoint(db: Path) -> None:
        pass

    monkeypatch.setattr("antigravity_cli_switcher.manager.paths._checkpoint_sqlite_wal", fake_checkpoint)

    _copy_managed_profile_files(source_dir, target_dir)

    target_cli = target_dir / "antigravity-cli"
    dst_db = target_cli / "conversation_summaries.db"
    dst_wal = target_cli / "conversation_summaries.db-wal"
    assert dst_db.is_file()
    assert dst_wal.is_file()

    con_dst = sqlite3.connect(str(dst_db))
    row = con_dst.execute("SELECT title FROM summaries WHERE id = 99").fetchone()
    assert row is not None
    assert row[0] == "Locked DB"
    con_dst.close()
    con.close()

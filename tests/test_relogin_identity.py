"""Tests ensuring relogin pre-seeds and preserves account identity without generating new IDs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from antigravity_cli_switcher.cli.commands import dispatch_command
from antigravity_cli_switcher.cli.parser import build_parser
from antigravity_cli_switcher.manager.login import login_account
from antigravity_cli_switcher.manager.paths import (
    account_dir,
    build_paths,
    ensure_layout,
    read_acs_id,
)
from antigravity_cli_switcher.manager.profiles import save_account_profile
from antigravity_cli_switcher.manager.state import load_state
from antigravity_cli_switcher.manager.stealth import (
    extract_jetski_uuid,
    read_canonical_uuid_file,
)


def _make_jwt(email: str) -> str:
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"email": email, "sub": "123456"}).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}.sig"


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
        "id_token": _make_jwt(email),
        "email": email,
    }
    token_file.write_text(json.dumps(token_data), encoding="utf-8")


def test_login_account_relogin_preseeds_and_preserves_established_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    # Setup initial account
    src_init = tmp_path / "src_init"
    _create_synthetic_login_artifacts(src_init, "user1@example.com")
    save_account_profile(paths, "test_account_a", src_init, overwrite=False)

    acc_dir = account_dir(paths, "test_account_a")
    gdir = acc_dir / ".gemini"

    orig_cli = read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id")
    orig_root = read_canonical_uuid_file(gdir / "installation_id")
    orig_jetski = extract_jetski_uuid((gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8"))
    orig_acs_id = read_acs_id(acc_dir)

    assert orig_cli is not None
    assert orig_root is not None
    assert orig_jetski is not None
    assert orig_acs_id is not None

    monkeypatch.setattr("os.isatty", lambda fd: True)

    verified_preseeded_identity: dict[str, str | None] = {}

    def mock_popen(*args, **kwargs):
        cwd_path = kwargs.get("cwd")
        live_gemini = cwd_path / ".gemini"

        # Verify identity files were pre-seeded in live_dir BEFORE agy runs
        verified_preseeded_identity["cli"] = read_canonical_uuid_file(
            live_gemini / "antigravity-cli" / "installation_id"
        )
        verified_preseeded_identity["root"] = read_canonical_uuid_file(live_gemini / "installation_id")
        jetski_p = live_gemini / "antigravity-cli" / "jetski_state.pbtxt"
        verified_preseeded_identity["jetski"] = (
            extract_jetski_uuid(jetski_p.read_text(encoding="utf-8")) if jetski_p.is_file() else None
        )

        # Verify old token was removed so agy will prompt for login
        token_path = live_gemini / "antigravity-cli" / "antigravity-oauth-token"
        verified_preseeded_identity["token_absent"] = "1" if not token_path.exists() else "0"

        # Simulate agy finishing authentication by writing the fresh token
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(
            json.dumps({"token": {"access_token": "ya29.relogin_fresh"}, "id_token": _make_jwt("user1@example.com")}),
            encoding="utf-8",
        )

        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    saved = login_account(paths, "test_account_a", agy_binary="/fake/agy", overwrite_existing=True)
    assert saved == "test_account_a"

    # Pre-seeding checks: live_dir had the existing IDs when agy started
    assert verified_preseeded_identity["cli"] == orig_cli
    assert verified_preseeded_identity["root"] == orig_root
    assert verified_preseeded_identity["jetski"] == orig_jetski
    assert verified_preseeded_identity["token_absent"] == "1"

    # Saved profile identity checks: IDs did not change across relogin
    assert read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id") == orig_cli
    assert read_canonical_uuid_file(gdir / "installation_id") == orig_root
    assert (
        extract_jetski_uuid((gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8"))
        == orig_jetski
    )
    assert read_acs_id(acc_dir) == orig_acs_id

    # No backup files generated due to ID churn
    bak_files = list(acc_dir.glob("*.bak*")) + list(gdir.glob("*.bak*"))
    assert len(bak_files) == 0


def test_cli_relogin_subcommand_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src_init = tmp_path / "src_init"
    _create_synthetic_login_artifacts(src_init, "user1@example.com")
    save_account_profile(paths, "alpha", src_init, overwrite=False)

    acc_dir = account_dir(paths, "alpha")
    gdir = acc_dir / ".gemini"
    orig_cli = read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id")
    assert orig_cli is not None

    captured_overwrite: list[bool] = []

    def mock_run_login(p, name, agy_bin, timeout, overwrite_existing=False):
        captured_overwrite.append(overwrite_existing)
        return name

    monkeypatch.setattr("antigravity_cli_switcher.cli.commands.run_login_with_prompt", mock_run_login)

    parser = build_parser()
    args = parser.parse_args(["relogin", "alpha"])
    code = dispatch_command(paths, args, parser)

    assert code == 0
    assert captured_overwrite == [True]


def test_save_account_profile_new_account_regenerates_colliding_ids(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    # First account
    src1 = tmp_path / "src1"
    _create_synthetic_login_artifacts(src1, "user1@example.com")
    save_account_profile(paths, "acc1", src1, overwrite=False)

    acc1_dir = account_dir(paths, "acc1")
    acc1_gdir = acc1_dir / ".gemini"
    acc1_cli = read_canonical_uuid_file(acc1_gdir / "antigravity-cli" / "installation_id")
    acc1_root = read_canonical_uuid_file(acc1_gdir / "installation_id")
    acc1_jetski = extract_jetski_uuid(
        (acc1_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8")
    )

    # Second account reusing the same source dir (would collide without de-collision)
    save_account_profile(paths, "acc2", src1, overwrite=False)

    acc2_dir = account_dir(paths, "acc2")
    acc2_gdir = acc2_dir / ".gemini"
    acc2_cli = read_canonical_uuid_file(acc2_gdir / "antigravity-cli" / "installation_id")
    acc2_root = read_canonical_uuid_file(acc2_gdir / "installation_id")
    acc2_jetski = extract_jetski_uuid(
        (acc2_gdir / "antigravity-cli" / "jetski_state.pbtxt").read_text(encoding="utf-8")
    )

    # IDs for new account must be unique and not collide with acc1
    assert acc2_cli != acc1_cli
    assert acc2_root != acc1_root
    assert acc2_jetski != acc1_jetski


def test_login_account_relogin_retains_state_id_when_disk_acs_id_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src_init = tmp_path / "src_init"
    _create_synthetic_login_artifacts(src_init, "user1@example.com")
    save_account_profile(paths, "alpha", src_init, overwrite=False)

    acc_dir = account_dir(paths, "alpha")
    state = load_state(paths)
    expected_state_id = state["accounts"]["alpha"]["id"]

    # Delete disk tag .acs_id
    (acc_dir / ".acs_id").unlink(missing_ok=True)
    assert read_acs_id(acc_dir) is None

    monkeypatch.setattr("os.isatty", lambda fd: True)

    def mock_popen(*args, **kwargs):
        cwd_path = kwargs.get("cwd")
        token_path = cwd_path / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(
            json.dumps({"token": {"access_token": "ya29.relogin_fresh"}, "id_token": _make_jwt("user1@example.com")}),
            encoding="utf-8",
        )
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    saved = login_account(paths, "alpha", agy_binary="/fake/agy", overwrite_existing=True)
    assert saved == "alpha"

    updated_state = load_state(paths)
    assert updated_state["accounts"]["alpha"]["id"] == expected_state_id
    assert read_acs_id(acc_dir) == expected_state_id

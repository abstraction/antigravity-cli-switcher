"""Comprehensive verification tests for stealth, anti-telemetry, keyring isolation, and credential security."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from antigravity_cli_switcher.manager.accounts import (
    _copy_active_runtime,
    switch_account,
)
from antigravity_cli_switcher.manager.hygiene import fix_hygiene
from antigravity_cli_switcher.manager.identity_probe import (
    probe_profile_identity_via_usage,
)
from antigravity_cli_switcher.manager.keyring import (
    _IN_MEMORY_KEYRING,
    KEYRING_ACCOUNT,
    KEYRING_SERVICE,
    _sync_keyring_to_home,
)
from antigravity_cli_switcher.manager.login import login_account
from antigravity_cli_switcher.manager.paths import (
    _copy_account_profile,
    _oauth_token_path,
    account_dir,
    build_paths,
    ensure_layout,
)
from antigravity_cli_switcher.manager.profiles import save_account_profile
from antigravity_cli_switcher.manager.quota import (
    UsageRefreshResult,
    _refresh_token_http,
    refresh_account_usage,
)
from antigravity_cli_switcher.manager.state import load_state, save_state, utc_now
from antigravity_cli_switcher.manager.stealth import read_canonical_uuid_file
from antigravity_cli_switcher.tui.app import ACSApp


def _create_synthetic_profile(home_dir: Path, email: str = "test@example.com") -> Path:
    tok_dir = home_dir / ".gemini" / "antigravity-cli"
    tok_dir.mkdir(parents=True, exist_ok=True)
    tok_file = tok_dir / "antigravity-oauth-token"
    payload = {
        "token": {
            "access_token": "ya29.synthetic_token_payload",
            "refresh_token": "1//synthetic_refresh_token",
            "expiry": "2099-01-01T00:00:00Z",
        },
        "email": email,
    }
    tok_file.write_text(json.dumps(payload), encoding="utf-8")
    return tok_file


def test_login_account_captures_and_restores_active_keyring_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src_init = tmp_path / "src_init"
    _create_synthetic_profile(src_init, "active_user@example.com")
    save_account_profile(paths, "active_acc", src_init)

    state = load_state(paths)
    state["active"] = "active_acc"
    save_state(paths, state)

    original_active_token = {"token": {"access_token": "ya29.active_token_preserved"}}
    _IN_MEMORY_KEYRING[(KEYRING_SERVICE, KEYRING_ACCOUNT)] = json.dumps(original_active_token)

    from antigravity_cli_switcher.manager import keyring as km

    orig_warmup = km._isolated_keyring_warmup
    warmup_captured_tokens: list[dict | None] = []

    def tracking_warmup(home: Path):
        warmup_captured_tokens.append(km._load_keyring_token())
        return orig_warmup(home)

    monkeypatch.setattr("antigravity_cli_switcher.manager.login._isolated_keyring_warmup", tracking_warmup)
    monkeypatch.setattr("os.isatty", lambda fd: True)

    captured_popen_envs: list[dict[str, str]] = []

    def mock_popen(*args: object, **kwargs: object) -> mock.MagicMock:
        live_cwd = Path(str(kwargs.get("cwd", "")))
        env_arg = kwargs.get("env")
        if isinstance(env_arg, dict):
            captured_popen_envs.append(dict(env_arg))
        live_tok = live_cwd / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        live_tok.parent.mkdir(parents=True, exist_ok=True)
        live_tok.write_text(
            json.dumps({"token": {"access_token": "ya29.new_token_logged_in"}, "email": "new_user@example.com"}),
            encoding="utf-8",
        )
        proc = mock.MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    saved = login_account(paths, "new_acc", agy_binary="/fake/agy", overwrite_existing=True)
    assert saved == "new_acc"

    assert len(captured_popen_envs) == 1
    assert captured_popen_envs[0].get("HOME") is not None
    assert (
        captured_popen_envs[0]["HOME"] == str(paths.root / ".gemini".split("/")[0])
        or "switcher" in captured_popen_envs[0]["HOME"]
    )

    assert len(warmup_captured_tokens) == 1
    assert warmup_captured_tokens[0] is not None
    assert warmup_captured_tokens[0]["token"]["access_token"] == "ya29.active_token_preserved"

    restored_raw = _IN_MEMORY_KEYRING.get((KEYRING_SERVICE, KEYRING_ACCOUNT))
    assert restored_raw is not None
    restored_data = json.loads(restored_raw)
    assert restored_data["token"]["access_token"] in ("ya29.active_token_preserved", "ya29.synthetic_token_payload")


def test_switch_account_terminates_process_before_quiescence_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _create_synthetic_profile(src1, "user1@example.com")
    _create_synthetic_profile(src2, "user2@example.com")
    save_account_profile(paths, "acc1", src1)
    save_account_profile(paths, "acc2", src2)

    state = load_state(paths)
    state["active"] = "acc1"
    save_state(paths, state)

    monkeypatch.setenv("ACS_ENFORCE_COOLDOWN", "1")

    kill_calls: list[str] = []

    def mock_safe_switch() -> list[int]:
        kill_calls.append("killed")
        return [77777]

    monkeypatch.setattr(
        "antigravity_cli_switcher.manager.accounts._ensure_safe_account_switch",
        mock_safe_switch,
    )

    # Successfully terminates process and switches without deadlock
    switch_account(paths, "acc2", force=False)

    assert len(kill_calls) == 1
    loaded = load_state(paths)
    assert loaded["active"] == "acc2"
    runtime = loaded.get("switch_runtime", {})
    assert runtime.get("last_process_kill_at") is not None


def test_tui_app_quiescence_always_loads_state(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    state = load_state(paths)
    state["switch_runtime"] = {"last_process_kill_at": utc_now().isoformat()}
    save_state(paths, state)

    app = ACSApp(paths=paths)
    app.record_account_switch()
    rem = app.get_switch_cooldown_remaining()
    assert rem > 0.0


def test_auto_backend_attempts_http_first_and_handles_ineligible(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    acc = "test_acc"
    _create_synthetic_profile(account_dir(paths, acc), "test@example.com")
    state = load_state(paths)
    state["accounts"][acc] = {"enabled": True, "status": "standby", "health_status": "ready"}
    save_state(paths, state)

    fake_load = {"cloudaicompanionProject": "synth-project", "planType": "GEMINI_CODE_ASSIST"}
    fake_summary = {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [{"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.9}],
            }
        ]
    }

    def fake_cloudcode(_tok: object, path: str, _body: object) -> dict:
        if "loadCodeAssist" in path:
            return fake_load
        return fake_summary

    with (
        mock.patch("antigravity_cli_switcher.manager.quota._cloudcode_request", side_effect=fake_cloudcode),
        mock.patch(
            "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
            return_value="ya29.synthetic_fresh",
        ),
        mock.patch(
            "antigravity_cli_switcher.manager.quota.check_token_account_match",
            return_value=mock.Mock(status="ok"),
        ),
        mock.patch("antigravity_cli_switcher.manager.quota._fetch_native_quota") as mock_native,
    ):
        res = refresh_account_usage(paths, acc, backend="auto")
        assert res.backend == "http"
        assert res.short_usage_value == 90.0
        mock_native.assert_not_called()

    with (
        mock.patch(
            "antigravity_cli_switcher.manager.quota._cloudcode_request",
            side_effect=ValueError("Eligibility check failed: Account ineligible"),
        ),
        mock.patch(
            "antigravity_cli_switcher.manager.quota._ensure_fresh_access_token",
            return_value="ya29.synthetic_fresh",
        ),
        mock.patch(
            "antigravity_cli_switcher.manager.quota.check_token_account_match",
            return_value=mock.Mock(status="ok"),
        ),
        mock.patch("antigravity_cli_switcher.manager.quota._fetch_native_quota") as mock_native,
    ):
        with pytest.raises(ValueError, match="Eligibility check failed"):
            refresh_account_usage(paths, acc, backend="auto")
        mock_native.assert_not_called()


def test_copy_active_runtime_ensures_identity_files_before_copy(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    acc = "acc_identity"
    acc_dir = account_dir(paths, acc)
    _create_synthetic_profile(acc_dir, "ident@example.com")

    gdir = acc_dir / ".gemini"
    assert not (gdir / "antigravity-cli" / "installation_id").exists()
    assert not (gdir / "installation_id").exists()

    _copy_active_runtime(paths, acc)

    assert read_canonical_uuid_file(gdir / "antigravity-cli" / "installation_id") is not None
    assert read_canonical_uuid_file(gdir / "installation_id") is not None
    rt_gdir = paths.runtime_dir / ".gemini"
    assert read_canonical_uuid_file(rt_gdir / "antigravity-cli" / "installation_id") is not None
    assert read_canonical_uuid_file(rt_gdir / "installation_id") is not None


def test_probe_profile_identity_via_usage_does_not_mutate_runtime_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_home = tmp_path / "live_home"
    live_gemini = live_home / ".gemini"
    live_gemini.mkdir(parents=True)
    sentinel = live_gemini / "sentinel.txt"
    sentinel.write_text("must_not_be_modified", encoding="utf-8")

    standby_dir = tmp_path / "standby"
    _create_synthetic_profile(standby_dir, "standby@example.com")

    captured_cwds: list[Path] = []

    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = Path(str(kwargs.get("cwd", "")))
        captured_cwds.append(cwd)
        assert cwd != live_home
        assert not (cwd / ".gemini" / "sentinel.txt").exists()
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Logged in as standby@example.com",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    result = probe_profile_identity_via_usage(
        standby_dir,
        agy_binary="/fake/agy",
        live_dir=live_gemini,
    )

    assert result["account_name"] == "standby@example.com"
    assert sentinel.read_text(encoding="utf-8") == "must_not_be_modified"
    assert len(captured_cwds) == 1
    assert captured_cwds[0] != live_home

    # Also test probing live_gemini directly: must not delete or alter files
    _create_synthetic_profile(live_home, "live@example.com")
    result_live = probe_profile_identity_via_usage(
        live_gemini,
        agy_binary="/fake/agy",
        live_dir=live_gemini,
    )
    assert result_live["account_name"] == "standby@example.com"
    assert sentinel.read_text(encoding="utf-8") == "must_not_be_modified"


def test_antigravity_oauth_token_permissions_0o600(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)
    acc = "acc_perms"
    acc_path = account_dir(paths, acc)
    tok_path = _create_synthetic_profile(acc_path, "perms@example.com")

    # Paths copy permissions
    _copy_account_profile(acc_path, paths.runtime_dir)
    rt_tok = _oauth_token_path(paths.runtime_dir)
    assert stat.S_IMODE(rt_tok.stat().st_mode) == 0o600

    # Keyring sync permissions
    target_home = tmp_path / "target_home"
    _sync_keyring_to_home(target_home)
    # If token synced:
    target_tok = _oauth_token_path(target_home)
    if target_tok.is_file():
        assert stat.S_IMODE(target_tok.stat().st_mode) == 0o600

    # Quota HTTP refresh permissions
    data = {"token": {"refresh_token": "dummy", "access_token": "dummy"}}
    tok_path.write_text(json.dumps(data), encoding="utf-8")
    with mock.patch("urllib.request.urlopen") as mock_url:
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps({"access_token": "new", "expires_in": 3600}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp
        _refresh_token_http(acc_path)
    assert stat.S_IMODE(tok_path.stat().st_mode) == 0o600

    # Hygiene remediation permissions: both authentic tokens with 0o644 and synthetic tokens
    state = load_state(paths)
    state["active"] = acc
    save_state(paths, state)
    live_dir = paths.root / ".gemini"
    live_tok = _oauth_token_path(live_dir.parent)
    live_tok.parent.mkdir(parents=True, exist_ok=True)
    live_tok.write_text(json.dumps({"token": {"access_token": "ya29.auth_token"}}), encoding="utf-8")
    live_tok.chmod(0o644)
    tok_path.chmod(0o644)
    assert stat.S_IMODE(live_tok.stat().st_mode) == 0o644

    fix_hygiene(paths)
    assert stat.S_IMODE(tok_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(live_tok.stat().st_mode) == 0o600


def test_usage_refresh_result_and_cli_backend_default_http(tmp_path: Path) -> None:
    paths = build_paths(tmp_path / "switcher")
    ensure_layout(paths)

    res = UsageRefreshResult(
        account="acc_test",
        source_home="/tmp/fake",
        project_id=None,
        plan_type=None,
        prompt_credits_available=None,
        prompt_credits_monthly=None,
        short_usage_status="available",
        short_usage_value=100.0,
        short_reset_at=None,
        weekly_usage_status="available",
        weekly_usage_value=100.0,
        weekly_reset_at=None,
        usage_families={},
        bucket_count=1,
    )
    assert res.backend == "http"

    from antigravity_cli_switcher.cli.commands import dispatch_command
    from antigravity_cli_switcher.cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(["quota-backend", "--json"])
    with mock.patch("builtins.print") as mock_print:
        code = dispatch_command(paths, args, parser)
        assert code == 0
        called_out = mock_print.call_args[0][0]
        parsed = json.loads(called_out)
        assert parsed["quota_backend"] == "http"

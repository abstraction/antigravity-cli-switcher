from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from antigravity_cli_switcher.cli import main
from antigravity_cli_switcher.manager import (
    account_dir,
    build_paths,
    ensure_layout,
    load_state,
    save_state,
)


@pytest.fixture
def mock_root(tmp_path: Path) -> Path:
    paths = build_paths(tmp_path)
    ensure_layout(paths)

    acct1 = account_dir(paths, "alpha")
    token_dir1 = acct1 / ".gemini" / "antigravity-cli"
    token_dir1.mkdir(parents=True, exist_ok=True)
    (token_dir1 / "antigravity-oauth-token").write_text(
        json.dumps(
            {
                "token": {"access_token": "ya29.fake1", "refresh_token": "1//fake1", "expiry": "2030-01-01T00:00:00Z"},
                "auth_method": "oauth",
            }
        ),
        encoding="utf-8",
    )

    acct2 = account_dir(paths, "[2] beta")
    token_dir2 = acct2 / ".gemini" / "antigravity-cli"
    token_dir2.mkdir(parents=True, exist_ok=True)
    (token_dir2 / "antigravity-oauth-token").write_text(
        json.dumps(
            {
                "token": {"access_token": "ya29.fake2", "refresh_token": "1//fake2", "expiry": "2030-01-01T00:00:00Z"},
                "auth_method": "oauth",
            }
        ),
        encoding="utf-8",
    )

    state = load_state(paths)
    state["active"] = "alpha"
    state["accounts"]["alpha"] = {
        "enabled": True,
        "status": "active",
        "plan_type": "pro",
        "expected_email": "alpha@example.com",
    }
    state["accounts"]["[2] beta"] = {
        "enabled": True,
        "status": "standby",
        "plan_type": "free",
        "expected_email": "beta@example.com",
    }
    save_state(paths, state)
    return tmp_path


def test_cli_main_callable() -> None:
    assert callable(main)


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_headless_list_does_not_import_textual(mock_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("textual")}
    for k in saved_modules:
        sys.modules.pop(k, None)

    try:
        assert "textual" not in sys.modules
        code = main(["--root", str(mock_root), "list", "--json"])
        assert code == 0
        assert "textual" not in sys.modules
    finally:
        sys.modules.update(saved_modules)


def test_cli_set_email(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 1. Set email text output
    code = main(["--root", str(mock_root), "set-email", "alpha", "updated@example.com"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "set-email: alpha -> updated@example.com" in out

    paths = build_paths(mock_root)
    state = load_state(paths)
    assert state["accounts"]["alpha"]["expected_email"] == "updated@example.com"

    # 2. Set email with resolution by numeric index
    code = main(["--root", str(mock_root), "set-email", "2", "beta-new@example.com"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "set-email: [2] beta -> beta-new@example.com" in out

    state = load_state(paths)
    assert state["accounts"]["[2] beta"]["expected_email"] == "beta-new@example.com"

    # 3. Clear email via --json
    code = main(["--root", str(mock_root), "set-email", "alpha", "--json"])
    assert code == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["account"] == "alpha"
    assert data["expected_email"] is None

    state = load_state(paths)
    assert "expected_email" not in state["accounts"]["alpha"]


def test_cli_switch_account(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = build_paths(mock_root)

    # Switch to [2] beta using stripped name "beta"
    code = main(["--root", str(mock_root), "switch", "beta"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "switched: alpha -> [2] beta" in out

    state = load_state(paths)
    assert state["active"] == "[2] beta"

    # Switch back using exact name
    code = main(["--root", str(mock_root), "switch", "alpha"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "switched: [2] beta -> alpha" in out

    # Switch using index "2"
    code = main(["--root", str(mock_root), "switch", "2"])
    assert code == 0
    state = load_state(paths)
    assert state["active"] == "[2] beta"

    # Switch to non-existent account returns non-zero code
    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(mock_root), "switch", "non-existent"])
    assert exc_info.value.code == 2
    _, err = capsys.readouterr()
    assert "Account not found: non-existent" in err


def test_cli_status(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_root), "status"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "active: alpha" in out

    code = main(["--root", str(mock_root), "status", "--json"])
    assert code == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["active"] == "alpha"
    assert "accounts" in data


def test_cli_current(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_root), "current"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert out.strip() == "alpha"


def test_cli_disable_enable(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = build_paths(mock_root)

    code = main(["--root", str(mock_root), "disable", "2"])
    assert code == 0
    state = load_state(paths)
    assert state["accounts"]["[2] beta"]["enabled"] is False

    code = main(["--root", str(mock_root), "enable", "2"])
    assert code == 0
    state = load_state(paths)
    assert state["accounts"]["[2] beta"]["enabled"] is True


def test_cli_login_accepts_bracketed_alias_and_overwrite(
    mock_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, bool]] = []

    def mock_run_login(paths: object, name: str, binary: object, timeout: int, overwrite_existing: bool = False) -> str:
        calls.append((name, overwrite_existing))
        return name

    monkeypatch.setattr("antigravity_cli_switcher.cli.commands.run_login_with_prompt", mock_run_login)

    # Multi-token without quotes: acs login [2] beta -f
    code = main(["--root", str(mock_root), "login", "[2]", "beta", "-f"])
    assert code == 0
    assert len(calls) == 1
    assert calls[0] == ("[2] beta", True)

    out, _ = capsys.readouterr()
    assert "logged-in: [2] beta" in out


def test_cli_switch_policy_options(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--root",
            str(mock_root),
            "switch-policy",
            "--candidate-strategy",
            "highest-short",
            "--family-fallback-strategy",
            "strict-family",
            "--json",
        ]
    )
    assert code == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["candidate_strategy"] == "highest-short"
    assert data["family_fallback_strategy"] == "strict-family"


def test_cli_resolve_route_and_ensure_active_claude(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(mock_root), "ensure-active", "--family", "claude", "--json"])
    assert code == 0
    capsys.readouterr()

    code = main(
        [
            "--root",
            str(mock_root),
            "resolve-route",
            "claude",
            "--fallback-strategy",
            "same-family-first",
            "--json",
        ]
    )
    assert code == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["preferred_family"] == "other"


def test_cli_quota_backend_get_and_set(mock_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 1. Get default backend
    code = main(["--root", str(mock_root), "quota-backend"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert out.strip() == "http"

    # 2. Get default backend via JSON
    code = main(["--root", str(mock_root), "quota-backend", "--json"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert json.loads(out) == {"quota_backend": "http"}

    # 3. Set to native
    code = main(["--root", str(mock_root), "quota-backend", "native"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "quota-backend: native" in out

    # Verify state updated
    paths = build_paths(mock_root)
    state = load_state(paths)
    assert state["quota_backend"] == "native"

    # 4. Set to auto via JSON
    code = main(["--root", str(mock_root), "quota-backend", "auto", "--json"])
    assert code == 0
    out, _ = capsys.readouterr()
    assert json.loads(out) == {"quota_backend": "auto"}
    state = load_state(paths)
    assert state["quota_backend"] == "auto"


def test_cli_refresh_usage_with_backend_flag(mock_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_cli_switcher.manager.quota import UsageRefreshResult

    called_kwargs: dict[str, object] = {}

    def mock_refresh(paths, name, agy_binary=None, timeout_seconds=30, backend=None):
        called_kwargs["account"] = name
        called_kwargs["backend"] = backend
        return UsageRefreshResult(
            account=name,
            source_home=str(paths.root),
            project_id=None,
            plan_type="free",
            prompt_credits_available=None,
            prompt_credits_monthly=None,
            short_usage_status="known",
            short_usage_value=90.0,
            short_reset_at=None,
            weekly_usage_status="known",
            weekly_usage_value=85.0,
            weekly_reset_at=None,
            usage_families={},
            bucket_count=0,
            backend=backend or "native",
        )

    monkeypatch.setattr("antigravity_cli_switcher.cli.refresh.refresh_account_usage", mock_refresh)

    code = main(["--root", str(mock_root), "refresh-usage", "alpha", "--backend", "http", "--json"])
    assert code == 0
    assert called_kwargs["backend"] == "http"
    assert called_kwargs["account"] == "alpha"


def test_cli_refresh_due_with_backend_flag(mock_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_cli_switcher.manager.quota import UsageRefreshResult

    called_kwargs: dict[str, object] = {}

    def mock_refresh_due(paths, agy_binary=None, timeout_seconds=30, backend=None):
        called_kwargs["backend"] = backend
        return UsageRefreshResult(
            account="alpha",
            source_home=str(paths.root),
            project_id=None,
            plan_type="free",
            prompt_credits_available=None,
            prompt_credits_monthly=None,
            short_usage_status="known",
            short_usage_value=90.0,
            short_reset_at=None,
            weekly_usage_status="known",
            weekly_usage_value=85.0,
            weekly_reset_at=None,
            usage_families={},
            bucket_count=0,
            backend=backend or "native",
        )

    monkeypatch.setattr("antigravity_cli_switcher.cli.refresh.refresh_due_account", mock_refresh_due)

    code = main(["--root", str(mock_root), "refresh-due", "--backend", "http", "--json"])
    assert code == 0
    assert called_kwargs["backend"] == "http"


def test_cli_refresh_all_with_backend_flag(mock_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from antigravity_cli_switcher.manager.paths import build_paths
    from antigravity_cli_switcher.manager.quota import UsageRefreshResult
    from antigravity_cli_switcher.manager.state import load_state, save_state

    called_backends: list[str | None] = []

    def mock_refresh(paths, name, agy_binary=None, timeout_seconds=30, backend=None):
        called_backends.append(backend)
        return UsageRefreshResult(
            account=name,
            source_home=str(paths.root),
            project_id=None,
            plan_type="free",
            prompt_credits_available=None,
            prompt_credits_monthly=None,
            short_usage_status="known",
            short_usage_value=90.0,
            short_reset_at=None,
            weekly_usage_status="known",
            weekly_usage_value=85.0,
            weekly_reset_at=None,
            usage_families={},
            bucket_count=0,
            backend=backend or "native",
        )

    monkeypatch.setattr("antigravity_cli_switcher.cli.refresh.refresh_account_usage", mock_refresh)

    paths = build_paths(mock_root)
    state = load_state(paths)
    state["accounts"]["test_account"] = {"enabled": True, "status": "standby"}
    save_state(paths, state)

    code = main(["--root", str(mock_root), "refresh-all", "--backend", "auto", "--json"])
    assert code == 0
    assert "auto" in called_backends

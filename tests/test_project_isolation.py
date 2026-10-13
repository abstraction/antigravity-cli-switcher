from __future__ import annotations

import json
from pathlib import Path

from antigravity_cli_switcher.manager.accounts import switch_account
from antigravity_cli_switcher.manager.paths import (
    MANAGED_PROFILE_FILES,
    _remove_managed_profile_files,
    build_paths,
    ensure_layout,
)
from antigravity_cli_switcher.manager.profiles import save_account_profile
from antigravity_cli_switcher.manager.state import load_state, save_state


def test_managed_profile_files_contains_project_cache() -> None:
    assert "antigravity-cli/cache/default_project_id.txt" in MANAGED_PROFILE_FILES
    assert "antigravity-cli/cache/projects.json" in MANAGED_PROFILE_FILES


def test_project_id_and_projects_cache_isolated_per_profile(tmp_path: Path) -> None:
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

    # Configure account 1 with proj-alpha
    cache1 = src1 / ".gemini" / "antigravity-cli" / "cache"
    cache1.mkdir(parents=True, exist_ok=True)
    (cache1 / "default_project_id.txt").write_text("proj-alpha\n", encoding="utf-8")
    (cache1 / "projects.json").write_text(json.dumps({"projects": ["proj-alpha"]}), encoding="utf-8")

    # Configure account 2 with proj-beta
    cache2 = src2 / ".gemini" / "antigravity-cli" / "cache"
    cache2.mkdir(parents=True, exist_ok=True)
    (cache2 / "default_project_id.txt").write_text("proj-beta\n", encoding="utf-8")
    (cache2 / "projects.json").write_text(json.dumps({"projects": ["proj-beta"]}), encoding="utf-8")

    # Account 3 has no project cache files

    save_account_profile(paths, "acc1", src1)
    save_account_profile(paths, "acc2", src2)
    save_account_profile(paths, "acc3", src3)

    state = load_state(paths)
    live_dir = tmp_path / "live_gemini" / ".gemini"
    state["live_dir"] = str(live_dir)
    save_state(paths, state)

    live_cache = live_dir / "antigravity-cli" / "cache"
    live_proj_file = live_cache / "default_project_id.txt"
    live_projs_json = live_cache / "projects.json"

    # Switch to acc1
    switch_account(paths, "acc1", force=True)
    assert live_proj_file.is_file()
    assert live_proj_file.read_text(encoding="utf-8").strip() == "proj-alpha"
    assert live_projs_json.is_file()
    assert "proj-alpha" in live_projs_json.read_text(encoding="utf-8")

    # Switch to acc2 -> must overwrite with proj-beta
    switch_account(paths, "acc2", force=True)
    assert live_proj_file.is_file()
    assert live_proj_file.read_text(encoding="utf-8").strip() == "proj-beta"
    assert live_projs_json.is_file()
    assert "proj-beta" in live_projs_json.read_text(encoding="utf-8")

    # Switch to acc3 (no project cache) -> must unlink live cache files rather than retaining acc2
    switch_account(paths, "acc3", force=True)
    assert not live_proj_file.exists()
    assert not live_projs_json.exists()


def test_remove_managed_profile_files_unlinks_project_cache(tmp_path: Path) -> None:
    target = tmp_path / ".gemini"
    cache = target / "antigravity-cli" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    p1 = cache / "default_project_id.txt"
    p2 = cache / "projects.json"
    p1.write_text("test-project", encoding="utf-8")
    p2.write_text("{}", encoding="utf-8")

    assert p1.is_file()
    assert p2.is_file()

    _remove_managed_profile_files(target)

    assert not p1.exists()
    assert not p2.exists()

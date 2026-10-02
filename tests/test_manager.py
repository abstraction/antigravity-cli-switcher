from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from antigravity_cli_switcher.manager import (
    account_dir,
    build_paths,
    delete_account,
    ensure_layout,
    format_plan_type_compact,
    format_plan_type_label,
    load_state,
    save_state,
)


def _make_fake_account(paths, name: str) -> None:
    """Create a minimal account directory with a fake oauth token and register it in state."""
    acct = account_dir(paths, name)
    token_path = acct / ".gemini" / "antigravity-cli"
    token_path.mkdir(parents=True, exist_ok=True)
    (token_path / "antigravity-oauth-token").write_text('{"token": "fake"}', encoding="utf-8")

    with paths.state_file.open(encoding="utf-8") as f:
        state = json.load(f)
    state["accounts"][name] = {"enabled": True, "status": "standby"}
    save_state(paths, state)


class DeleteAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.paths = build_paths(Path(self._tmp))
        ensure_layout(self.paths)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Happy paths
    # ------------------------------------------------------------------

    def test_removes_account_from_state_and_disk(self) -> None:
        _make_fake_account(self.paths, "alice")
        delete_account(self.paths, "alice")

        state = load_state(self.paths)
        self.assertNotIn("alice", state["accounts"])
        self.assertFalse(account_dir(self.paths, "alice").exists())

    def test_returns_false_for_non_active_account(self) -> None:
        _make_fake_account(self.paths, "bob")
        was_active = delete_account(self.paths, "bob")
        self.assertFalse(was_active)

    def test_other_accounts_unaffected(self) -> None:
        _make_fake_account(self.paths, "carol")
        _make_fake_account(self.paths, "dave")
        delete_account(self.paths, "carol")

        state = load_state(self.paths)
        self.assertNotIn("carol", state["accounts"])
        self.assertIn("dave", state["accounts"])
        self.assertTrue(account_dir(self.paths, "dave").exists())

    # ------------------------------------------------------------------
    # Active-account edge cases
    # ------------------------------------------------------------------

    def test_returns_true_and_nulls_active_when_deleting_active_account(self) -> None:
        _make_fake_account(self.paths, "eve")
        state = load_state(self.paths)
        state["active"] = "eve"
        save_state(self.paths, state)

        was_active = delete_account(self.paths, "eve")

        self.assertTrue(was_active)
        state = load_state(self.paths)
        self.assertIsNone(state["active"])
        self.assertNotIn("eve", state["accounts"])

    def test_non_active_delete_does_not_change_active_pointer(self) -> None:
        _make_fake_account(self.paths, "frank")
        _make_fake_account(self.paths, "grace")
        state = load_state(self.paths)
        state["active"] = "frank"
        save_state(self.paths, state)

        delete_account(self.paths, "grace")

        state = load_state(self.paths)
        self.assertEqual(state["active"], "frank")

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_raises_for_nonexistent_account(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            delete_account(self.paths, "ghost")
        self.assertIn("Account not found", str(ctx.exception))

    def test_raises_for_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            delete_account(self.paths, "")

    def test_raises_for_whitespace_only_name(self) -> None:
        with self.assertRaises(ValueError):
            delete_account(self.paths, "   ")

    # ------------------------------------------------------------------
    # Resilience
    # ------------------------------------------------------------------

    def test_delete_raises_if_dir_already_gone_and_state_evicted(self) -> None:
        """sync_state_from_disk evicts accounts whose directory has already been removed.
        Calling delete_account after that should raise ValueError (the account is already gone).
        This documents the expected behavior: manual rm of the directory is treated as removal.
        """
        _make_fake_account(self.paths, "heidi")
        shutil.rmtree(account_dir(self.paths, "heidi"))

        with self.assertRaises(ValueError):
            delete_account(self.paths, "heidi")


if __name__ == "__main__":
    unittest.main()


class PlanTypeLabelTests(unittest.TestCase):
    def test_known_pro(self):
        self.assertEqual(format_plan_type_label("GEMINI_CODE_ASSIST"), "Google AI Pro")
        self.assertEqual(format_plan_type_compact("GEMINI_CODE_ASSIST"), "Pro")

    def test_known_free(self):
        self.assertEqual(format_plan_type_compact("GEMINI_CODE_ASSIST_STARTER"), "Free")

    def test_known_ultra(self):
        self.assertEqual(format_plan_type_compact("GOOGLE_AI_ULTRA"), "Ultra")

    def test_unknown_falls_through(self):
        label = format_plan_type_compact("SOME_NEW_TIER_2027")
        self.assertLessEqual(len(label), 6)

    def test_none_returns_placeholder(self):
        self.assertEqual(format_plan_type_compact(None), "?")
        self.assertEqual(format_plan_type_label(None), "-")

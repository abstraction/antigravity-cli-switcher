from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["ACS_TEST_MODE"] = "1"

from antigravity_cli_switcher.cli import _problem_badge
from antigravity_cli_switcher.manager import (
    _IN_MEMORY_KEYRING,
    _clear_keyring_token,
    _is_test_environment,
    _load_keyring_token,
    _oauth_token_path,
    _save_keyring_token,
    account_dir,
    build_paths,
    check_hygiene,
    default_live_dir,
    default_root,
    ensure_layout,
    fix_hygiene,
    is_synthetic_or_test_token,
    load_state,
    save_state,
    verify_account,
)


def _make_jwt(email: str, sub: str = "123456789", name: str = "Test User") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "email": email,
                    "sub": sub,
                    "name": name,
                    "email_verified": True,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"{header}.{payload}.sig"


class HygieneTokenDetectionTests(unittest.TestCase):
    def test_synthetic_token_signatures_detected(self) -> None:
        # Access token with ya29.fake
        self.assertTrue(
            is_synthetic_or_test_token(
                {
                    "token": {"access_token": "ya29.fake_token", "refresh_token": "1//real"},
                }
            )
        )

        # Refresh token with 1//fake
        self.assertTrue(
            is_synthetic_or_test_token(
                {
                    "token": {"access_token": "ya29.real_looking", "refresh_token": "1//fake"},
                }
            )
        )

        # Email wrong@gmail.com in id_token
        self.assertTrue(
            is_synthetic_or_test_token(
                {
                    "token": {"access_token": "ya29.real", "refresh_token": "1//real"},
                    "id_token": _make_jwt("wrong@gmail.com"),
                }
            )
        )

        # Email test@gmail.com in id_token
        self.assertTrue(
            is_synthetic_or_test_token(
                {
                    "token": {"access_token": "ya29.real", "refresh_token": "1//real"},
                    "id_token": _make_jwt("test@gmail.com"),
                }
            )
        )

        # Domain @example.com in id_token
        self.assertTrue(
            is_synthetic_or_test_token(
                {
                    "token": {"access_token": "ya29.real", "refresh_token": "1//real"},
                    "id_token": _make_jwt("developer@example.com"),
                }
            )
        )

    def test_authentic_token_not_flagged(self) -> None:
        real_jwt = _make_jwt("authentic_user@gmail.com")
        self.assertFalse(
            is_synthetic_or_test_token(
                {
                    "token": {
                        "access_token": "ya29.a0synthetic_valid_sample_token_fixture",
                        "refresh_token": "1//00112233445566778899aabbccddeeff",
                    },
                    "id_token": real_jwt,
                }
            )
        )

    def test_none_or_empty_returns_false(self) -> None:
        self.assertFalse(is_synthetic_or_test_token(None))
        self.assertFalse(is_synthetic_or_test_token({}))
        self.assertFalse(is_synthetic_or_test_token(""))


class KeyringIsolationAndGuardsTests(unittest.TestCase):
    def setUp(self) -> None:
        _IN_MEMORY_KEYRING.clear()

    def tearDown(self) -> None:
        _IN_MEMORY_KEYRING.clear()

    def test_test_environment_is_detected(self) -> None:
        self.assertTrue(_is_test_environment())

    def test_keyring_operations_use_in_memory_store_in_test_env(self) -> None:
        dummy_data = {"token": {"access_token": "test-tok"}, "id_token": "test-id"}
        self.assertTrue(_save_keyring_token(dummy_data))
        loaded = _load_keyring_token()
        self.assertEqual(loaded, dummy_data)
        _clear_keyring_token()
        self.assertIsNone(_load_keyring_token())

    def test_save_blocks_synthetic_when_outside_test_mode(self) -> None:
        synthetic_token = {
            "token": {"access_token": "ya29.fake"},
            "id_token": _make_jwt("wrong@gmail.com"),
        }
        with patch("antigravity_cli_switcher.manager.keyring._is_test_environment", return_value=False):
            with patch("subprocess.run") as mock_proc:
                saved = _save_keyring_token(synthetic_token)
                self.assertFalse(saved)
                # Verify secret-tool was never invoked
                mock_proc.assert_not_called()


class StorageIsolationTests(unittest.TestCase):
    def test_default_live_dir_isolates_custom_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_root = Path(tmpdir)
            live_dir = default_live_dir(custom_root)
            self.assertEqual(live_dir, custom_root / ".gemini")
            self.assertNotEqual(live_dir, default_live_dir(default_root()))


class HygieneAuditAndFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp()
        self.paths = build_paths(Path(self.tmp_dir))
        ensure_layout(self.paths)
        _IN_MEMORY_KEYRING.clear()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        _IN_MEMORY_KEYRING.clear()

    def test_audit_clean_environment(self) -> None:
        # Create an authentic account
        acct = account_dir(self.paths, "acc1")
        tok_path = _oauth_token_path(acct)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        real_tok = {
            "token": {"access_token": "ya29.authentic123", "refresh_token": "1//real123"},
            "id_token": _make_jwt("realuser@gmail.com"),
        }
        tok_path.write_text(json.dumps(real_tok), encoding="utf-8")

        state = load_state(self.paths)
        state["accounts"]["acc1"] = {"enabled": True, "status": "active"}
        state["active"] = "acc1"
        save_state(self.paths, state)

        results = check_hygiene(self.paths)
        acct_results = [r for r in results if r.target == "acc1"]
        self.assertEqual(len(acct_results), 1)
        self.assertEqual(acct_results[0].status, "clean")
        self.assertIn("realuser@gmail.com", acct_results[0].detail)

    def test_audit_flags_contaminated_account(self) -> None:
        acct = account_dir(self.paths, "bad_acc")
        tok_path = _oauth_token_path(acct)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        synth_tok = {
            "token": {"access_token": "ya29.fake", "refresh_token": "1//fake"},
            "id_token": _make_jwt("wrong@gmail.com"),
        }
        tok_path.write_text(json.dumps(synth_tok), encoding="utf-8")

        state = load_state(self.paths)
        state["accounts"]["bad_acc"] = {"enabled": True, "status": "standby"}
        save_state(self.paths, state)

        results = check_hygiene(self.paths)
        bad_results = [r for r in results if r.target == "bad_acc"]
        self.assertEqual(len(bad_results), 1)
        self.assertEqual(bad_results[0].status, "contaminated")
        self.assertIn("wrong@gmail.com", bad_results[0].detail)
        self.assertIsNotNone(bad_results[0].remediation)

    def test_verify_account_marks_synthetic_badge(self) -> None:
        acct = account_dir(self.paths, "synth_acc")
        tok_path = _oauth_token_path(acct)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        synth_tok = {
            "token": {"access_token": "ya29.fake", "refresh_token": "1//fake"},
            "id_token": _make_jwt("wrong@gmail.com"),
        }
        tok_path.write_text(json.dumps(synth_tok), encoding="utf-8")

        from antigravity_cli_switcher.models import AccountMeta

        meta = AccountMeta(enabled=True, status="standby")
        verification = verify_account(self.paths, "synth_acc", meta)
        self.assertEqual(verification.problem_status.value, "synthetic_token")
        self.assertEqual(_problem_badge(verification.problem_status.value), "SYNTH")

    def test_fix_hygiene_clears_synthetic_from_stores(self) -> None:
        # Create active account with clean token
        acct = account_dir(self.paths, "active_clean")
        tok_path = _oauth_token_path(acct)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        clean_tok = {
            "token": {"access_token": "ya29.authentic456", "refresh_token": "1//real456"},
            "id_token": _make_jwt("clean@gmail.com"),
        }
        tok_path.write_text(json.dumps(clean_tok), encoding="utf-8")

        state = load_state(self.paths)
        state["accounts"]["active_clean"] = {"enabled": True, "status": "active"}
        state["active"] = "active_clean"
        save_state(self.paths, state)

        # Contaminate runtime dir
        rt_tok = _oauth_token_path(self.paths.runtime_dir)
        rt_tok.parent.mkdir(parents=True, exist_ok=True)
        synth_tok = {
            "token": {"access_token": "ya29.fake"},
            "id_token": _make_jwt("wrong@gmail.com"),
        }
        rt_tok.write_text(json.dumps(synth_tok), encoding="utf-8")

        # Contaminate in-memory keyring
        _save_keyring_token(synth_tok)

        # Run fix_hygiene
        actions = fix_hygiene(self.paths)
        self.assertTrue(len(actions) > 0)

        # Verify runtime token was replaced with active account's authentic token
        rt_data = json.loads(rt_tok.read_text(encoding="utf-8"))
        self.assertFalse(is_synthetic_or_test_token(rt_data))
        self.assertEqual(rt_data["token"]["access_token"], "ya29.authentic456")

        # Verify keyring token was restored with active account's authentic token
        ktok = _load_keyring_token()
        self.assertIsNotNone(ktok)
        self.assertFalse(is_synthetic_or_test_token(ktok))


if __name__ == "__main__":
    unittest.main()

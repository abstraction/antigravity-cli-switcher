import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from antigravity_cli_switcher.manager import _isolated_keyring_warmup


class TestKeyringIsolation(unittest.TestCase):
    @patch("antigravity_cli_switcher.manager.keyring._load_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._sync_home_to_keyring")
    @patch("antigravity_cli_switcher.manager.keyring._sync_keyring_to_home")
    @patch("antigravity_cli_switcher.manager.keyring._save_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._clear_keyring_token")
    def test_isolated_keyring_warmup_restores_previous_state(
        self, mock_clear, mock_save, mock_sync_out, mock_sync_in, mock_load
    ):
        mock_load.return_value = {"token": "active_token"}
        target_home = Path("/fake/home")

        with _isolated_keyring_warmup(target_home):
            mock_sync_in.assert_called_once_with(target_home)
            mock_sync_out.assert_not_called()
            mock_save.assert_not_called()

        mock_sync_out.assert_called_once_with(target_home)
        mock_save.assert_called_once_with({"token": "active_token"})
        mock_clear.assert_called_once()

    @patch("antigravity_cli_switcher.manager.keyring._load_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._sync_home_to_keyring")
    @patch("antigravity_cli_switcher.manager.keyring._sync_keyring_to_home")
    @patch("antigravity_cli_switcher.manager.keyring._save_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._clear_keyring_token")
    def test_isolated_keyring_warmup_clears_if_previously_empty(
        self, mock_clear, mock_save, mock_sync_out, mock_sync_in, mock_load
    ):
        mock_load.return_value = None
        target_home = Path("/fake/home")

        with _isolated_keyring_warmup(target_home):
            mock_sync_in.assert_called_once_with(target_home)

        mock_sync_out.assert_called_once_with(target_home)
        mock_save.assert_not_called()
        self.assertEqual(mock_clear.call_count, 2)

    @patch("antigravity_cli_switcher.manager.keyring._load_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._sync_home_to_keyring")
    @patch("antigravity_cli_switcher.manager.keyring._sync_keyring_to_home")
    @patch("antigravity_cli_switcher.manager.keyring._save_keyring_token")
    @patch("antigravity_cli_switcher.manager.keyring._clear_keyring_token")
    def test_isolated_keyring_warmup_handles_exceptions(
        self, mock_clear, mock_save, mock_sync_out, mock_sync_in, mock_load
    ):
        mock_load.return_value = {"token": "active_token"}
        target_home = Path("/fake/home")

        try:
            with _isolated_keyring_warmup(target_home):
                raise ValueError("Exception during warmup")
        except ValueError:
            pass

        mock_sync_out.assert_called_once_with(target_home)
        mock_save.assert_called_once_with({"token": "active_token"})


if __name__ == "__main__":
    unittest.main()

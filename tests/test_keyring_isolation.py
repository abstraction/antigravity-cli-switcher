import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from antigravity_cli_switcher.manager import _isolated_keyring_warmup


class TestKeyringIsolation(unittest.TestCase):
    def test_isolated_keyring_warmup_sets_empty_dbus_and_restores(self) -> None:
        target_home = Path("/fake/home")
        orig_dbus = "unix:path=/run/user/1000/bus"
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = orig_dbus

        with (
            patch("antigravity_cli_switcher.manager.keyring._sync_home_to_keyring") as mock_sync_in,
            patch("antigravity_cli_switcher.manager.keyring._sync_keyring_to_home") as mock_sync_out,
            patch("antigravity_cli_switcher.manager.keyring._save_keyring_token") as mock_save,
            patch("antigravity_cli_switcher.manager.keyring._clear_keyring_token") as mock_clear,
        ):
            with _isolated_keyring_warmup(target_home):
                self.assertEqual(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "")

            # Ensure host keyring was never mutated
            mock_sync_in.assert_not_called()
            mock_sync_out.assert_not_called()
            mock_save.assert_not_called()
            mock_clear.assert_not_called()

        self.assertEqual(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), orig_dbus)

    def test_isolated_keyring_warmup_unsets_dbus_if_previously_unset(self) -> None:
        target_home = Path("/fake/home")
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)

        with _isolated_keyring_warmup(target_home):
            self.assertEqual(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "")

        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", os.environ)

    def test_isolated_keyring_warmup_restores_on_exception(self) -> None:
        target_home = Path("/fake/home")
        orig_dbus = "unix:path=/run/user/1000/bus"
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = orig_dbus

        try:
            with _isolated_keyring_warmup(target_home):
                self.assertEqual(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "")
                raise RuntimeError("something failed inside warmup")
        except RuntimeError:
            pass

        self.assertEqual(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), orig_dbus)


if __name__ == "__main__":
    unittest.main()

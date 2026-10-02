import json
import shutil
import tempfile
import unittest
from pathlib import Path

from antigravity_cli_switcher.migrate import (
    count_files_and_dotfiles,
    execute_migration,
    scan_migration,
)


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="acs_test_migrate_")
        self.test_path = Path(self.test_dir)
        self.legacy_root = self.test_path / "legacy_manager"
        self.target_root = self.test_path / "new_switcher"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _setup_mock_legacy(self):
        # Create legacy directory structure with accounts and hidden dotfiles
        self.legacy_root.mkdir(parents=True)
        (self.legacy_root / "runtime").mkdir()
        (self.legacy_root / "logs").mkdir()

        # State file
        state = {
            "active": "alpha",
            "accounts": {"alpha": {}, "beta": {}},
            "live_dir": "/tmp/live",
        }
        (self.legacy_root / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Root dotfile
        (self.legacy_root / ".log-watch.tmp").write_text("temp", encoding="utf-8")

        # Account alpha with .gemini and nested dotfile
        acc_alpha = self.legacy_root / "accounts" / "alpha"
        alpha_gemini = acc_alpha / ".gemini" / "antigravity-cli"
        alpha_gemini.mkdir(parents=True)
        (alpha_gemini / "antigravity-oauth-token").write_text("token-alpha", encoding="utf-8")
        alpha_config = acc_alpha / ".gemini" / "config"
        alpha_config.mkdir(parents=True)
        (alpha_config / ".migrated").write_text("1", encoding="utf-8")

        # Account beta
        acc_beta = self.legacy_root / "accounts" / "beta"
        beta_gemini = acc_beta / ".gemini" / "antigravity-cli"
        beta_gemini.mkdir(parents=True)
        (beta_gemini / "antigravity-oauth-token").write_text("token-beta", encoding="utf-8")

    def test_count_files_and_dotfiles(self):
        self._setup_mock_legacy()
        total_files, dotfiles, _ = count_files_and_dotfiles(self.legacy_root)
        self.assertGreater(total_files, 0)
        self.assertGreater(dotfiles, 0)
        # We know there are .log-watch.tmp, .gemini (dirs), .migrated, etc.
        self.assertGreaterEqual(dotfiles, 4)

    def test_scan_unmigrated(self):
        self._setup_mock_legacy()
        # Set target as a symlink pointing to legacy (mirroring user's machine)
        self.target_root.symlink_to(self.legacy_root)

        scan = scan_migration(legacy_root=self.legacy_root, target_root=self.target_root)
        self.assertTrue(scan.legacy_exists)
        self.assertFalse(scan.legacy_is_symlink)
        self.assertTrue(scan.target_is_symlink)
        self.assertTrue(scan.target_points_to_legacy)
        self.assertFalse(scan.already_migrated)
        self.assertEqual(len(scan.accounts), 2)
        self.assertTrue(scan.accounts[0].has_gemini)
        self.assertTrue(scan.accounts[0].has_token)

    def test_dry_run_leaves_disk_untouched(self):
        self._setup_mock_legacy()
        self.target_root.symlink_to(self.legacy_root)

        scan = scan_migration(legacy_root=self.legacy_root, target_root=self.target_root)
        result = execute_migration(scan, dry_run=True, backup=True)

        self.assertTrue(result.success)
        self.assertEqual(result.migrated_accounts, 2)
        # Verify target is still a symlink and legacy is still a directory
        self.assertTrue(self.target_root.is_symlink())
        self.assertTrue(self.legacy_root.is_dir())
        self.assertFalse(self.legacy_root.is_symlink())

    def test_execute_migration_success(self):
        self._setup_mock_legacy()
        self.target_root.symlink_to(self.legacy_root)

        scan = scan_migration(legacy_root=self.legacy_root, target_root=self.target_root)
        result = execute_migration(scan, dry_run=False, backup=True)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(Path(result.backup_path).is_dir())

        # Target should now be a real directory
        self.assertTrue(self.target_root.is_dir())
        self.assertFalse(self.target_root.is_symlink())

        # Legacy should now be a symlink pointing to target
        self.assertTrue(self.legacy_root.is_symlink())
        self.assertEqual(self.legacy_root.resolve(), self.target_root.resolve())

        # Verify all dotfiles in target
        alpha_token = (
            self.target_root / "accounts" / "alpha" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        )
        alpha_migrated = self.target_root / "accounts" / "alpha" / ".gemini" / "config" / ".migrated"
        self.assertTrue(alpha_token.is_file())
        self.assertEqual(alpha_token.read_text(), "token-alpha")
        self.assertTrue(alpha_migrated.is_file())

        # Re-scanning should now report already_migrated
        scan2 = scan_migration(legacy_root=self.legacy_root, target_root=self.target_root)
        self.assertTrue(scan2.already_migrated)

    def test_shell_alias_update(self):
        self._setup_mock_legacy()
        mock_bashrc = self.test_path / ".bashrc"
        mock_bashrc.write_text("export FOO=1\nalias acm='agy-cli-manager'\nalias bar=2\n")

        scan = scan_migration(legacy_root=self.legacy_root, target_root=self.target_root)
        scan.shell_aliases = [
            {
                "file": str(mock_bashrc),
                "line_number": 2,
                "line": "alias acm='agy-cli-manager'",
            }
        ]

        result = execute_migration(scan, dry_run=False, backup=False, update_shell=True)
        self.assertTrue(result.success)
        self.assertEqual(len(result.shell_updated), 1)

        updated_text = mock_bashrc.read_text()
        self.assertIn("alias acs='antigravity-cli-switcher'", updated_text)
        self.assertNotIn("alias acm='agy-cli-manager'", updated_text)


if __name__ == "__main__":
    unittest.main()

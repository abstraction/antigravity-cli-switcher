import unittest

from antigravity_cli_switcher.models import (
    AccountMeta,
    AccountVerification,
    StatusSnapshot,
)


class TestPydanticModels(unittest.TestCase):
    def test_account_meta_defaults(self) -> None:
        meta = AccountMeta()
        self.assertTrue(meta.enabled)
        self.assertEqual(meta.status, "standby")
        self.assertEqual(meta.fail_count, 0)
        self.assertEqual(meta.refresh_policy_seconds, 300)
        self.assertEqual(meta.proxy.status, "ok")

    def test_account_meta_from_dict_extra_ignored(self) -> None:
        data = {
            "enabled": True,
            "status": "active",
            "plan_type": "pro",
            "fail_count": 2,
            "identity": {"email": "test@example.com", "name": "Tester"},
            "proxy": {"enabled": True, "url": "http://proxy.local:8080"},
            "unknown_extra_field": "should_not_crash",
        }
        meta = AccountMeta.model_validate(data)
        self.assertEqual(meta.status, "active")
        self.assertEqual(meta.plan_type, "pro")
        self.assertEqual(meta.fail_count, 2)
        self.assertIsNotNone(meta.identity)
        if meta.identity:
            self.assertEqual(meta.identity.email, "test@example.com")
        self.assertTrue(meta.proxy.enabled)

    def test_status_snapshot_parsing(self) -> None:
        snap_data = {
            "root": "/tmp/root",
            "runtime_dir": "/tmp/root/runtime",
            "lock_file": "/tmp/root/lock",
            "active": "alpha",
            "accounts": {
                "alpha": {
                    "enabled": True,
                    "status": "active",
                    "plan_type": "pro",
                },
                "beta": {
                    "enabled": False,
                    "status": "standby",
                },
            },
        }
        snapshot = StatusSnapshot.model_validate(snap_data)
        self.assertEqual(snapshot.active, "alpha")
        self.assertEqual(len(snapshot.accounts), 2)
        self.assertTrue(snapshot.accounts["alpha"].enabled)
        self.assertFalse(snapshot.accounts["beta"].enabled)

    def test_account_verification_defaults(self) -> None:
        ver = AccountVerification()
        self.assertEqual(ver.problem_status, "ok")
        self.assertEqual(ver.recommended_action, "none")
        self.assertEqual(ver.summary, "Ready for use.")


if __name__ == "__main__":
    unittest.main()

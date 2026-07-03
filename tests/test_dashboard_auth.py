"""dashboard 帳號/認證/可見性測試。不碰真實裝置。"""
import os
import sys
import unittest
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import dashboard_settings as ds


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_creates_admin_from_env(self):
        os.environ["MUSHROOM_DASHBOARD_USER"] = "bossman"
        os.environ["MUSHROOM_DASHBOARD_PASS"] = "pw123456"
        try:
            data = ds.load_settings()
        finally:
            del os.environ["MUSHROOM_DASHBOARD_USER"]
            del os.environ["MUSHROOM_DASHBOARD_PASS"]
        self.assertEqual(data["accounts"][0]["username"], "bossman")
        self.assertTrue(data["accounts"][0]["is_admin"])
        self.assertIsNotNone(ds.verify_login("bossman", "pw123456"))
        self.assertIsNone(ds.verify_login("bossman", "wrong"))

    def test_application_flow(self):
        ds.load_settings()
        self.assertIsNone(ds.create_application("newguy", "secret12"))
        self.assertIsNotNone(ds.create_application("newguy", "x"))  # 重名拒絕
        self.assertIsNotNone(ds.create_application("a", "x"))  # 名稱太短
        self.assertIsNone(ds.verify_login("newguy", "secret12"))  # pending 不可登入
        self.assertEqual(ds.pending_count(), 1)
        ds.approve_account("newguy", ["emulator-5554"])
        acct = ds.verify_login("newguy", "secret12")
        self.assertEqual(acct["visible_devices"], ["emulator-5554"])
        self.assertEqual(ds.pending_count(), 0)

    def test_reject_deletes_pending(self):
        ds.load_settings()
        ds.create_application("newguy", "secret12")
        ds.reject_account("newguy")
        self.assertEqual(ds.pending_count(), 0)
        self.assertIsNone(ds.create_application("newguy", "secret12"))  # 名字釋出

    def test_last_admin_guard(self):
        data = ds.load_settings()
        admin = data["accounts"][0]["username"]
        self.assertIsNotNone(ds.delete_account(admin))
        self.assertIsNotNone(ds.set_admin(admin, False))

    def test_corrupt_file_raises(self):
        with open(ds.settings_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        with self.assertRaises(ds.SettingsCorruptError):
            ds.load_settings()

    def test_host_role_roundtrip(self):
        ds.load_settings()
        self.assertIsNone(ds.get_host_role())
        ds.set_host_role("worker", "http://10.0.0.1:5002")
        self.assertEqual(ds.get_host_role()["mode"], "worker")
        ds.set_host_role(None, None)
        self.assertIsNone(ds.get_host_role())


if __name__ == "__main__":
    unittest.main()

"""dashboard 帳號/認證/可見性測試。不碰真實裝置。"""
import importlib
import os
import sys
import types
import unittest
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import dashboard_settings as ds


def _install_lightweight_stubs():
    """Stub the ADB / detector / CNN heavy modules so ``control_panel_app``
    imports headless (mirrors tests/test_worker_routes_integration.py)."""
    for name, attrs in (
        ("adb_operations", {"run_adb": lambda *a, **k: ""}),
        ("game_state.detector", {"stage_by_str": lambda d, ocr, img: "unknown"}),
        ("new_cnn.cnn_model", {"load_cnn_model": lambda path: None}),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))

    def tearDown(self):
        # 還原 module-global 設定路徑，避免污染後續測試（守門會讀 settings）。
        ds.set_settings_path(self._orig_settings_path)
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


class TestAuthGuard(unittest.TestCase):
    """全站 before_request 守門 + 登入/申請/登出流程。"""

    @classmethod
    def setUpClass(cls):
        _install_lightweight_stubs()
        existing = sys.modules.get("control_panel_app")
        if existing is not None and not hasattr(existing, "app"):
            del sys.modules["control_panel_app"]
        cls.cpa = importlib.import_module("control_panel_app")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))
        ds.load_settings()
        ds.create_account("boss", "pw123456", True, [])
        ds.create_account("viewer", "pw123456", False, ["emulator-5554"])
        self.client = self.cpa.app.test_client()

    def tearDown(self):
        # 還原 module-global 設定路徑，避免污染後續測試（守門會讀 settings）。
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def test_page_redirects_to_login_when_anonymous(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_api_returns_401_when_anonymous(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 401)

    def test_worker_endpoints_exempt(self):
        r = self.client.post("/api/report_status", json={})
        self.assertNotEqual(r.status_code, 401)

    def test_login_logout_cycle(self):
        r = self.client.post("/login", data={"username": "boss", "password": "pw123456"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.client.get("/logout")
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_pending_cannot_login(self):
        ds.create_application("waiting", "pw123456")
        r = self.client.post("/login", data={"username": "waiting", "password": "pw123456"})
        self.assertEqual(r.status_code, 200)  # 留在登入頁帶錯誤訊息
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_apply_rate_limit(self):
        for i in range(5):
            self.client.post("/apply", data={"username": f"user_{i}", "password": "pw123456"})
        r = self.client.post("/apply", data={"username": "user_x", "password": "pw123456"})
        self.assertEqual(r.status_code, 429)

    def test_corrupt_settings_fail_closed(self):
        with open(ds.settings_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 503)


class TestVisibility(unittest.TestCase):
    """裝置可見性過濾：/api/status 出口過濾 + 控制端點 403。"""

    @classmethod
    def setUpClass(cls):
        _install_lightweight_stubs()
        existing = sys.modules.get("control_panel_app")
        if existing is not None and not hasattr(existing, "app"):
            del sys.modules["control_panel_app"]
        cls.cpa = importlib.import_module("control_panel_app")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))
        ds.load_settings()
        ds.create_account("boss", "pw123456", True, [])
        ds.create_account("viewer", "pw123456", False, ["emulator-5554"])
        # stub bot_state.get_all_states 回傳兩台裝置（不碰真實裝置）
        import bot_state
        self._orig_get_all_states = bot_state.get_all_states
        bot_state.get_all_states = lambda: {
            "emulator-5554": {"status": "RUNNING", "logs": []},
            "emulator-5556": {"status": "RUNNING", "logs": []},
        }
        self.client = self.cpa.app.test_client()

    def tearDown(self):
        import bot_state
        bot_state.get_all_states = self._orig_get_all_states
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def _login(self, username, admin=None):
        if admin is None:
            admin = username == "boss"
        with self.client.session_transaction() as sess:
            sess["dash_user"] = username
            sess["dash_admin"] = admin

    def test_admin_sees_all(self):
        self._login("boss")
        bots = self.client.get("/api/status").get_json()["bots"]
        self.assertIn("emulator-5554", bots)
        self.assertIn("emulator-5556", bots)

    def test_viewer_sees_only_assigned(self):
        self._login("viewer")  # visible=["emulator-5554"]
        bots = self.client.get("/api/status").get_json()["bots"]
        self.assertIn("emulator-5554", bots)
        self.assertNotIn("emulator-5556", bots)

    def test_viewer_control_forbidden_on_hidden_device(self):
        self._login("viewer")
        r = self.client.post("/api/pause/emulator-5556")
        self.assertEqual(r.status_code, 403)

    def test_viewer_control_allowed_on_visible_device(self):
        self._login("viewer")
        r = self.client.post("/api/pause/emulator-5554")
        self.assertNotEqual(r.status_code, 403)

    def test_composite_worker_key_matches_real_id(self):
        self._login("viewer")
        r = self.client.post("/api/pause/laptop_worker:emulator-5554")
        self.assertNotEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()

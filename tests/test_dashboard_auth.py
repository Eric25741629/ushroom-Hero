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


class TestMountTrackerPublicExemption(unittest.TestCase):
    """坐騎追蹤刻意公開：頁面 + 檢視/編輯 API 免登入；管理操作（toggle/rebootstrap）仍受守門。

    直接測 ``check_request_auth``（僅讀 request.path/session，豁免分支先於 settings 讀取），
    不 import ``control_panel_app`` 也不碰 store / 設定檔，純 hermetic。
    """

    def _auth_unauthenticated(self, path):
        import flask
        from control_panel.shared.auth import check_request_auth
        app = flask.Flask(__name__)
        with app.test_request_context(path):     # 無 session = 未登入
            return check_request_auth()

    def test_public_paths_bypass_login(self):
        for path in ("/mount-tracker", "/api/mount_tracker/results",
                     "/api/mount_tracker/targets", "/api/mount_tracker/mark"):
            self.assertIsNone(self._auth_unauthenticated(path), path)  # None = 放行

    def test_admin_ops_not_public(self):
        from control_panel.shared import auth
        # 需登入的操作端點都不得公開：管理操作 + 立即全部刷新（避免路人 spam 催掃）。
        self.assertNotIn("/api/mount_tracker/toggle", auth.PUBLIC_PATHS)
        self.assertNotIn("/api/mount_tracker/rebootstrap", auth.PUBLIC_PATHS)
        self.assertNotIn("/api/mount_tracker/scan_now", auth.PUBLIC_PATHS)
        # 分享搶奪車位卡到家族（借帳號 + 發家族）亦須登入，絕不公開給路人。
        self.assertNotIn("/api/mount_tracker/rally", auth.PUBLIC_PATHS)


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

    def test_viewer_config_write_forbidden_on_hidden_device(self):
        # 集中式守門涵蓋 control/status 以外的所有 <ip> 端點（此處為 config POST）。
        self._login("viewer")
        r = self.client.post("/api/config/emulator-5556", json={"enable_farm": True})
        self.assertEqual(r.status_code, 403)

    def test_viewer_config_read_allowed_on_visible_device(self):
        self._login("viewer")  # visible=["emulator-5554"]
        r = self.client.get("/api/config/emulator-5554")
        self.assertNotEqual(r.status_code, 403)

    def test_admin_config_read_not_forbidden_on_any_device(self):
        self._login("boss")
        r = self.client.get("/api/config/emulator-5556")
        self.assertNotEqual(r.status_code, 403)


class TestAdminApi(unittest.TestCase):
    """總後台 API：帳號 CRUD / 審核 / 可見裝置 / 主機角色。管理員專屬。"""

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
        ds.load_settings()  # 產生預設 admin（migration）
        ds.create_account("boss", "pw123456", True, [])
        ds.create_account("viewer", "pw123456", False, ["emulator-5554"])
        self.client = self.cpa.app.test_client()

    def tearDown(self):
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def _login(self, username, admin):
        with self.client.session_transaction() as sess:
            sess["dash_user"] = username
            sess["dash_admin"] = admin

    # --- 權限 ---
    def test_non_admin_gets_403_on_any_admin_route(self):
        self._login("viewer", False)
        for method, path in (
            ("get", "/api/admin/accounts"),
            ("post", "/api/admin/accounts"),
            ("get", "/api/admin/pending_count"),
            ("get", "/api/admin/host_role"),
            ("post", "/api/admin/host_role"),
            ("delete", "/api/admin/accounts/boss"),
            ("post", "/api/admin/accounts/boss/password"),
            ("post", "/api/admin/accounts/boss/visible_devices"),
            ("post", "/api/admin/accounts/boss/admin"),
            ("post", "/api/admin/accounts/boss/approve"),
            ("post", "/api/admin/accounts/boss/reject"),
        ):
            r = getattr(self.client, method)(path, json={})
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    # --- /admin 頁 route 存在性 + 權限 gating ---
    def test_admin_page_renders_for_admin_and_gates_non_admin(self):
        self._login("boss", True)
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self._login("viewer", False)
        # 頁面（非 /api/）非管理員由 require_admin redirect ``/``。
        self.assertIn(self.client.get("/admin").status_code, (302, 403))

    # --- list ---
    def test_list_accounts_excludes_hash(self):
        self._login("boss", True)
        data = self.client.get("/api/admin/accounts").get_json()
        self.assertEqual(data["status"], "ok")
        names = {a["username"] for a in data["accounts"]}
        self.assertIn("boss", names)
        self.assertIn("viewer", names)
        for acct in data["accounts"]:
            self.assertNotIn("password_hash", acct)

    # --- create ---
    def test_create_account(self):
        self._login("boss", True)
        r = self.client.post(
            "/api/admin/accounts",
            json={"username": "fresh", "password": "pw123456",
                  "is_admin": False, "visible_devices": ["emulator-5560"]},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")
        self.assertIsNotNone(ds.verify_login("fresh", "pw123456"))

    def test_create_duplicate_returns_400(self):
        self._login("boss", True)
        r = self.client.post(
            "/api/admin/accounts",
            json={"username": "boss", "password": "pw123456",
                  "is_admin": False, "visible_devices": []},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "error")

    # --- password / visible_devices / admin ---
    def test_set_password(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/viewer/password",
                             json={"password": "newpw123"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(ds.verify_login("viewer", "newpw123"))

    def test_set_visible_devices(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/viewer/visible_devices",
                             json={"visible_devices": ["emulator-5556"]})
        self.assertEqual(r.status_code, 200)
        acct = next(a for a in ds.list_accounts() if a["username"] == "viewer")
        self.assertEqual(acct["visible_devices"], ["emulator-5556"])

    def test_set_admin(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/viewer/admin",
                             json={"is_admin": True})
        self.assertEqual(r.status_code, 200)
        acct = next(a for a in ds.list_accounts() if a["username"] == "viewer")
        self.assertTrue(acct["is_admin"])

    def test_unknown_username_returns_400(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/nobody/password",
                             json={"password": "pw123456"})
        self.assertEqual(r.status_code, 400)

    # --- approve / reject ---
    def test_approve_pending(self):
        ds.create_application("waiting", "pw123456")
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/waiting/approve",
                             json={"visible_devices": ["emulator-5554"]})
        self.assertEqual(r.status_code, 200)
        acct = ds.verify_login("waiting", "pw123456")
        self.assertIsNotNone(acct)
        self.assertEqual(acct["visible_devices"], ["emulator-5554"])

    def test_reject_pending(self):
        ds.create_application("waiting", "pw123456")
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/waiting/reject", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ds.pending_count(), 0)

    def test_approve_nonexistent_returns_400(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/accounts/ghost/approve",
                             json={"visible_devices": []})
        self.assertEqual(r.status_code, 400)

    # --- pending_count ---
    def test_pending_count(self):
        ds.create_application("waiting", "pw123456")
        self._login("boss", True)
        data = self.client.get("/api/admin/pending_count").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["count"], 1)

    # --- host_role ---
    def test_host_role_roundtrip(self):
        self._login("boss", True)
        data = self.client.get("/api/admin/host_role").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIsNone(data["override"])
        r = self.client.post("/api/admin/host_role",
                             json={"mode": "worker", "master_url": "http://10.0.0.1:5002"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("note", r.get_json())
        data = self.client.get("/api/admin/host_role").get_json()
        self.assertEqual(data["override"]["mode"], "worker")
        self.assertEqual(data["effective"]["mode"], "worker")
        self.assertEqual(data["effective"]["source"], "override")
        # 清除覆寫
        self.client.post("/api/admin/host_role", json={"mode": "", "master_url": ""})
        self.assertIsNone(ds.get_host_role())

    def test_host_role_partial_override_falls_back_for_missing_key(self):
        self._login("boss", True)
        base = self.client.get("/api/admin/host_role").get_json()["effective"]
        # 只覆寫 mode，master_url 留空 → effective master_url 應落回 base 值，非 null。
        self.client.post("/api/admin/host_role",
                         json={"mode": "worker", "master_url": ""})
        eff = self.client.get("/api/admin/host_role").get_json()["effective"]
        self.assertEqual(eff["mode"], "worker")
        self.assertEqual(eff["master_url"], base["master_url"])
        self.assertEqual(eff["source"], "override")

    def test_host_role_invalid_mode_returns_400(self):
        self._login("boss", True)
        r = self.client.post("/api/admin/host_role",
                             json={"mode": "garbage", "master_url": ""})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "error")
        self.assertIsNone(ds.get_host_role())  # 不合法值不得寫入

    def test_host_role_clear_via_empty_body(self):
        self._login("boss", True)
        ds.set_host_role("worker", "http://10.0.0.1:5002")
        r = self.client.post("/api/admin/host_role", json={})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(ds.get_host_role())

    # --- delete / last-admin guard ---
    def test_delete_account(self):
        self._login("boss", True)
        r = self.client.delete("/api/admin/accounts/viewer")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("viewer", {a["username"] for a in ds.list_accounts()})

    def test_delete_last_admin_returns_400(self):
        self._login("boss", True)
        # 預設 migration admin + boss 共兩名管理員；先刪 boss 再刪最後一名。
        default_admin = next(
            a["username"] for a in ds.list_accounts()
            if a.get("is_admin") and a["username"] != "boss"
        )
        self.assertEqual(self.client.delete("/api/admin/accounts/boss").status_code, 200)
        r = self.client.delete(f"/api/admin/accounts/{default_admin}")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "error")


class TestHostRoleOverride(unittest.TestCase):
    """config_manager.get_global_config() 讀 dashboard host_role 覆寫。

    優先序：dashboard host_role override > host_settings > global 預設。
    fail-open：settings 檔壞掉時絕不能讓 get_global_config() raise（主程式啟動保護）。
    """

    def setUp(self):
        import json as _json

        import config_manager

        self.config_manager = config_manager
        self.tmp = tempfile.TemporaryDirectory()

        # 沙箱 dashboard settings。
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))
        ds.load_settings()

        # 沙箱 bot_config.json：已知 base（mode=master），無 host_settings 命中。
        self._orig_cfg_file = config_manager.CONFIG_FILE
        cfg_path = os.path.join(self.tmp.name, "bot_config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            _json.dump(
                {"devices": {}, "global": {
                    "mode": "master",
                    "master_url": "http://127.0.0.1:5002",
                    "host_settings": {},
                }},
                f,
            )
        config_manager.CONFIG_FILE = cfg_path
        config_manager._invalidate_config_cache()

    def tearDown(self):
        self.config_manager.CONFIG_FILE = self._orig_cfg_file
        self.config_manager._invalidate_config_cache()
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def test_override_applies(self):
        self.assertEqual(self.config_manager.get_global_config()["mode"], "master")
        ds.set_host_role("worker", "http://10.0.0.1:5002")
        cfg = self.config_manager.get_global_config()
        self.assertEqual(cfg["mode"], "worker")
        self.assertEqual(cfg["master_url"], "http://10.0.0.1:5002")

    def test_clear_override_restores_base(self):
        ds.set_host_role("worker", "http://10.0.0.1:5002")
        self.assertEqual(self.config_manager.get_global_config()["mode"], "worker")
        ds.set_host_role(None, None)
        self.assertEqual(self.config_manager.get_global_config()["mode"], "master")

    def test_partial_override_keeps_base_for_empty_key(self):
        # 只覆寫 mode，master_url 留空 → master_url 落回 base 值，非被清掉。
        ds.set_host_role("worker", "")
        cfg = self.config_manager.get_global_config()
        self.assertEqual(cfg["mode"], "worker")
        self.assertEqual(cfg["master_url"], "http://127.0.0.1:5002")

    def test_corrupt_settings_does_not_raise(self):
        with open(ds.settings_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        # fail-open：設定檔壞掉沿用 base，絕不 raise。
        cfg = self.config_manager.get_global_config()
        self.assertEqual(cfg["mode"], "master")


if __name__ == "__main__":
    unittest.main()

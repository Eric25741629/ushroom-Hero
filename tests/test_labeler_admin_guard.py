"""labeler / trainer / ocr_config 變更端點的 require_admin 守門測試。

覆蓋 audit P1（SECURITY-TIGHTEN）：所有觸發 run/training 或寫設定的
``/api/labeler/*`` / ``/api/trainer/*`` / ``/api/ocr_config`` POST 端點僅限管理員；
唯讀 GET 與純連線探測（check_connection）維持登入即可。

harness 與 tests/test_dashboard_auth.py 相同：stub 掉 ADB/detector/CNN 重模組，
沙箱 dashboard_settings，用 session_transaction 直接登入。副作用（設定寫入 /
worker thread / flag 檔）在 admin-pass 測試中一律 monkeypatch 成 no-op。
"""
import importlib
import os
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_manager
from utils import dashboard_settings as ds


def _install_lightweight_stubs():
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


# 所有應被 require_admin 守住的變更端點。
MUTATING = (
    ("post", "/api/labeler/config"),
    ("post", "/api/labeler/run_once"),
    ("post", "/api/labeler/pause"),
    ("post", "/api/labeler/resume"),
    ("post", "/api/labeler/terminate"),
    ("post", "/api/trainer/run_once"),
    ("post", "/api/ocr_config"),
)

# 維持登入即可（未被守門）的唯讀 / 探測端點——確認沒有過度收緊。
READ_ONLY = (
    ("get", "/api/labeler/config"),
    ("get", "/api/labeler/status"),
    ("get", "/api/trainer/status"),
    ("get", "/api/ocr_config"),
    ("post", "/api/labeler/check_connection"),
)


class TestLabelerAdminGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_lightweight_stubs()
        existing = sys.modules.get("control_panel_app")
        if existing is not None and not hasattr(existing, "app"):
            del sys.modules["control_panel_app"]
        cls.cpa = importlib.import_module("control_panel_app")
        cls.rl = importlib.import_module("control_panel.routes_labeler")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))
        ds.load_settings()
        ds.create_account("boss", "pw123456", True, [])
        ds.create_account("viewer", "pw123456", False, ["emulator-5554"])
        self.client = self.cpa.app.test_client()

        # 中和副作用，讓 admin-pass 測試不真的寫設定 / 起 thread / 打網路。
        self._restore = []

        def _patch(obj, name, value):
            orig = getattr(obj, name)
            self._restore.append((obj, name, orig))
            setattr(obj, name, value)

        _patch(config_manager, "update_ocr_config", lambda *a, **k: None)
        _patch(self.rl, "_run_labeler_once_worker", lambda *a, **k: None)
        _patch(self.rl, "_run_trainer_worker", lambda *a, **k: None)
        _patch(self.rl, "_labeler_control_dir",
               os.path.join(self.tmp.name, "labeler_control"))
        _patch(self.rl, "_check_llama_endpoint",
               lambda *a, **k: (False, "stub"))

    def tearDown(self):
        for obj, name, orig in reversed(self._restore):
            setattr(obj, name, orig)
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def _login(self, username, admin):
        with self.client.session_transaction() as sess:
            sess["dash_user"] = username
            sess["dash_admin"] = admin

    def test_non_admin_gets_403_on_every_mutating_endpoint(self):
        self._login("viewer", False)
        for method, path in MUTATING:
            r = getattr(self.client, method)(path, json={})
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    def test_admin_not_forbidden_on_mutating_endpoints(self):
        self._login("boss", True)
        for method, path in MUTATING:
            r = getattr(self.client, method)(path, json={})
            self.assertNotEqual(r.status_code, 403, f"{method} {path}")

    def test_read_only_endpoints_stay_open_to_non_admin(self):
        self._login("viewer", False)
        for method, path in READ_ONLY:
            r = getattr(self.client, method)(path, json={})
            self.assertNotEqual(r.status_code, 403, f"{method} {path}")

    def test_anonymous_still_blocked_by_site_login(self):
        # 未登入：全站 before_request 應回 401（非 403），確認守門疊加正確。
        for method, path in MUTATING:
            r = getattr(self.client, method)(path, json={})
            self.assertEqual(r.status_code, 401, f"{method} {path}")


if __name__ == "__main__":
    unittest.main()

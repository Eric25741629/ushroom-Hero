"""POST /api/config/<ip> 後 _device_role_id lru_cache 失效（audit B8）
+ /api/device_data 路由已刪除（audit D14）。

harness 同 tests/test_dashboard_auth.py：stub 重模組、沙箱 dashboard_settings、
session_transaction 直接以管理員登入。config 寫入 monkeypatch 成 no-op，避免動到
真實 bot_config.json。
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


class TestRoleIdCacheAndDeletedRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_lightweight_stubs()
        existing = sys.modules.get("control_panel_app")
        if existing is not None and not hasattr(existing, "app"):
            del sys.modules["control_panel_app"]
        cls.cpa = importlib.import_module("control_panel_app")
        cls.rs = importlib.import_module("control_panel.routes_status")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_settings_path = ds.settings_path()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))
        ds.load_settings()
        ds.create_account("boss", "pw123456", True, [])
        self.client = self.cpa.app.test_client()
        with self.client.session_transaction() as sess:
            sess["dash_user"] = "boss"
            sess["dash_admin"] = True

        self._restore = []

        def _patch(obj, name, value):
            self._restore.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        self._patch = _patch
        # 別動到真實設定檔。
        _patch(config_manager, "update_device_config", lambda *a, **k: None)
        self.rs._device_role_id.cache_clear()

    def tearDown(self):
        for obj, name, orig in reversed(self._restore):
            setattr(obj, name, orig)
        self.rs._device_role_id.cache_clear()
        ds.set_settings_path(self._orig_settings_path)
        self.tmp.cleanup()

    def test_config_post_invalidates_role_id_cache(self):
        # 先讓 role_id=111 進 cache。
        self._patch(config_manager, "get_device_role_id", lambda d: 111)
        self.assertEqual(self.rs._device_role_id("emulator-5554"), 111)
        self.assertGreaterEqual(self.rs._device_role_id.cache_info().currsize, 1)

        # 設定改到讓 role_id 變 222；未清 cache 前仍讀到舊值。
        config_manager.get_device_role_id = lambda d: 222
        self.assertEqual(self.rs._device_role_id("emulator-5554"), 111)  # stale

        # POST 設定後應清 cache → 下次讀到新值。
        r = self.client.post("/api/config/emulator-5554", json={"enable_farm": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rs._device_role_id("emulator-5554"), 222)

    def test_device_data_route_removed(self):
        # 路由已刪除：登入的管理員打過去也應 404（Flask 找不到 endpoint）。
        r = self.client.get("/api/device_data/emulator-5554")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()

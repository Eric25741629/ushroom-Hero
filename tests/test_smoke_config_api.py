import os
import sys
import types
import tempfile
import unittest
import importlib

import config_manager


class TestConfigApiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adb_stub = types.ModuleType('adb_operations')
        adb_stub.run_adb = lambda *args, **kwargs: ''
        sys.modules.setdefault('adb_operations', adb_stub)

        detector_stub = types.ModuleType('game_state.detector')
        detector_stub.stage_by_str = lambda d, ocr, img: 'unknown'
        sys.modules.setdefault('game_state.detector', detector_stub)

        cnn_stub = types.ModuleType('new_cnn.cnn_model')
        cnn_stub.load_cnn_model = lambda path: None
        sys.modules.setdefault('new_cnn.cnn_model', cnn_stub)

        # test_bootstrap_api_services.py stubs `control_panel_app` with an
        # empty ModuleType (no `app` attr) at its own collect time. If we run
        # after it, importlib.import_module returns that stub. Drop the stub
        # so we get the real Flask app.
        if 'control_panel_app' in sys.modules and not hasattr(
            sys.modules['control_panel_app'], 'app'
        ):
            del sys.modules['control_panel_app']
        cls.control_panel_app = importlib.import_module('control_panel_app')

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self._old_cfg_file = config_manager.CONFIG_FILE
        config_manager.CONFIG_FILE = os.path.join(self.tmpdir.name, 'bot_config.json')

        self.client = self.control_panel_app.app.test_client()

    def tearDown(self):
        config_manager.CONFIG_FILE = self._old_cfg_file

    def test_device_config_roundtrip_name(self):
        ip = 'emulator-5554'
        payload = {'name': '???A', 'online_check_interval_sec': 99999, 'lamp_check_interval': 0, 'lamp_duration_sec': 9}
        r = self.client.post(f'/api/config/{ip}', json=payload)
        self.assertEqual(r.status_code, 200)

        r2 = self.client.get(f'/api/config/{ip}')
        data = r2.get_json()
        self.assertEqual(data.get('name'), '???A')
        self.assertEqual(data.get('online_check_interval_sec'), 3600)
        self.assertEqual(data.get('lamp_check_interval'), 1)
        self.assertEqual(data.get('lamp_duration_sec'), 30)

    def test_ocr_config_server_mode_roundtrip(self):
        self.client.post('/api/ocr_config', json={'server_mode': 'backup'})
        cfg = self.client.get('/api/ocr_config').get_json()
        self.assertEqual(cfg.get('server_mode'), 'backup')

    def test_ocr_config_invalid_server_mode_fallback(self):
        self.client.post('/api/ocr_config', json={'server_mode': 'invalid-mode'})
        cfg = self.client.get('/api/ocr_config').get_json()
        self.assertEqual(cfg.get('server_mode'), 'main')


if __name__ == '__main__':
    unittest.main()

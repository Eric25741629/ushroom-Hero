"""輕量瀏覽器在線偵測: /api/fly_pet_browser_status 直接問 CDP /json/list
(透過 find_game_page_target)，不注入遊戲 JS。讓前端能快速分辨
「沒開瀏覽器」vs「瀏覽器已開、遊戲載入中」。
"""
import importlib
import sys
import types


def _import_control_panel_app():
    adb_stub = types.ModuleType("adb_operations")
    adb_stub.run_adb = lambda *args, **kwargs: ""
    sys.modules.setdefault("adb_operations", adb_stub)

    sys.modules.setdefault("cv2", types.ModuleType("cv2"))

    detector_stub = types.ModuleType("game_state.detector")
    detector_stub.stage_by_str = lambda *args, **kwargs: "unknown"
    sys.modules.setdefault("game_state.detector", detector_stub)

    cnn_stub = types.ModuleType("new_cnn.cnn_model")
    cnn_stub.load_cnn_model = lambda path: None
    sys.modules.setdefault("new_cnn.cnn_model", cnn_stub)

    existing = sys.modules.get("control_panel_app")
    if existing is not None and not hasattr(existing, "app"):
        del sys.modules["control_panel_app"]
    return importlib.import_module("control_panel_app")


def _login_fly_pet(client):
    with client.session_transaction() as sess:
        sess["fly_pet_auth"] = True


def _stub_live_view_bridge(monkeypatch, target):
    stub = types.ModuleType("runtime_services.live_view_bridge")
    stub.find_game_page_target = lambda *args, **kwargs: target
    monkeypatch.setitem(sys.modules, "runtime_services.live_view_bridge", stub)


def test_browser_status_up_when_cdp_target_found(monkeypatch):
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa.config_manager, "get_device_config", lambda ip: {"web_debug_port": 9322})
    _stub_live_view_bridge(monkeypatch, "ws://127.0.0.1:9322/devtools/page/ABC")

    client = cpa.app.test_client()
    _login_fly_pet(client)
    resp = client.get("/api/fly_pet_browser_status/7fe98fc6")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["browser_up"] is True
    assert data["debug_port"] == 9322


def test_browser_status_down_when_no_cdp_target(monkeypatch):
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa.config_manager, "get_device_config", lambda ip: {"web_debug_port": 9322})
    _stub_live_view_bridge(monkeypatch, None)

    client = cpa.app.test_client()
    _login_fly_pet(client)
    resp = client.get("/api/fly_pet_browser_status/7fe98fc6")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["browser_up"] is False
    assert data["debug_port"] == 9322


def test_browser_status_no_debug_port_is_not_an_error(monkeypatch):
    """adb 機 / 未設 web_debug_port: 回 200 + browser_up False, 不視為錯誤。"""
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa.config_manager, "get_device_config", lambda ip: {})

    client = cpa.app.test_client()
    _login_fly_pet(client)
    resp = client.get("/api/fly_pet_browser_status/emulator-5554")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["browser_up"] is False
    assert data["no_debug_port"] is True

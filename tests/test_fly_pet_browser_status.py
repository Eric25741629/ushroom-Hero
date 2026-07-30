"""舊 browser_status URL 現在回報純 WS session，供舊前端相容。"""
import importlib
import sys
import types


def _client(monkeypatch, ws_client):
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    detector_stub = types.ModuleType("game_state.detector")
    detector_stub.stage_by_str = lambda *args, **kwargs: "unknown"
    sys.modules.setdefault("game_state.detector", detector_stub)
    cnn_stub = types.ModuleType("new_cnn.cnn_model")
    cnn_stub.load_cnn_model = lambda path: None
    sys.modules.setdefault("new_cnn.cnn_model", cnn_stub)
    cpa = importlib.import_module("control_panel_app")
    import control_panel.routes_fly_pet as routes
    monkeypatch.setattr(routes.ws_session, "get_client", lambda ip: ws_client)
    client = cpa.app.test_client()
    with client.session_transaction() as sess:
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True
    return client


class _Live:
    def is_running(self):
        return True


def test_compat_status_up_when_pure_ws_connected(monkeypatch):
    data = _client(monkeypatch, _Live()).get(
        "/api/fly_pet_browser_status/emulator-5554"
    ).get_json()["data"]
    assert data == {
        "browser_up": True,
        "connected": True,
        "transport": "pure_ws",
    }


def test_compat_status_down_without_pure_ws_session(monkeypatch):
    data = _client(monkeypatch, None).get(
        "/api/fly_pet_browser_status/emulator-5554"
    ).get_json()["data"]
    assert data["browser_up"] is False
    assert data["connected"] is False
    assert data["transport"] == "pure_ws"

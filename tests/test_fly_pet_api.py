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
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True


def test_fly_pet_hatch_uses_pure_ws_egg_command(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)

    captured = {}
    import control_panel.routes_fly_pet as routes
    monkeypatch.setattr(routes, "_session_client", lambda ip: (object(), None))
    monkeypatch.setattr(
        routes.ws_fly_pet,
        "hatch_egg",
        lambda _client, egg_id: captured.setdefault("egg_id", egg_id) or [88],
    )

    resp = client.post("/api/fly_pet_hatch/7fe98fc6", json={"egg_id": 12345})

    assert resp.status_code == 200
    assert captured["egg_id"] == 12345
    assert resp.get_json()["data"]["ok"] is True

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


def test_fly_pet_hatch_posts_egg_id_array_and_waits_for_egg_list(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)

    captured = {}

    def fake_cdp_json_response(ip, expression, await_promise=False, data_key="data"):
        captured["ip"] = ip
        captured["expression"] = expression
        captured["await_promise"] = await_promise
        captured["data_key"] = data_key
        return cpa.jsonify({"status": "ok", "data": {"ok": True}})

    monkeypatch.setattr(cpa, "_cdp_json_response", fake_cdp_json_response)

    resp = client.post("/api/fly_pet_hatch/7fe98fc6", json={"egg_id": 12345})

    assert resp.status_code == 200
    assert captured["ip"] == "7fe98fc6"
    assert captured["await_promise"] is True
    assert "normalEvent.on('EggListBack', handler);" in captured["expression"]
    assert "normalEvent.off('EggListBack', handler);" in captured["expression"]
    assert "send_66_3([12345])" in captured["expression"]
    assert "send_66_29" not in captured["expression"]
    assert "base_id" not in captured["expression"]

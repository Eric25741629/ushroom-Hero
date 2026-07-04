"""/api/fly_pet_icon/<config_id> serves the cached real in-game icon PNG.

Icons are dumped by tools/dump_flypet_icons.py into static/flypet_icons/<id>.png.
The endpoint serves them so the dashboard's card wall can show real sprites; a
missing icon returns 404 so the front-end falls back to its placeholder avatar.
"""
import importlib
import sys
import types

# Minimal 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100"
    "05fe02fea7d4e2120000000049454e44ae426082"
)


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


def _login(client):
    with client.session_transaction() as sess:
        sess["fly_pet_auth"] = True
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True


def test_icon_served_when_cached(monkeypatch, tmp_path):
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa, "_FLY_PET_ICON_DIR", str(tmp_path))
    (tmp_path / "1001.png").write_bytes(_PNG)

    client = cpa.app.test_client()
    _login(client)
    resp = client.get("/api/fly_pet_icon/1001")

    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"
    # Icons must stay browser-cacheable (exempt from the global no-store hook).
    assert "max-age=86400" in resp.headers.get("Cache-Control", "")
    assert "no-store" not in resp.headers.get("Cache-Control", "")


def test_missing_icon_returns_404(monkeypatch, tmp_path):
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa, "_FLY_PET_ICON_DIR", str(tmp_path))

    client = cpa.app.test_client()
    _login(client)
    resp = client.get("/api/fly_pet_icon/999999")

    assert resp.status_code == 404


def test_non_integer_config_id_is_not_routed(monkeypatch, tmp_path):
    """The <int:> converter blocks path traversal / arbitrary filenames."""
    cpa = _import_control_panel_app()
    monkeypatch.setattr(cpa, "_FLY_PET_ICON_DIR", str(tmp_path))

    client = cpa.app.test_client()
    _login(client)
    resp = client.get("/api/fly_pet_icon/..%2f..%2fsecret")

    assert resp.status_code == 404

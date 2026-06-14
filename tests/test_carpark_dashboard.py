"""Tests for the /api/carpark/<ip> dashboard endpoint (read-only ws_state snapshot).

The endpoint NEVER logs in over WS (that would kick the device session) — it only
reads the snapshot the bot's WS carpark task wrote into ws_state, and recomputes
each car's elapsed / time-to-8h against the local clock (the --parked calibration,
surfaced on the dashboard).
"""
import importlib
import sys
import time
import types


def _import_control_panel_app():
    sys.modules.setdefault("adb_operations",
                           types.ModuleType("adb_operations")).run_adb = \
        lambda *a, **k: ""
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    det = types.ModuleType("game_state.detector")
    det.stage_by_str = lambda *a, **k: "unknown"
    sys.modules.setdefault("game_state.detector", det)
    cnn = types.ModuleType("new_cnn.cnn_model")
    cnn.load_cnn_model = lambda path: None
    sys.modules.setdefault("new_cnn.cnn_model", cnn)
    existing = sys.modules.get("control_panel_app")
    if existing is not None and not hasattr(existing, "app"):
        del sys.modules["control_panel_app"]
    return importlib.import_module("control_panel_app")


def _patch(monkeypatch, *, plan_enabled, snapshot):
    import config_manager
    from ws_token import state as ws_state
    monkeypatch.setattr(config_manager, "get_device_config",
                        lambda ip: {"ws_token": {"carpark_plan":
                                                 {"enabled": plan_enabled}}})
    monkeypatch.setattr(ws_state, "load_state",
                        lambda ip, **kw: {"carpark_repark": snapshot}
                        if snapshot is not None else {})


def test_carpark_endpoint_disabled_plan(monkeypatch):
    cpa = _import_control_panel_app()
    _patch(monkeypatch, plan_enabled=False, snapshot=None)
    resp = cpa.app.test_client().get("/api/carpark/dev1")
    assert resp.status_code == 200
    assert resp.get_json() == {"enabled": False}


def test_carpark_endpoint_no_snapshot_yet(monkeypatch):
    cpa = _import_control_panel_app()
    _patch(monkeypatch, plan_enabled=True, snapshot=None)
    data = cpa.app.test_client().get("/api/carpark/dev1").get_json()
    assert data["enabled"] is True
    assert data["captured"] is False


def test_carpark_endpoint_computes_elapsed_and_remaining(monkeypatch):
    cpa = _import_control_panel_app()
    now = int(time.time())
    snap = {
        "next_ts": now + 21600, "captured_ts": now, "park_max": 28800,
        "offset": 0, "window": "day", "target": 1,
        "cars": [{"mount_id": 101, "master_id": 1001001013, "pos": 4,
                  "start_time": now - 7200}],   # parked 2h ago
    }
    _patch(monkeypatch, plan_enabled=True, snapshot=snap)
    data = cpa.app.test_client().get("/api/carpark/dev1").get_json()
    assert data["enabled"] is True and data["captured"] is True
    assert data["current"] == 1 and data["target"] == 1
    car = data["cars"][0]
    assert abs(car["elapsed_h"] - 2.0) < 0.05      # ~2h elapsed
    assert abs(car["remaining_h"] - 6.0) < 0.05    # ~6h to the 8h collect
    assert car["epoch_sane"] is True
    assert data["epoch_sane"] is True
    assert abs(data["worst_remaining_h"] - 6.0) < 0.05


def test_carpark_endpoint_flags_non_unix_epoch(monkeypatch):
    cpa = _import_control_panel_app()
    now = int(time.time())
    snap = {
        "next_ts": None, "captured_ts": now, "park_max": 28800, "offset": 0,
        "window": "day", "target": 1,
        "cars": [{"mount_id": 1, "master_id": 900, "pos": 1,
                  "start_time": 12345}],   # bogus -> not unix epoch
    }
    _patch(monkeypatch, plan_enabled=True, snapshot=snap)
    data = cpa.app.test_client().get("/api/carpark/dev1").get_json()
    assert data["cars"][0]["epoch_sane"] is False
    assert data["epoch_sane"] is False


def test_carpark_endpoint_strips_host_prefix(monkeypatch):
    cpa = _import_control_panel_app()
    seen = {}
    import config_manager
    from ws_token import state as ws_state

    def _cfg(ip):
        seen["cfg_ip"] = ip
        return {"ws_token": {"carpark_plan": {"enabled": True}}}

    def _load(ip, **kw):
        seen["state_ip"] = ip
        return {}

    monkeypatch.setattr(config_manager, "get_device_config", _cfg)
    monkeypatch.setattr(ws_state, "load_state", _load)
    cpa.app.test_client().get("/api/carpark/laptop:adb-fc65396d")
    assert seen["cfg_ip"] == "adb-fc65396d"     # host prefix stripped
    assert seen["state_ip"] == "adb-fc65396d"

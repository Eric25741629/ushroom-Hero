"""POST /api/devices/register must create the device DISABLED.

A newly registered web_h5 device opens a manual login browser. Until the user
finishes logging in / filling in settings and explicitly enables it, the scanner
must not auto-launch a bot thread for it (otherwise the bot thread and the login
browser fight over the same Playwright session). The endpoint expresses this by
writing `enabled: false`.
"""
import importlib
import json
import sys
import types

import config_manager


def _load_control_panel_app():
    # Mirror tests/test_smoke_config_api.py: stub heavy device/CNN imports so the
    # real Flask app imports without a live runtime.
    sys.modules.setdefault("adb_operations", _stub_module("adb_operations", run_adb=lambda *a, **k: ""))
    sys.modules.setdefault(
        "game_state.detector",
        _stub_module("game_state.detector", stage_by_str=lambda d, ocr, img: "unknown"),
    )
    sys.modules.setdefault(
        "new_cnn.cnn_model",
        _stub_module("new_cnn.cnn_model", load_cnn_model=lambda path: None),
    )
    if "control_panel_app" in sys.modules and not hasattr(sys.modules["control_panel_app"], "app"):
        del sys.modules["control_panel_app"]
    return importlib.import_module("control_panel_app")


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def test_register_device_creates_disabled(tmp_path, monkeypatch):
    cpa = _load_control_panel_app()

    cfg_path = tmp_path / "bot_config.json"
    cfg_path.write_text(
        json.dumps({"devices": {}, "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_path", None, raising=False)

    # Don't actually launch Playwright — register spawns a login-worker thread.
    monkeypatch.setattr(cpa, "_run_web_login_worker", lambda ip, payload: None)

    client = cpa.app.test_client()
    resp = client.post(
        "/api/devices/register",
        json={"device_id": "web-new", "web_url": "https://example.com/"},
    )
    assert resp.status_code == 200

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    dev = raw["devices"]["web-new"]
    assert dev["backend"] == "web_h5"
    assert dev["enabled"] is False  # disabled until the user enables it

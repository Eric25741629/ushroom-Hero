import json
import sys
import types

import pytest

import bot_state
import config_manager

detector_stub = types.ModuleType("game_state.detector")
detector_stub.stage_by_str = lambda d, ocr, img: "unknown"
detector_stub.get_stage = lambda *args, **kwargs: "unknown"
sys.modules.setdefault("game_state.detector", detector_stub)

cnn_stub = types.ModuleType("new_cnn.cnn_model")
cnn_stub.load_cnn_model = lambda path: None
sys.modules.setdefault("new_cnn.cnn_model", cnn_stub)

from control_panel import routes_status


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({
        "global": {},
        "devices": {
            "web-001": {
                "name": "寶兒",
                "enabled": True,
                "backend": "web_h5",
                "battle_speed_scale": 4,
            },
            "adb-001": {
                "name": "手機",
                "enabled": True,
                "backend": "adb",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(path))
    monkeypatch.setattr(routes_status, "check_ocr_server", lambda: False)
    monkeypatch.setattr(routes_status, "_account_presence", lambda: {})
    monkeypatch.setattr(
        bot_state,
        "get_all_states",
        lambda: {
            "web-001": {
                "status": "ONLINE",
                "task": "休眠中",
                "step": "等待排程",
                "logs": [],
            }
        },
    )

    import control_panel_app

    test_client = control_panel_app.app.test_client()
    with test_client.session_transaction() as session:
        session["dash_user"] = "boss"
        session["dash_admin"] = True
    return test_client


def test_set_battle_speed_persists_and_returns_value(client):
    response = client.post("/api/battle_speed/web-001", json={"scale": 2})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["battle_speed_scale"] == 2.0
    cfg = config_manager.get_device_config("web-001")
    assert cfg.get("battle_speed_scale") == 2.0


def test_set_battle_speed_disable_with_one(client):
    response = client.post("/api/battle_speed/web-001", json={"scale": 1})

    assert response.status_code == 200
    assert response.get_json()["battle_speed_scale"] == 1.0
    assert config_manager.get_device_config("web-001").get("battle_speed_scale") == 1.0


@pytest.mark.parametrize(("sent", "expected"), [
    (99, 10.0),   # clamped to ceiling
    (0, 1.0),     # non-positive floors to 1
    (-5, 1.0),
])
def test_set_battle_speed_coerces_out_of_range(client, sent, expected):
    response = client.post("/api/battle_speed/web-001", json={"scale": sent})

    assert response.status_code == 200
    assert response.get_json()["battle_speed_scale"] == expected


def test_missing_scale_is_rejected(client):
    response = client.post("/api/battle_speed/web-001", json={})

    assert response.status_code == 400


def test_non_numeric_scale_is_rejected(client):
    response = client.post("/api/battle_speed/web-001", json={"scale": "fast"})

    assert response.status_code == 400


def test_adb_device_is_rejected(client):
    response = client.post("/api/battle_speed/adb-001", json={"scale": 2})

    assert response.status_code == 403
    # config untouched — no battle_speed_scale forced onto the adb device request
    assert config_manager.get_device_config("adb-001").get("backend") == "adb"


def test_status_includes_battle_speed_scale(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    bots = response.get_json()["bots"]
    assert bots["web-001"]["battle_speed_scale"] == 4.0

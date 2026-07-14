import json
import sys
import types

import pytest

import bot_state
import config_manager

detector_stub = types.ModuleType("game_state.detector")
detector_stub.stage_by_str = lambda d, ocr, img: "unknown"
sys.modules.setdefault("game_state.detector", detector_stub)

cnn_stub = types.ModuleType("new_cnn.cnn_model")
cnn_stub.load_cnn_model = lambda path: None
sys.modules.setdefault("new_cnn.cnn_model", cnn_stub)

adb_stub = types.ModuleType("adb_operations")
adb_stub.run_adb = lambda *args, **kwargs: ""
sys.modules.setdefault("adb_operations", adb_stub)

from control_panel import routes_status
from game_actions import special_wanshen


class EmptyManager:
    def get_record(self, name):
        return None


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
                "special_wanshen_account": True,
                "special_wanshen_enabled": False,
                "special_wanshen_rounds": 10,
            },
            "web-002": {
                "name": "暴哥",
                "enabled": False,
                "backend": "web_h5",
                "special_wanshen_account": True,
                "special_wanshen_enabled": False,
                "special_wanshen_rounds": 10,
            },
            "normal": {
                "name": "一般帳號",
                "enabled": False,
                "backend": "web_h5",
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(path))
    monkeypatch.setattr(special_wanshen, "JsonDataManager", lambda ip: EmptyManager())
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


def test_enable_special_wanshen_persists(client):
    response = client.post("/api/special_wanshen/web-001", json={"enabled": True})

    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is True
    cfg = config_manager.get_device_config("web-001")
    assert cfg.get("special_wanshen_enabled") is True
    assert cfg.get("enabled") is True


def test_get_special_wanshen_returns_schedule_state(client):
    response = client.get("/api/special_wanshen/web-001")

    assert response.status_code == 200
    body = response.get_json()
    assert body["rounds"] == 10
    assert body["attempted_today"] is False
    assert body["completed_this_week"] is False
    assert "next_attempt_at" in body


def test_non_special_device_is_rejected(client):
    response = client.post("/api/special_wanshen/normal", json={"enabled": True})

    assert response.status_code == 403


def test_status_includes_special_fields_for_running_and_disabled_cards(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    bots = response.get_json()["bots"]
    for ip in ("web-001", "web-002"):
        assert bots[ip]["special_wanshen_account"] is True
        assert bots[ip]["special_wanshen_enabled"] is False
        assert bots[ip]["special_wanshen_rounds"] == 10
        assert bots[ip]["special_wanshen_attempted_today"] is False
        assert bots[ip]["special_wanshen_completed_this_week"] is False

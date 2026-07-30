"""星據搶佔純 WS 路由與 Python 安全閘門。"""
import pytest
from flask import Flask

from control_panel import routes_star_seize


class FakeClient:
    pass


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(routes_star_seize, "require_device_access", lambda ip: None)
    monkeypatch.setattr(
        routes_star_seize.config_manager,
        "get_device_config",
        lambda ip: {"backend": "adb", "ws_enabled": True,
                    "star_seize_my_server": 1467},
    )
    monkeypatch.setattr(routes_star_seize.ws_session, "get_client", lambda ip: fake)
    app = Flask(__name__)
    app.register_blueprint(routes_star_seize.bp)
    app.testing = True
    return app.test_client()


def test_api_does_not_create_ws_when_load_was_not_pressed(monkeypatch):
    monkeypatch.setattr(routes_star_seize, "require_device_access", lambda ip: None)
    monkeypatch.setattr(routes_star_seize.ws_session, "get_client", lambda ip: None)
    app = Flask(__name__)
    app.register_blueprint(routes_star_seize.bp)
    resp = app.test_client().get("/api/star_seize/state/emulator-5554")
    assert resp.status_code == 409
    assert "請先按載入" in resp.get_json()["message"]


def test_state_reads_from_existing_ws_for_adb_device(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_state",
        lambda ws, my_server=0: calls.append((ws, my_server)) or {
            "serverTime": 14400, "slots": []
        },
    )
    resp = client.get("/api/star_seize/state/emulator-5554")
    assert resp.status_code == 200
    assert resp.get_json()["state"]["serverTime"] == 14400
    assert calls and calls[0][1] == 1467


def test_state_request_my_server_overrides_config(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_state",
        lambda _ws, my_server=0: seen.append(my_server) or {
            "serverTime": 14400, "slots": []
        },
    )
    assert client.get("/api/star_seize/state/dev?my_server=88").status_code == 200
    assert seen == [88]


def test_opponent_validation_and_ws_reader(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_opponent",
        lambda ws, pos: calls.append((ws, pos)) or {
            "pos": pos, "defenders": [{"name": "守方"}]
        },
    )
    assert client.get("/api/star_seize/opponent/dev?pos=7").status_code == 400
    resp = client.get("/api/star_seize/opponent/dev?pos=2")
    assert resp.status_code == 200
    assert resp.get_json()["opponent"]["defenders"][0]["name"] == "守方"
    assert calls[0][1] == 2


@pytest.mark.parametrize("payload", [
    {"pos": 0, "queue_type": 1},
    {"pos": 5, "queue_type": 1},
    {"pos": 1, "queue_type": 9},
    {"queue_type": 1},
])
def test_seize_rejects_invalid_payload(client, payload):
    assert client.post("/api/star_seize/seize/dev", json=payload).status_code == 400


def _state(*, owner=5, free_end=0, attack_cd=0, pos=1):
    return {
        "serverTime": 14400,
        "attack_cd_end_time": attack_cd,
        "defend_cd_end_time": 0,
        "slots": [{"pos": pos, "owner": owner, "free_end": free_end}],
    }


def test_seize_reads_fresh_state_then_sends_one_ws_join(client, monkeypatch):
    events = []
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_state",
        lambda _ws, my_server=0: events.append(("state", my_server)) or _state(),
    )
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "join",
        lambda _ws, pos, qt: events.append(("join", pos, qt)) or {
            "ok": True, "code": 0, "pos": pos, "queue_type": qt
        },
    )
    resp = client.post(
        "/api/star_seize/seize/dev",
        json={"pos": 1, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    assert resp.get_json()["reply"]["ok"] is True
    assert events == [("state", 1467), ("join", 1, 1)]


@pytest.mark.parametrize(("state", "reason"), [
    (_state(attack_cd=999999), "cooldown"),
    (_state(owner=1467), "own-server"),
    (_state(free_end=999999), "protected"),
    (_state(owner=0), "empty"),
])
def test_seize_gate_blocks_without_sending_join(
    client, monkeypatch, state, reason
):
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_state",
        lambda _ws, my_server=0: state,
    )
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "join",
        lambda *_args: pytest.fail("安全閘未通過時不得送 join"),
    )
    resp = client.post(
        "/api/star_seize/seize/dev",
        json={"pos": 1, "queue_type": 1, "my_server": 1467},
    )
    assert resp.get_json()["reply"] == {"ok": False, "reason": reason}


def test_defend_only_requires_nonempty_slot(client, monkeypatch):
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "read_state",
        lambda _ws, my_server=0: _state(free_end=999999, attack_cd=999999),
    )
    monkeypatch.setattr(
        routes_star_seize.ws_star_seize,
        "join",
        lambda _ws, pos, qt: {"ok": True, "code": 0, "pos": pos,
                              "queue_type": qt},
    )
    resp = client.post(
        "/api/star_seize/seize/dev", json={"pos": 1, "queue_type": 2}
    )
    assert resp.get_json()["reply"]["ok"] is True


def test_gate_truce_uses_taiwan_time():
    state = _state()
    state["serverTime"] = 0  # UTC+8 = 08:00
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 1, 1467)
    assert ok is False and reason == "truce"

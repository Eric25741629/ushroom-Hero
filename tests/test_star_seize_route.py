"""Unit tests for star_seize routes (control_panel.routes_star_seize).

gate = 裝置 backend=='web_h5' 且有 web_debug_port;pos∈1..4;queue_type∈{1,2};
合法時晚綁定呼叫 control_panel_app._cdp_json_response 且 JS 含正確 pos/queue_type。
以 minimal Flask app 註冊 bp,monkeypatch require_device_access 為 no-op,並以
sys.modules 注入 stub control_panel_app 攔截 _cdp_json_response。
"""
import sys
import types

import pytest
from flask import Flask, jsonify

from control_panel import routes_star_seize


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes_star_seize, "require_device_access", lambda ip: None)
    # web_h5 + web_debug_port 的裝置 → 通過 gate
    monkeypatch.setattr(
        routes_star_seize.config_manager,
        "get_device_config",
        lambda ip: {"backend": "web_h5", "web_debug_port": 9226},
    )
    app = Flask(__name__)
    app.register_blueprint(routes_star_seize.bp)
    app.testing = True
    return app.test_client()


@pytest.fixture
def spy_cdp(monkeypatch):
    """攔截 _cdp_json_response,回傳呼叫紀錄 list。"""
    calls = []

    def fake_cdp_json_response(ip, expression, **kwargs):
        calls.append({"ip": ip, "js": expression, "kwargs": kwargs})
        return jsonify({"status": "ok", "data": {"ok": True}}), 200

    fake_cpa = types.ModuleType("control_panel_app")
    fake_cpa._cdp_json_response = fake_cdp_json_response
    monkeypatch.setitem(sys.modules, "control_panel_app", fake_cpa)
    return calls


def test_gate_rejects_non_web_h5(monkeypatch):
    monkeypatch.setattr(routes_star_seize, "require_device_access", lambda ip: None)
    monkeypatch.setattr(
        routes_star_seize.config_manager,
        "get_device_config",
        lambda ip: {"backend": "adb"},
    )
    app = Flask(__name__)
    app.register_blueprint(routes_star_seize.bp)
    app.testing = True
    c = app.test_client()

    resp = c.get("/api/star_seize/state/emulator-5554")
    assert resp.status_code == 403


def test_gate_rejects_web_h5_without_debug_port(monkeypatch):
    monkeypatch.setattr(routes_star_seize, "require_device_access", lambda ip: None)
    monkeypatch.setattr(
        routes_star_seize.config_manager,
        "get_device_config",
        lambda ip: {"backend": "web_h5"},  # 無 web_debug_port
    )
    app = Flask(__name__)
    app.register_blueprint(routes_star_seize.bp)
    app.testing = True
    c = app.test_client()

    resp = c.post("/api/star_seize/seize/7fe98fc6", json={"pos": 1, "queue_type": 1})
    assert resp.status_code == 403


def test_seize_rejects_pos_out_of_range(client):
    resp = client.post("/api/star_seize/seize/7fe98fc6", json={"pos": 5, "queue_type": 1})
    assert resp.status_code == 400


def test_seize_rejects_pos_zero(client):
    resp = client.post("/api/star_seize/seize/7fe98fc6", json={"pos": 0, "queue_type": 1})
    assert resp.status_code == 400


def test_seize_rejects_missing_pos(client):
    resp = client.post("/api/star_seize/seize/7fe98fc6", json={"queue_type": 1})
    assert resp.status_code == 400


def test_seize_rejects_bad_queue_type(client):
    resp = client.post("/api/star_seize/seize/7fe98fc6", json={"pos": 1, "queue_type": 9})
    assert resp.status_code == 400


def test_seize_valid_calls_cdp_once(client, spy_cdp):
    resp = client.post("/api/star_seize/seize/7fe98fc6", json={"pos": 3, "queue_type": 2})
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    js = spy_cdp[0]["js"]
    # 驗證安全鐵律:先讀 server_car_info,再帶正確 pos/queue_type 送 join
    assert "POS = 3" in js
    assert "QT = 2" in js
    assert "car_park.server_car_info" in js  # 送 join 前先讀狀態驗證
    assert spy_cdp[0]["kwargs"].get("await_promise") is True


def test_snipe_rejects_bad_pos(client):
    resp = client.post("/api/star_seize/snipe/7fe98fc6", json={"pos": 9, "queue_type": 1})
    assert resp.status_code == 400


def test_snipe_rejects_bad_queue_type(client):
    resp = client.post("/api/star_seize/snipe/7fe98fc6", json={"pos": 1, "queue_type": 3})
    assert resp.status_code == 400


def test_snipe_valid_calls_cdp_once(client, spy_cdp):
    resp = client.post(
        "/api/star_seize/snipe/7fe98fc6",
        json={"pos": 4, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    js = spy_cdp[0]["js"]
    assert "POS = 4" in js
    assert "QT = 1" in js
    assert "OWN = 1467" in js


def test_opponent_rejects_bad_pos(client):
    resp = client.get("/api/star_seize/opponent/7fe98fc6?pos=7")
    assert resp.status_code == 400


def test_opponent_valid_calls_cdp_once(client, spy_cdp):
    resp = client.get("/api/star_seize/opponent/7fe98fc6?pos=2")
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    assert "pos: 2" in spy_cdp[0]["js"]


def test_state_valid_calls_cdp_once(client, spy_cdp):
    resp = client.get("/api/star_seize/state/7fe98fc6?my_server=1467")
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    assert "MY = 1467" in spy_cdp[0]["js"]

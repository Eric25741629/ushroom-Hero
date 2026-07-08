"""Unit tests for the carpark_rob route (control_panel.routes_control).

route 只接受小寶 (ip == "7fe98fc6")；驗 pos/queue_type；合法時晚綁定呼叫
control_panel_app._cdp_json_response。測試用 minimal Flask app 註冊 bp，
monkeypatch require_device_access 為 no-op，並以 sys.modules 注入 stub
control_panel_app 以攔截 _cdp_json_response（避免載入真實 dashboard 模組）。
"""
import sys
import types

import pytest
from flask import Flask, jsonify

from control_panel import routes_control


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes_control, "require_device_access", lambda ip: None)
    app = Flask(__name__)
    app.register_blueprint(routes_control.bp)
    app.testing = True
    return app.test_client()


def test_rejects_non_xiaobao_device(client):
    resp = client.post(
        "/api/carpark_rob/emulator-5554", json={"pos": 1, "queue_type": 1}
    )
    assert resp.status_code == 403


def test_rejects_missing_pos(client):
    resp = client.post("/api/carpark_rob/7fe98fc6", json={"queue_type": 1})
    assert resp.status_code == 400


def test_rejects_bad_queue_type(client):
    resp = client.post(
        "/api/carpark_rob/7fe98fc6", json={"pos": 1, "queue_type": 9}
    )
    assert resp.status_code == 400


def test_rejects_pos_below_one(client):
    resp = client.post(
        "/api/carpark_rob/7fe98fc6", json={"pos": 0, "queue_type": 1}
    )
    assert resp.status_code == 400


def test_valid_calls_cdp_once(client, monkeypatch):
    calls = []

    def fake_cdp_json_response(ip, expression, **kwargs):
        calls.append({"ip": ip, "js": expression, "kwargs": kwargs})
        return jsonify({"status": "ok", "reply": {"ok": True}}), 200

    fake_cpa = types.ModuleType("control_panel_app")
    fake_cpa._cdp_json_response = fake_cdp_json_response
    monkeypatch.setitem(sys.modules, "control_panel_app", fake_cpa)

    resp = client.post(
        "/api/carpark_rob/7fe98fc6", json={"pos": 3, "queue_type": 2}
    )

    assert resp.status_code == 200
    assert len(calls) == 1
    js = calls[0]["js"]
    assert "pos: 3" in js
    assert "queue_type: 2" in js

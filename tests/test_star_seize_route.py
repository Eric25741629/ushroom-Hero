"""Unit tests for star_seize routes (control_panel.routes_star_seize).

gate = 裝置 backend=='web_h5' 且有 web_debug_port;pos∈1..4;queue_type∈{1,2}。
- /state、/opponent 走單次 ``_cdp_json_response``(晚綁定 stub 攔截、驗 JS 內容)。
- /seize 為 Python 端多步 orchestration(數次 ``_cdp_evaluate``):
  ensure arena → open building → 讀 server_car_info 驗閘 → 全過才 emit join。
  以 stub ``_cdp_evaluate`` 依序回 canned 結果,驗證「閘門不過即不 emit 加入搶佔」。
snipe 冷送路徑已移除(本檔不再測 /snipe*)。
"""
import json
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
    """攔截 _cdp_json_response(/state /opponent 用),回傳呼叫紀錄 list。"""
    calls = []

    def fake_cdp_json_response(ip, expression, **kwargs):
        calls.append({"ip": ip, "js": expression, "kwargs": kwargs})
        return jsonify({"status": "ok", "data": {"ok": True}}), 200

    fake_cpa = types.ModuleType("control_panel_app")
    fake_cpa._cdp_json_response = fake_cdp_json_response
    monkeypatch.setitem(sys.modules, "control_panel_app", fake_cpa)
    return calls


@pytest.fixture
def seize_cdp(monkeypatch):
    """攔截 _cdp_evaluate(/seize orchestration 用)。

    回 (calls, responses)。測試前把 ``responses["queue"]`` 設成每步依序要回的物件;
    stub 依呼叫序 pop 出、包成 ``_cdp_evaluate`` 的回傳格式 (result_dict, None)。
    """
    calls = []
    responses = {"queue": []}

    def fake_cdp_evaluate(ip, expression, await_promise=False, timeout=15):
        calls.append({"ip": ip, "js": expression})
        q = responses["queue"]
        obj = q.pop(0) if q else {}
        return {"result": {"type": "string", "value": json.dumps(obj)}}, None

    # 避免 sleep 拖慢測試
    monkeypatch.setattr(routes_star_seize.time, "sleep", lambda *_a, **_k: None)

    click_calls = []

    def fake_cdp_click(ip, x, y, timeout=10):
        click_calls.append({"ip": ip, "x": x, "y": y})
        return True, None

    fake_cpa = types.ModuleType("control_panel_app")
    fake_cpa._cdp_evaluate = fake_cdp_evaluate
    fake_cpa._cdp_click = fake_cdp_click
    monkeypatch.setitem(sys.modules, "control_panel_app", fake_cpa)
    responses["click_calls"] = click_calls
    return calls, responses


# --- gate / validation ------------------------------------------------------
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


# --- Python 端閘門(pure unit) ---------------------------------------------
def test_gate_free_end_zero_is_attackable():
    state = {
        "serverTime": 14400,
        "attack_cd_end_time": 0,
        "defend_cd_end_time": 0,
        "slots": [{"pos": 1, "owner": 5, "free_end": 0}],
    }
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 1, 1467)
    assert ok is True and reason is None


def test_gate_blocks_cooldown():
    state = {
        "serverTime": 14400,
        "attack_cd_end_time": 999999,
        "defend_cd_end_time": 0,
        "slots": [{"pos": 1, "owner": 5, "free_end": 0}],
    }
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 1, 1467)
    assert ok is False and reason == "cooldown"


def test_gate_blocks_own_server():
    state = {
        "serverTime": 14400,
        "attack_cd_end_time": 0,
        "slots": [{"pos": 1, "owner": 1467, "free_end": 0}],
    }
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 1, 1467)
    assert ok is False and reason == "own-server"


def test_gate_truce_uses_taiwan_time():
    # serverTime=0 → +8h → 台灣 8 點 → 休戰(<10)
    state = {"serverTime": 0, "attack_cd_end_time": 0, "slots": [{"pos": 1, "owner": 5, "free_end": 0}]}
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 1, 1467)
    assert ok is False and reason == "truce"


def test_gate_defend_skips_attack_gates():
    # queue_type=2 駐守:即使 cooldown/protected 也只驗槽存在
    state = {
        "serverTime": 14400,
        "attack_cd_end_time": 999999,
        "slots": [{"pos": 1, "owner": 5, "free_end": 999999}],
    }
    ok, reason = routes_star_seize._evaluate_seize_gate(state, 1, 2, 1467)
    assert ok is True and reason is None


# --- /seize orchestration ---------------------------------------------------
def _nav_ok():
    return {"view": ["ParkingMainView"], "inArena": True}


def test_seize_all_gates_pass_emits_join(client, seize_cdp):
    calls, responses = seize_cdp
    responses["queue"] = [
        _nav_ok(),
        {"opened": 1, "tried": 1},
        {"serverTime": 14400, "attack_cd_end_time": 0, "defend_cd_end_time": 0,
         "slots": [{"pos": 1, "owner": 5, "free_end": 0}]},
        {"ok": True, "code": 0, "pos": 1, "queue_type": 1, "queue_index": 0},
    ]
    resp = client.post(
        "/api/star_seize/seize/7fe98fc6",
        json={"pos": 1, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert reply["ok"] is True and reply["code"] == 0
    # NAV + OPEN + GATE_READ + JOIN = 4 步
    assert len(calls) == 4
    assert any("加入搶佔" in c["js"] for c in calls)


def test_seize_cooldown_blocks_without_join(client, seize_cdp):
    calls, responses = seize_cdp
    responses["queue"] = [
        _nav_ok(),
        {"opened": 1, "tried": 1},
        {"serverTime": 14400, "attack_cd_end_time": 999999, "defend_cd_end_time": 0,
         "slots": [{"pos": 1, "owner": 5, "free_end": 0}]},
    ]
    resp = client.post(
        "/api/star_seize/seize/7fe98fc6",
        json={"pos": 1, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert reply["ok"] is False and reply["reason"] == "cooldown"
    # 只跑到 GATE_READ(3 步),未 emit join
    assert len(calls) == 3
    assert not any("加入搶佔" in c["js"] for c in calls)


def test_seize_own_server_blocks_without_join(client, seize_cdp):
    calls, responses = seize_cdp
    responses["queue"] = [
        _nav_ok(),
        {"opened": 2, "tried": 1},
        {"serverTime": 14400, "attack_cd_end_time": 0,
         "slots": [{"pos": 2, "owner": 1467, "free_end": 0}]},
    ]
    resp = client.post(
        "/api/star_seize/seize/7fe98fc6",
        json={"pos": 2, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert reply["ok"] is False and reply["reason"] == "own-server"
    assert not any("加入搶佔" in c["js"] for c in calls)


def test_seize_pos_not_open_blocks_before_gate(client, seize_cdp):
    calls, responses = seize_cdp
    responses["queue"] = [
        _nav_ok(),
        {"opened": None},  # 找不到目標 pos 的 view
    ]
    resp = client.post(
        "/api/star_seize/seize/7fe98fc6",
        json={"pos": 3, "queue_type": 1, "my_server": 1467},
    )
    assert resp.status_code == 200
    reply = resp.get_json()["reply"]
    assert reply["ok"] is False and reply["reason"] == "pos-not-open"
    # 只跑到 OPEN(2 步),不讀 gate 也不 emit
    assert len(calls) == 2


def test_seize_open_js_carries_pos(client, seize_cdp):
    calls, responses = seize_cdp
    responses["queue"] = [_nav_ok(), {"opened": None}]
    client.post(
        "/api/star_seize/seize/7fe98fc6",
        json={"pos": 4, "queue_type": 1, "my_server": 1467},
    )
    open_js = calls[1]["js"]
    assert "var TARGET = 4" in open_js


# --- /state / /opponent -----------------------------------------------------
def test_state_valid_calls_cdp_once(client, spy_cdp):
    resp = client.get("/api/star_seize/state/7fe98fc6?my_server=1467")
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    assert "MY = 1467" in spy_cdp[0]["js"]


def test_state_includes_cooldown_fields_and_taiwan_tz(client, spy_cdp):
    resp = client.get("/api/star_seize/state/7fe98fc6?my_server=1467")
    assert resp.status_code == 200
    js = spy_cdp[0]["js"]
    assert "attack_cd_end_time" in js
    assert "defend_cd_end_time" in js
    assert "my_attack_cd_remaining" in js
    # 休戰改台灣時 +8h
    assert "8*3600" in js


def test_opponent_rejects_bad_pos(client):
    resp = client.get("/api/star_seize/opponent/7fe98fc6?pos=7")
    assert resp.status_code == 400


def test_opponent_valid_calls_cdp_once(client, spy_cdp):
    resp = client.get("/api/star_seize/opponent/7fe98fc6?pos=2")
    assert resp.status_code == 200
    assert len(spy_cdp) == 1
    assert "pos: 2" in spy_cdp[0]["js"]

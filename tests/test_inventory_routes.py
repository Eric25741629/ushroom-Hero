"""倉庫 blueprint (Task 3 神器附魔 + Task 4 守護靈) 路由測試。

晚綁定 monkeypatch 模式同 sibling blueprint 測試（``test_fly_pet_api``）：
路由透過 ``control_panel_app._cdp_json_response`` 屬性查找，故 patch façade 即可攔截，
不必碰真實 CDP / web socket。
"""
import importlib
import sys
import types


def _import_control_panel_app():
    """Import control_panel_app with heavy device/CNN deps stubbed (mirror fly_pet)."""
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


# --- route registration ----------------------------------------------------

def test_inventory_routes_registered():
    cpa = _import_control_panel_app()
    rules = {r.rule for r in cpa.app.url_map.iter_rules()}
    assert "/inventory" in rules
    assert "/api/spirit_list/<ip>" in rules
    assert "/api/artifact_gem_list/<ip>" in rules
    assert "/api/artifact_gem_action/<ip>" in rules


# --- spirit list (Task 4) ---------------------------------------------------

def test_spirit_list_calls_cdp_with_await_and_spirit_cmd(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)

    captured = {}

    def fake_cdp_json_response(ip, expression, await_promise=False, data_key="data"):
        captured["ip"] = ip
        captured["expression"] = expression
        captured["await_promise"] = await_promise
        captured["data_key"] = data_key
        return cpa.jsonify({"status": "ok", "data": {"spirits": []}})

    monkeypatch.setattr(cpa, "_cdp_json_response", fake_cdp_json_response)

    resp = client.get("/api/spirit_list/emulator-5554")

    assert resp.status_code == 200
    assert captured["ip"] == "emulator-5554"
    assert captured["await_promise"] is True
    assert captured["data_key"] == "data"
    # 守護靈 cmd 19713 (0x4D01) must be sent + hooked in the injected JS
    assert "19713" in captured["expression"]
    assert "sendMessage(19713" in captured["expression"]


# --- artifact gem list (Task 3) --------------------------------------------

def test_artifact_gem_list_calls_cdp_with_await_and_gem_cmd(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)

    captured = {}

    def fake_cdp_json_response(ip, expression, await_promise=False, data_key="data"):
        captured["ip"] = ip
        captured["expression"] = expression
        captured["await_promise"] = await_promise
        captured["data_key"] = data_key
        return cpa.jsonify({"status": "ok", "data": {"gems": []}})

    monkeypatch.setattr(cpa, "_cdp_json_response", fake_cdp_json_response)

    resp = client.get("/api/artifact_gem_list/emulator-5554")

    assert resp.status_code == 200
    assert captured["ip"] == "emulator-5554"
    assert captured["await_promise"] is True
    assert captured["data_key"] == "data"
    # 神器附魔 cmd 13569 (0x3501) must be sent + hooked in the injected JS
    assert "13569" in captured["expression"]
    assert "sendMessage(13569" in captured["expression"]


# --- action route is 501 pending (no destructive cmd until body captured) ---

def test_artifact_gem_action_returns_501_pending(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)

    # _cdp_json_response must NOT be touched by the pending action route.
    def boom(*args, **kwargs):  # pragma: no cover - asserts it is never called
        raise AssertionError("action route must not hit CDP while pending")

    monkeypatch.setattr(cpa, "_cdp_json_response", boom)

    resp = client.post(
        "/api/artifact_gem_action/emulator-5554",
        json={"action": "split", "ids": ["1", "2"]},
    )

    assert resp.status_code == 501
    body = resp.get_json()
    assert body["status"] == "pending"
    # the derived (not yet wired) cmd ids are surfaced for the caller
    assert body["cmds"]["split"] == 0x350A
    assert body["cmds"]["sub"] == 0x350B
    assert body["cmds"]["up"] == 0x3503


# --- auth: unauthenticated requests are rejected ----------------------------

def test_inventory_page_redirects_when_unauthenticated():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    # no fly_pet_auth in session
    resp = client.get("/inventory")
    assert resp.status_code in (301, 302)
    assert "/fly-pet/login" in resp.headers.get("Location", "")


def test_inventory_apis_return_401_when_unauthenticated():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    for path in (
        "/api/spirit_list/emulator-5554",
        "/api/artifact_gem_list/emulator-5554",
    ):
        resp = client.get(path)
        assert resp.status_code == 401, path
        assert resp.get_json()["status"] == "error"

    resp = client.post("/api/artifact_gem_action/emulator-5554", json={})
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"

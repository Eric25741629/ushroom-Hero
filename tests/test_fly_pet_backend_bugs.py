"""Backend-bug regression tests for fly-pet endpoints in control_panel_app.

Covers three fixes:
  Bug 9 - breed_start / breed_collect must wait for a cache state transition
           (bounded poll) instead of fire-and-forget _cdp_evaluate.
  Bug 6 - lock extraction JS must be DRY: a single shared _FLY_PET_LOCK_JS
           helper interpolated into both fly_pet_list and fly_pet_find_pair.
  Bug 5 - fly_pet_refresh_breed must BLOCK on the EggListBack event instead of
           fire-and-forget.

Tests mirror tests/test_fly_pet_api.py: same stub-based import, fly_pet_auth
session login, monkeypatched _cdp_json_response / _cdp_evaluate to capture the
JS expression + await_promise, then assert on the captured JS.
"""

import importlib
import sys
import types


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


def _login_fly_pet(client):
    with client.session_transaction() as sess:
        sess["fly_pet_auth"] = True


def _capture_json_response(cpa, monkeypatch):
    """Monkeypatch _cdp_json_response to capture its args; return the dict."""
    captured = {}

    def fake_cdp_json_response(ip, expression, await_promise=False, data_key="data"):
        captured["ip"] = ip
        captured["expression"] = expression
        captured["await_promise"] = await_promise
        captured["data_key"] = data_key
        return cpa.jsonify({"status": "ok", "data": {"ok": True}})

    monkeypatch.setattr(cpa, "_cdp_json_response", fake_cdp_json_response)
    return captured


# --------------------------------------------------------------------------
# Bug 9: breed_start
# --------------------------------------------------------------------------
def test_breed_start_waits_for_breeding_state(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post(
        "/api/fly_pet_breed_start/dev1",
        json={"base_id": 7, "fly_a_id": 11, "fly_b_id": 22},
    )

    assert resp.status_code == 200
    assert captured["ip"] == "dev1"
    assert captured["await_promise"] is True
    js = captured["expression"]
    # keeps the send literal with the three ids
    assert "send_66_27(7, 11, 22)" in js
    # is a bounded poll, not a bare one-liner
    assert "home_list" in js
    assert ".state" in js
    assert "setInterval" in js or "setTimeout" in js
    # resolves the documented shape
    assert "timed_out" in js
    # waits specifically for breeding state == 1
    assert "=== 1" in js or "== 1" in js


def test_breed_start_requires_all_params(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    _capture_json_response(cpa, monkeypatch)

    resp = client.post("/api/fly_pet_breed_start/dev1", json={"base_id": 7})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Bug 9: breed_collect
# --------------------------------------------------------------------------
def test_breed_collect_waits_for_state_to_leave_collectable(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post("/api/fly_pet_breed_collect/dev2", json={"base_id": 5})

    assert resp.status_code == 200
    assert captured["ip"] == "dev2"
    assert captured["await_promise"] is True
    js = captured["expression"]
    assert "send_66_28(5)" in js
    assert "home_list" in js
    assert ".state" in js
    assert "setInterval" in js or "setTimeout" in js
    assert "timed_out" in js
    # waits for state to move AWAY from collectable (2)
    assert "!== 2" in js or "!= 2" in js


def test_breed_collect_requires_base_id(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    _capture_json_response(cpa, monkeypatch)

    resp = client.post("/api/fly_pet_breed_collect/dev2", json={})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Bug 5: refresh_breed
# --------------------------------------------------------------------------
def test_refresh_breed_blocks_on_egg_list_back(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post("/api/fly_pet_refresh_breed/dev3", json={})

    assert resp.status_code == 200
    assert captured["ip"] == "dev3"
    assert captured["await_promise"] is True
    js = captured["expression"]
    assert "normalEvent.on('EggListBack', handler);" in js
    assert "normalEvent.off('EggListBack', handler);" in js
    assert "send_66_1()" in js
    assert "send_66_21()" in js
    assert "send_66_22()" in js
    # bounded timeout fallback
    assert "setTimeout" in js


# --------------------------------------------------------------------------
# Bug 6: shared lock helper
# --------------------------------------------------------------------------
def test_shared_lock_helper_exists_in_module_source():
    cpa = _import_control_panel_app()
    src = cpa.__file__
    with open(src, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    assert "_FLY_PET_LOCK_JS" in text
    # canonical rule: check ext for k===2, fall back to pet.lock
    assert "k===2" in cpa._FLY_PET_LOCK_JS or "k === 2" in cpa._FLY_PET_LOCK_JS
    assert "pet.lock" in cpa._FLY_PET_LOCK_JS


def test_fly_pet_list_uses_shared_lock_helper(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_list/dev4")
    assert resp.status_code == 200
    js = captured["expression"]
    # the shared helper body is interpolated into the list JS
    assert cpa._FLY_PET_LOCK_JS in js
    # list still reports both lock and star
    assert "lock:" in js
    assert "star:" in js


def test_fly_pet_list_fetches_collection_and_marks_model(monkeypatch):
    """列表刷新須讀 fly_pet_collection,並以 config_id 標記 is_collected。"""
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_list/dev4")

    assert resp.status_code == 200
    js = captured["expression"]
    assert captured["await_promise"] is True
    assert "send_66_32()" in js
    assert "FlyPeCollectionBack" in js
    assert "collection_list" in js
    assert "is_collected" in js
    assert "collectionIds.has(String(pet.config_id))" in js


def test_fly_pet_find_pair_uses_shared_lock_helper(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post(
        "/api/fly_pet_find_pair/dev5",
        json={"criteria": {"mode": "quality_count"}, "exclude_ids": []},
    )
    assert resp.status_code == 200
    js = captured["expression"]
    # the shared helper body is interpolated into the find_pair JS
    assert cpa._FLY_PET_LOCK_JS in js
    # still skips locked pets and fighting pets
    assert "continue" in js


def test_fly_pet_resolve_filters_collected_and_deployed_before_rpc(monkeypatch):
    """分解 RPC 只能收到未收藏、未上陣、未鎖定的 safeIds。"""
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post("/api/fly_pet_resolve/dev6", json={"ids": [101, 202]})

    assert resp.status_code == 200
    assert captured["ip"] == "dev6"
    assert captured["await_promise"] is True
    js = captured["expression"]
    assert "send_66_32()" in js
    assert "collection_list" in js
    assert "collectionIds.has(String(pet.config_id))" in js
    assert "pet.fight === 1" in js or "pet.fight == 1" in js
    assert "safeIds" in js
    assert "send_66_8(safeIds)" in js
    assert "send_66_8([101, 202])" not in js

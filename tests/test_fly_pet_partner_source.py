"""Regression tests: 搭檔飛寵 (breeding-partner pets) source + parsing.

Two live-verified bugs (device 7fe98fc6, FlyPetDataCache probe):

  Bug A - fly_pet_partner parsed the wrong RolePetListBack field. The protobuf
          payload carries the partner's shelved pets under ``data.list`` (an
          array of {info, state, ...}); the code read ``data.pet_list`` which is
          undefined, so every partner returned [] -> "搭檔飛寵沒有抓到".

  Bug B - the panel loaded 好友 (FriendModel.friendList, ~50) instead of 搭檔.
          The game's breeding partners live in FlyPetDataCache.role_list
          (= InitHybridData's partner_list, capped at 30). A dedicated
          /api/fly_pet_partners endpoint must read role_list, not the friend list.

Mirrors tests/test_fly_pet_backend_bugs.py: stub-based import, fly_pet_auth
session, monkeypatched _cdp_json_response to capture the JS expression.
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
# Bug A: fly_pet_partner must read RolePetListBack's `list` field
# --------------------------------------------------------------------------
def test_fly_pet_partner_reads_list_not_pet_list(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_partner/dev1?role_id=89551141870264")
    assert resp.status_code == 200
    js = captured["expression"]
    assert captured["await_promise"] is True
    # still triggers the right request + listens on the right event
    assert "send_66_24(89551141870264)" in js
    assert "RolePetListBack" in js
    # reads the payload's `list` array, NOT the non-existent `pet_list`
    assert "data.list" in js
    assert "data.pet_list" not in js
    # exposes cooldown info so the UI can tell borrowable from 冷卻中/繁殖中:
    # state (0=可用,1=繁殖中,2=冷卻中) + end_time countdown
    assert "state:" in js
    assert "end_time:" in js


def test_fly_pet_partner_requires_role_id(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_partner/dev1")
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Bug B: fly_pet_partners reads the 搭檔 role_list, not the friend list
# --------------------------------------------------------------------------
def test_fly_pet_partners_reads_role_list(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_partners/dev2")
    assert resp.status_code == 200
    assert captured["ip"] == "dev2"
    assert captured["await_promise"] is True
    assert captured["data_key"] == "partners"
    js = captured["expression"]
    # 搭檔 come from FlyPetDataCache.role_list (= InitHybridData partner_list)
    assert "FlyPetDataCache" in js
    assert "role_list" in js
    # must NOT fall back to the friend model
    assert "FriendModel" not in js
    assert "friendList" not in js
    # exposes role_id + name so the frontend can fetch each partner's pets
    assert "role_id" in js
    assert "name" in js


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

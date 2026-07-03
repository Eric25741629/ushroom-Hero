"""看廣告獎勵 blueprint 路由測試（純 WS 版）。

資料源走「純 WS（ws_session 持久連線 + ws_token.ad_reward orchestrator）」，
與 routes_inventory 同一套（共用 ws_session registry）。測試 stub 掉
``ws_session.get_client`` 與 ``ws_token.ad_reward`` 的 reader/orchestrator，
不碰真實 socket，驗證路由輸出（status JSON、claim 摘要、到上限略過、未連線 502、
auth 401）。鏡像 tests/test_inventory_routes.py。
"""
import importlib
import sys
import types


def _import_control_panel_app():
    """Import control_panel_app with heavy device/CNN deps stubbed (mirror inventory)."""
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


def _patch_live_client(monkeypatch):
    """讓 _session_client 拿到一個假的 live client（不連線）。"""
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: object())


# --- route registration ----------------------------------------------------

def test_ad_reward_routes_registered():
    cpa = _import_control_panel_app()
    rules = {r.rule for r in cpa.app.url_map.iter_rules()}
    assert "/api/ad_reward/status/<ip>" in rules
    assert "/api/ad_reward/claim/<ip>" in rules


# --- status：純 WS read_ad_counts -> count/cap/maxed -------------------------

def test_ad_reward_status_returns_counts_and_caps(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_ad_reward as rar

    # 12 maxed (3/3), 14 partial (2/5), 15 absent -> count 0/2.
    monkeypatch.setattr(rar.ws_ad, "read_ad_counts",
                        lambda c, **k: {12: {"count": 3, "next_ts": 0},
                                        14: {"count": 2, "next_ts": 0}})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/ad_reward/status/emulator-5554")

    assert resp.status_code == 200
    items = {it["config_id"]: it for it in resp.get_json()["data"]["items"]}
    assert items[12]["count"] == 3 and items[12]["cap"] == 3 and items[12]["maxed"] is True
    assert items[14]["count"] == 2 and items[14]["remaining"] == 3 and items[14]["maxed"] is False
    assert items[15]["count"] == 0 and items[15]["maxed"] is False
    # 中文名來自 ad_reward.AD_NAMES
    assert items[12]["name"] and items[15]["name"]


# --- claim：成功摘要（含到上限 skipped、claimed、total） --------------------

def test_ad_reward_claim_returns_summary(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_ad_reward as rar

    captured = {}

    def fake_claim_ads(client, config_ids, **k):
        captured["ids"] = list(config_ids)
        n = rar.ws_ad.AD_NAMES
        return {"results": {
            n[12]: {"name": n[12], "claimed": 2, "stopped": "remaining_zero"},
            n[14]: {"name": n[14], "skipped": "maxed 5/5"},
            n[15]: {"name": n[15], "claimed": 1, "stopped": None},
        }, "total_claimed": 3}

    monkeypatch.setattr(rar.ws_ad, "claim_ads", fake_claim_ads)

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/ad_reward/claim/emulator-5554", json={})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok" and body["total_claimed"] == 3
    # 預設 config_ids = [1,2,3,12,14,15]（含挖礦鎬子/鑽頭/炸彈）
    assert captured["ids"] == [1, 2, 3, 12, 14, 15]
    by_name = {r["name"]: r for r in body["results"]}
    n = rar.ws_ad.AD_NAMES
    assert by_name[n[12]]["claimed"] == 2 and by_name[n[12]]["config_id"] == 12
    assert by_name[n[14]]["skipped"] == "maxed 5/5"     # 到上限略過、不送請求
    assert by_name[n[15]]["claimed"] == 1


def test_ad_reward_claim_honours_custom_config_ids(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_ad_reward as rar

    captured = {}

    def fake_claim_ads(client, config_ids, **k):
        captured["ids"] = list(config_ids)
        return {"results": {}, "total_claimed": 0}

    monkeypatch.setattr(rar.ws_ad, "claim_ads", fake_claim_ads)

    client = cpa.app.test_client(); _login_fly_pet(client)
    # 只送 15 + 一個不存在的 99（會被過濾掉）
    resp = client.post("/api/ad_reward/claim/emulator-5554",
                       json={"config_ids": [15, 99]})
    assert resp.status_code == 200
    assert captured["ids"] == [15]


# --- 未連線：ws_session 連不上 -> 502 --------------------------------------

def test_ad_reward_claim_502_when_no_session(monkeypatch):
    cpa = _import_control_panel_app()
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: None)
    monkeypatch.setattr(ws_session, "ensure",
                        lambda ip: {"status": "error", "message": "WS 連線失敗"})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/ad_reward/claim/emulator-5554", json={})
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"


def test_ad_reward_status_502_when_no_session(monkeypatch):
    cpa = _import_control_panel_app()
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: None)
    monkeypatch.setattr(ws_session, "ensure",
                        lambda ip: {"status": "error", "message": "WS 連線失敗"})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/ad_reward/status/emulator-5554")
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"


# --- auth: unauthenticated requests are rejected ----------------------------

def test_ad_reward_apis_return_401_when_unauthenticated():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    resp = client.get("/api/ad_reward/status/emulator-5554")
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"

    resp = client.post("/api/ad_reward/claim/emulator-5554", json={})
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"

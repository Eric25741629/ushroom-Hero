"""倉庫 blueprint 路由測試（純 WS 版）。

資料源已從「CDP 注入瀏覽器」改為「純 WS（ws_session 持久連線 + ws_token reader）」。
測試 stub 掉 ``ws_session.get_client`` 與 ``ws_token`` reader/mutator，不碰真實 socket，
驗證路由輸出（中文名解析、賣石防呆）即可。
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


def _patch_live_client(monkeypatch):
    """讓 _session_client 拿到一個假的 live client（不連線）。"""
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: object())


# --- route registration ----------------------------------------------------

def test_inventory_routes_registered():
    cpa = _import_control_panel_app()
    rules = {r.rule for r in cpa.app.url_map.iter_rules()}
    assert "/inventory" in rules
    assert "/api/spirit_list/<ip>" in rules
    assert "/api/artifact_gem_list/<ip>" in rules
    assert "/api/artifact_gem_action/<ip>" in rules
    # 新增的純 WS session 端點
    assert "/api/ws_session/<ip>/connect" in rules
    assert "/api/ws_session/<ip>/ping" in rules
    assert "/api/ws_session/<ip>/disconnect" in rules


# --- spirit list：純 WS 讀 + 中文名解析 ------------------------------------

def test_spirit_list_returns_chinese_names(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from ws_token import spirit as ws_spirit
    from utils import config_names

    inv = ws_spirit.SpiritInventory(
        reset_times=0, reshape_times=3, tab=1,
        spirits=(ws_spirit.SpiritCard(
            id=111, config_id=101, level=9, is_lock=True,
            positions=(ws_spirit.SpiritPosition(
                pos=1, cur_id=5, cur_attrs={1001: 50}, reshape_id=0,
                reshape_attrs={}),)),))
    monkeypatch.setattr(ws_spirit, "read_spirit_info", lambda c, **k: inv)
    monkeypatch.setattr(config_names, "spirit_name", lambda c: "格鬥犬")
    monkeypatch.setattr(config_names, "attr_name", lambda a: "攻擊")

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/spirit_list/emulator-5554")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["count"] == 1 and data["reshape_times"] == 3
    s = data["spirits"][0]
    assert s["name"] == "格鬥犬" and s["lock"] == 1 and s["level"] == 9
    assert s["positions"][0]["affixes"][0]["name"] == "攻擊"


# --- gem list：純 WS 讀 + 套裝/品質中文名 + is_equipped --------------------

def test_artifact_gem_list_resolves_names_and_equipped(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from ws_token import artifact_gem as ws_gem
    from utils import config_names

    inv = ws_gem.ArtifactGemInventory(
        tab=5,
        gems=(
            ws_gem.ArtifactGem(id=100, quality=7, pos=2, suit=104, lv=9, exp=0,
                               is_red=False, is_lock=False, base_attr={1001: 5},
                               rand_attr={}),
            ws_gem.ArtifactGem(id=200, quality=3, pos=4, suit=101, lv=1, exp=0,
                               is_red=False, is_lock=False, base_attr={}, rand_attr={}),
        ),
        equipped_ids=frozenset({100}))
    monkeypatch.setattr(ws_gem, "read_gem_info", lambda c, **k: inv)
    monkeypatch.setattr(config_names, "suit_name", lambda s: f"套{s}")
    monkeypatch.setattr(config_names, "quality_name", lambda q: f"品{q}")
    monkeypatch.setattr(config_names, "attr_name", lambda a: f"屬{a}")

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/artifact_gem_list/emulator-5554")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["count"] == 2 and data["equipped"] == 1
    by_id = {g["id"]: g for g in data["gems"]}
    assert by_id["100"]["is_equipped"] is True and by_id["100"]["suit_name"] == "套104"
    assert by_id["100"]["quality_name"] == "品7"
    assert by_id["200"]["is_equipped"] is False


# --- action：賣石防呆（排除 equipped/locked）+ 只送安全子集 ----------------

def test_artifact_gem_action_guards_equipped_and_locked(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from ws_token import artifact_gem as ws_gem

    inv = ws_gem.ArtifactGemInventory(
        tab=1,
        gems=(
            ws_gem.ArtifactGem(id=1, quality=3, pos=1, suit=101, lv=2, exp=0,
                               is_red=False, is_lock=False, base_attr={}, rand_attr={}),
            ws_gem.ArtifactGem(id=2, quality=3, pos=1, suit=101, lv=1, exp=0,
                               is_red=False, is_lock=True, base_attr={}, rand_attr={}),
            ws_gem.ArtifactGem(id=3, quality=3, pos=1, suit=101, lv=1, exp=0,
                               is_red=False, is_lock=False, base_attr={}, rand_attr={}),
        ),
        equipped_ids=frozenset({3}))
    monkeypatch.setattr(ws_gem, "read_gem_info", lambda c, **k: inv)

    sent = {}

    def fake_decompose(c, ids, **k):
        sent["ids"] = list(ids)
        return {"ok": True, "error_code": 0, "removed": list(ids)}

    monkeypatch.setattr(ws_gem, "decompose", fake_decompose)

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/artifact_gem_action/emulator-5554",
                       json={"action": "split", "ids": ["1", "2", "3"]})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert sent["ids"] == [1]            # 只有未鎖未裝備的 1 被送出
    assert body["decomposed"] == [1]
    assert body["blocked"] == {"2": "locked", "3": "equipped"}


def test_artifact_gem_action_all_blocked_returns_400(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from ws_token import artifact_gem as ws_gem

    inv = ws_gem.ArtifactGemInventory(
        tab=1,
        gems=(ws_gem.ArtifactGem(id=2, quality=3, pos=1, suit=101, lv=1, exp=0,
                                 is_red=False, is_lock=True, base_attr={}, rand_attr={}),),
        equipped_ids=frozenset())
    monkeypatch.setattr(ws_gem, "read_gem_info", lambda c, **k: inv)

    def boom(*a, **k):
        raise AssertionError("全部被擋時不可呼叫 decompose")

    monkeypatch.setattr(ws_gem, "decompose", boom)

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/artifact_gem_action/emulator-5554",
                       json={"action": "split", "ids": ["2"]})
    assert resp.status_code == 400
    assert resp.get_json()["blocked"] == {"2": "locked"}


# --- auth: unauthenticated requests are rejected ----------------------------

def test_inventory_page_redirects_when_unauthenticated():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
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

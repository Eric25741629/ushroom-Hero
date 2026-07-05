"""坐騎追蹤器 blueprint (control_panel/routes_mount_tracker.py) 路由測試。

hermetic 策略：不 import 真實 ``control_panel_app`` / 服務層 / 設定檔。改建一個
最小 ``flask.Flask`` app 只註冊本 blueprint，並 monkeypatch 三個間接口
(``_store`` / ``_get_enabled`` / ``_set_enabled``)。認證走真實 decorator
(``_fly_pet_auth`` / ``require_admin``)：它們只讀 session，用 ``session_transaction``
直接塞 ``dash_user`` / ``dash_admin`` 即可，無需全站 before_request。
"""

import flask
import pytest

from control_panel import routes_mount_tracker as rmt


# --- 假 store（記錄呼叫，不碰真實服務） --------------------------------------

class FakeStore:
    def __init__(self, known=None, snap=None):
        self.known = known or {}
        self.added: list[dict] = []
        self.removed: list[int] = []
        self.marked: list[tuple] = []
        self._snap = snap if snap is not None else {
            "targets": [],
            "results": {},
            "known_count": len(self.known),
            "last_run": None,
            "running": False,
        }

    def snapshot(self):
        return dict(self._snap)

    def add_target(self, d):
        self.added.append(d)

    def remove_target(self, rid):
        self.removed.append(rid)

    def get_known(self):
        return self.known

    def mark_attacked(self, target_role_id, owner_role_id, start_time, on=True):
        self.marked.append((target_role_id, owner_role_id, start_time, on))


@pytest.fixture
def app():
    a = flask.Flask(__name__)
    a.secret_key = "test-secret"
    a.register_blueprint(rmt.bp)
    return a


def _login(client, admin=False):
    with client.session_transaction() as sess:
        sess["dash_user"] = "boss"
        sess["dash_admin"] = admin


def _use_store(monkeypatch, store):
    monkeypatch.setattr(rmt, "_store", lambda: store)


# --- 模組在服務層缺席時仍可 import ------------------------------------------

def test_module_imports_cleanly():
    # 服務層走延遲 import（包在間接函式內），故本模組 import 時不依賴服務層是否存在，
    # 兩分支合併前後皆能載入。確認 blueprint 正常建立即可。
    assert rmt.bp.name == "mount_tracker"


# --- results 信封 -----------------------------------------------------------

def test_results_envelope(app, monkeypatch):
    store = FakeStore(snap={
        "targets": [{"role_id": 111, "name": "A"}],
        "results": {"111": {"mount": "x"}},
        "known_count": 3,
        "last_run": 1234,
        "running": True,
    })
    _use_store(monkeypatch, store)
    monkeypatch.setattr(rmt, "_get_enabled", lambda: True)

    client = app.test_client()
    _login(client)
    resp = client.get("/api/mount_tracker/results")

    assert resp.status_code == 200
    d = resp.get_json()
    assert d["status"] == "ok"
    assert d["enabled"] is True
    assert d["targets"] == [{"role_id": 111, "name": "A"}]
    assert d["results"] == {"111": {"mount": "x"}}
    assert d["known_count"] == 3
    assert d["last_run"] == 1234
    assert d["running"] is True


def test_results_requires_login(app, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    monkeypatch.setattr(rmt, "_get_enabled", lambda: False)
    resp = app.test_client().get("/api/mount_tracker/results")
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"


# --- targets：直接 roleId 新增 + 移除 --------------------------------------

def test_add_by_role_id(app, monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets",
                       json={"role_id": 777, "name": "Bob", "uid": "ZZZZZ"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.added == [{"role_id": 777, "name": "Bob", "uid": "ZZZZZ"}]


def test_remove_target(app, monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets", json={"remove": 777})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.removed == [777]


def test_targets_missing_all_fields(app, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    client = app.test_client()
    _login(client)
    resp = client.post("/api/mount_tracker/targets", json={})
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"


# --- targets：離線以 UID 解析 ----------------------------------------------

def test_add_by_uid_single_match(app, monkeypatch):
    # roleId 0xABCDE -> low 20bit "ABCDE" -> _uid_of = C A D B E = "CADBE"
    assert rmt._uid_of(0xABCDE) == "CADBE"
    store = FakeStore(known={0xABCDE: {"name": "Alice"}})
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    # 大小寫不敏感（用小寫查）。
    resp = client.post("/api/mount_tracker/targets", json={"uid": "cadbe"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.added == [{"role_id": 0xABCDE, "name": "Alice", "uid": "cadbe"}]


def test_add_by_uid_no_match(app, monkeypatch):
    store = FakeStore(known={0xABCDE: {"name": "Alice"}})
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets", json={"uid": "00000"})
    assert resp.status_code == 404
    d = resp.get_json()
    assert d["status"] == "error"
    assert "UID" in d["message"]
    assert store.added == []


def test_add_by_uid_multiple_matches_returns_candidates(app, monkeypatch):
    # 合服：高位不同、低 20bit 相同 -> 同一 UID -> 多筆候選。
    store = FakeStore(known={
        0xABCDE: {"name": "Alice"},
        0x1ABCDE: {"name": "Alice2"},
    })
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets", json={"uid": "CADBE"})
    assert resp.status_code == 409
    d = resp.get_json()
    assert d["status"] == "error"
    cand_ids = {c["role_id"] for c in d["candidates"]}
    assert cand_ids == {0xABCDE, 0x1ABCDE}
    assert store.added == []


# --- targets：離線以名稱解析 -----------------------------------------------

def test_add_by_name_single_match(app, monkeypatch):
    store = FakeStore(known={123: {"name": "Carol"}, 456: {"name": "Dave"}})
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets", json={"name": "Carol"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.added == [{"role_id": 123, "name": "Carol", "uid": rmt._uid_of(123)}]


def test_add_by_name_multiple_matches(app, monkeypatch):
    store = FakeStore(known={123: {"name": "Same"}, 456: {"name": "Same"}})
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/targets", json={"name": "Same"})
    assert resp.status_code == 409
    d = resp.get_json()
    assert d["status"] == "error"
    assert {c["role_id"] for c in d["candidates"]} == {123, 456}


# --- mark：標記 / 取消標記已打掉 -------------------------------------------

def test_mark_records_call_default_on(app, monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/mark",
                       json={"target_role_id": 100, "owner_role_id": 5, "start_time": 111})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.marked == [(100, 5, 111, True)]     # on 預設 True


def test_mark_off(app, monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/mark",
                       json={"target_role_id": 100, "owner_role_id": 5,
                             "start_time": 111, "on": False})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert store.marked == [(100, 5, 111, False)]


def test_mark_missing_fields_error(app, monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    client = app.test_client()
    _login(client)

    resp = client.post("/api/mount_tracker/mark", json={"target_role_id": 100})
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "error"
    assert store.marked == []                         # 未觸及 store


def test_mark_requires_login(app, monkeypatch):
    _use_store(monkeypatch, FakeStore())
    resp = app.test_client().post("/api/mount_tracker/mark",
                                  json={"target_role_id": 1, "owner_role_id": 2, "start_time": 3})
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"


# --- toggle：僅管理員 -------------------------------------------------------

def test_toggle_non_admin_forbidden(app, monkeypatch):
    calls = []
    monkeypatch.setattr(rmt, "_set_enabled", lambda v: calls.append(v))
    client = app.test_client()
    _login(client, admin=False)

    resp = client.post("/api/mount_tracker/toggle", json={"enabled": True})
    assert resp.status_code == 403
    assert resp.get_json()["status"] == "error"
    assert calls == []  # 未觸及設定寫入


def test_toggle_admin_ok(app, monkeypatch):
    calls = []
    monkeypatch.setattr(rmt, "_set_enabled", lambda v: calls.append(v))
    client = app.test_client()
    _login(client, admin=True)

    resp = client.post("/api/mount_tracker/toggle", json={"enabled": True})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["status"] == "ok"
    assert d["enabled"] is True
    assert calls == [True]

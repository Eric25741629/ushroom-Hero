"""遺物碎片均勻升級(衝刺) blueprint 路由測試（純 WS 兩階段 plan→execute）。

資料源走「純 WS（ws_session 持久連線 + ws_token.relic_sprint orchestrator）」，
與 routes_ad_reward / routes_inventory 同一套（共用 ws_session registry）。測試
stub 掉 ``ws_session.get_client`` 與 ``ws_token.relic_sprint`` / ``ws_token.relic``
的 reader/orchestrator，不碰真實 socket，驗證路由輸出（plan 預覽結構 = 有活動/
無活動/碎片足夠與否、execute 摘要、未連線 502、auth 401、blueprint 註冊）。
鏡像 tests/test_ad_reward_routes.py。
"""
import importlib
import sys
import types


def _import_control_panel_app():
    """Import control_panel_app with heavy device/CNN deps stubbed (mirror ad_reward)."""
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
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True


def _patch_live_client(monkeypatch):
    """讓 _session_client 拿到一個假的 live client（不連線）。

    client.set_push_handler 會被路由呼叫一次（補掛 tracker），所以假 client 要有它。
    """
    from control_panel import ws_session

    class _FakeClient:
        def set_push_handler(self, handler):
            pass

    monkeypatch.setattr(ws_session, "get_client", lambda ip: _FakeClient())


class _FakeRelicInfo:
    """ws_relic.read_relics 回傳值替身：只需要 .equipped。"""

    def __init__(self, equipped):
        self.equipped = equipped


# --- route registration ----------------------------------------------------

def test_relic_sprint_routes_registered():
    cpa = _import_control_panel_app()
    rules = {r.rule for r in cpa.app.url_map.iter_rules()}
    assert "/api/relic_sprint/plan/<ip>" in rules
    assert "/api/relic_sprint/execute/<ip>" in rules


# --- plan：有活動，碎片足夠 -------------------------------------------------

def test_plan_open_with_enough_fragments(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_relic_sprint as rrs

    monkeypatch.setattr(rrs.ws_sprint, "find_active_act_type", lambda c, **k: 269)
    monkeypatch.setattr(rrs.ws_sprint, "read_sprint", lambda c, at, **k: {
        "open": True, "act_type": 269, "accrued": 450_000,
        "claimable_rounds": [2],
        # 新模型:rounds = 4 個 stage round(small_group_id 1..4),_rounds_view 讀這個。
        "rounds": [
            {"small_group_id": 1, "task_id": 269129, "status": 2, "done_subtasks": 7},  # 已領
            {"small_group_id": 2, "task_id": 269130, "status": 1, "done_subtasks": 7},  # 可領
            {"small_group_id": 3, "task_id": 269131, "status": 0, "done_subtasks": 3},  # 未達
            {"small_group_id": 4, "task_id": 269132, "status": 0, "done_subtasks": 0},
        ],
        "tasks": [{"task_id": 269101 + i, "status": 0, "count": 450_000}
                  for i in range(28)],  # 28 milestone 子任務(非輪)
    })
    monkeypatch.setattr(rrs.ws_relic, "read_relics",
                        lambda c, **k: _FakeRelicInfo([object(), object()]))
    # tracker 有碎片現量 (>= remaining 450K) -> enough True
    rrs._trackers.clear()
    tr = rrs._tracker_for("emulator-5554")
    tr.counts[rrs.ws_relic.RELIC_FRAGMENT_ITEM] = 600_000

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/relic_sprint/plan/emulator-5554")

    assert resp.status_code == 200
    d = resp.get_json()["data"]
    assert d["open"] is True and d["act_type"] == 269
    assert d["accrued"] == 450_000 and d["total"] == 900_000
    assert d["remaining"] == 450_000
    assert d["fragments_now"] == 600_000 and d["enough"] is True
    assert d["claimable_rounds"] == [2]
    assert len(d["rounds"]) == 4
    assert d["rounds"][0]["status_name"] == "已領"
    assert d["rounds"][1]["status_name"] == "可領"
    assert d["rounds"][2]["threshold"] == 675_000
    assert d["equipped_count"] == 2 and d["note"]


# --- plan：碎片現量未知（tracker 沒收到帶 100022 的 push）-> fragments_now None

def test_plan_fragments_unknown_when_tracker_empty(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_relic_sprint as rrs

    monkeypatch.setattr(rrs.ws_sprint, "find_active_act_type", lambda c, **k: 13)
    monkeypatch.setattr(rrs.ws_sprint, "read_sprint", lambda c, at, **k: {
        "open": True, "act_type": 13, "accrued": 0, "claimable_rounds": [],
        "tasks": [{"task_id": 1001, "status": 0, "count": 0}],
    })
    monkeypatch.setattr(rrs.ws_relic, "read_relics",
                        lambda c, **k: _FakeRelicInfo([]))
    rrs._trackers.clear()  # 全新 tracker，counts 空 -> 碎片未知

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/relic_sprint/plan/emulator-5554")

    assert resp.status_code == 200
    d = resp.get_json()["data"]
    assert d["open"] is True
    assert d["fragments_now"] is None and d["enough"] is False
    assert d["remaining"] == 900_000  # accrued 0 -> 全額
    # tasks 只有 1 個，rounds 仍補滿 4 輪
    assert len(d["rounds"]) == 4
    assert d["rounds"][3]["status_name"] == "未達"


# --- plan：無活動 -----------------------------------------------------------

def test_plan_no_active_sprint(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_relic_sprint as rrs

    monkeypatch.setattr(rrs.ws_sprint, "find_active_act_type", lambda c, **k: None)

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/relic_sprint/plan/emulator-5554")

    assert resp.status_code == 200
    d = resp.get_json()["data"]
    assert d["open"] is False and "無遺物衝刺" in d["msg"]


# --- execute：完整流程摘要 --------------------------------------------------

def test_execute_returns_summary(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_relic_sprint as rrs

    captured = {}

    def fake_run(client, tracker, *, target_spend, enabled, **k):
        captured["target_spend"] = target_spend
        captured["enabled"] = enabled
        return {
            "act_type": 269,
            "spent": 450_000,
            "spend_stopped": "target_reached",
            "frag_unknown": False,
            "claimed_rounds": [3, 4],
            "sprint_before": {"open": True, "accrued": 450_000},
            "sprint_after": {"open": True, "accrued": 900_000, "rounds": [
                {"small_group_id": 1, "task_id": 269129, "status": 2, "done_subtasks": 7},
                {"small_group_id": 2, "task_id": 269130, "status": 2, "done_subtasks": 7},
                {"small_group_id": 3, "task_id": 269131, "status": 2, "done_subtasks": 7},
                {"small_group_id": 4, "task_id": 269132, "status": 2, "done_subtasks": 7},
            ]},
        }

    monkeypatch.setattr(rrs.ws_sprint, "run_relic_sprint", fake_run)

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/relic_sprint/execute/emulator-5554", json={})

    assert resp.status_code == 200
    d = resp.get_json()["data"]
    assert d["open"] is True and d["act_type"] == 269
    assert d["spent"] == 450_000 and d["claimed_rounds"] == [3, 4]
    assert d["accrued_before"] == 450_000 and d["accrued_after"] == 900_000
    assert d["stopped"] == "target_reached" and d["frag_unknown"] is False
    assert len(d["rounds"]) == 4 and d["rounds"][0]["status_name"] == "已領"
    # 路由用 SPRINT_TOTAL 當 target、enabled=True
    assert captured["target_spend"] == rrs.ws_sprint.SPRINT_TOTAL
    assert captured["enabled"] is True


# --- execute：無活動 -> skipped 安全略過 ------------------------------------

def test_execute_no_active_sprint_skips(monkeypatch):
    cpa = _import_control_panel_app()
    _patch_live_client(monkeypatch)
    from control_panel import routes_relic_sprint as rrs

    monkeypatch.setattr(rrs.ws_sprint, "run_relic_sprint",
                        lambda c, t, **k: {"skipped": "no active sprint"})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/relic_sprint/execute/emulator-5554", json={})

    assert resp.status_code == 200
    d = resp.get_json()["data"]
    assert d["open"] is False and d["skipped"] == "no active sprint"
    assert "無遺物衝刺" in d["msg"]


# --- 未連線：ws_session 連不上 -> 502 --------------------------------------

def test_plan_502_when_no_session(monkeypatch):
    cpa = _import_control_panel_app()
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: None)
    monkeypatch.setattr(ws_session, "ensure",
                        lambda ip: {"status": "error", "message": "WS 連線失敗"})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.get("/api/relic_sprint/plan/emulator-5554")
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"


def test_execute_502_when_no_session(monkeypatch):
    cpa = _import_control_panel_app()
    from control_panel import ws_session
    monkeypatch.setattr(ws_session, "get_client", lambda ip: None)
    monkeypatch.setattr(ws_session, "ensure",
                        lambda ip: {"status": "error", "message": "WS 連線失敗"})

    client = cpa.app.test_client(); _login_fly_pet(client)
    resp = client.post("/api/relic_sprint/execute/emulator-5554", json={})
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"


# --- _trackers LRU size cap (記憶體：per-ip dict 不可無限增長) ----------------

def test_tracker_registry_is_size_capped():
    cpa = _import_control_panel_app()  # ensure module importable
    del cpa
    from control_panel import routes_relic_sprint as rrs
    rrs._trackers.clear()
    # create more trackers than the cap -> registry stays bounded, oldest evicted
    for i in range(rrs._MAX_TRACKERS + 10):
        rrs._tracker_for(f"dev-{i}")
    assert len(rrs._trackers) == rrs._MAX_TRACKERS
    # the earliest-created ids were evicted; the most recent are retained
    assert "dev-0" not in rrs._trackers
    assert f"dev-{rrs._MAX_TRACKERS + 9}" in rrs._trackers
    rrs._trackers.clear()


def test_tracker_registry_lru_touches_keep_recent_alive():
    cpa = _import_control_panel_app()
    del cpa
    from control_panel import routes_relic_sprint as rrs
    rrs._trackers.clear()
    keep = rrs._tracker_for("keep-me")
    # fill up to the cap with fresh ids
    for i in range(rrs._MAX_TRACKERS - 1):
        rrs._tracker_for(f"filler-{i}")
    # re-touch keep-me so it becomes most-recently-used, then push one more in
    again = rrs._tracker_for("keep-me")
    assert again is keep  # same instance (not rebuilt)
    rrs._tracker_for("one-more")
    # keep-me survived eviction because it was touched; an early filler got evicted
    assert "keep-me" in rrs._trackers
    assert "filler-0" not in rrs._trackers
    assert len(rrs._trackers) == rrs._MAX_TRACKERS
    rrs._trackers.clear()


# --- auth: unauthenticated requests are rejected ----------------------------

def test_relic_sprint_apis_return_401_when_unauthenticated():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    resp = client.get("/api/relic_sprint/plan/emulator-5554")
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"

    resp = client.post("/api/relic_sprint/execute/emulator-5554", json={})
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "error"

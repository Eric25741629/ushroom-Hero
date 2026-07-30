"""飛寵 routes 純 WS regression tests。"""
import importlib
import sys
import types

from ws_token import fly_pet


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


def _login(client):
    with client.session_transaction() as sess:
        sess["fly_pet_auth"] = True
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True


def _client(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login(client)
    import control_panel.routes_fly_pet as routes
    monkeypatch.setattr(routes, "_session_client", lambda ip: (object(), None))
    return client, routes


def test_breed_start_returns_server_state(monkeypatch):
    client, routes = _client(monkeypatch)
    seen = {}

    def start(_client, base_id, fly_a_id, fly_b_id):
        seen["args"] = (base_id, fly_a_id, fly_b_id)
        return fly_pet.Base(id=base_id, state=1)

    monkeypatch.setattr(routes.ws_fly_pet, "start_breeding", start)
    resp = client.post("/api/fly_pet_breed_start/dev1", json={
        "base_id": 7, "fly_a_id": 11, "fly_b_id": 22,
    })
    assert resp.status_code == 200
    assert seen["args"] == (7, 11, 22)
    assert resp.get_json()["data"]["state"] == 1


def test_breed_start_requires_all_params(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.post("/api/fly_pet_breed_start/dev1",
                       json={"base_id": 7}).status_code == 400


def test_breed_collect_returns_new_egg_ids(monkeypatch):
    client, routes = _client(monkeypatch)
    monkeypatch.setattr(
        routes.ws_fly_pet,
        "collect_breeding",
        lambda _client, base_id: (
            fly_pet.Base(id=base_id, state=3),
            [fly_pet.Egg(id=901)],
        ),
    )
    resp = client.post("/api/fly_pet_breed_collect/dev2", json={"base_id": 5})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["egg_ids"] == [901]


def test_breed_collect_requires_base_id(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.post("/api/fly_pet_breed_collect/dev2", json={}).status_code == 400


def test_refresh_breed_waits_for_all_three_ws_snapshots(monkeypatch):
    client, routes = _client(monkeypatch)
    seen = {"count": 0}

    def read(_client):
        seen["count"] += 1
        return fly_pet.BreedSnapshot((), (), (), (), ())

    monkeypatch.setattr(routes.ws_fly_pet, "read_breed_snapshot", read)
    resp = client.post("/api/fly_pet_refresh_breed/dev3", json={})
    assert resp.status_code == 200
    assert seen["count"] == 1
    assert resp.get_json()["data"]["timed_out"] is False


def test_fly_pet_list_marks_lock_star_and_collection(monkeypatch):
    client, routes = _client(monkeypatch)
    pet = fly_pet.Pet(
        id=1, config_id=1001, level=5, quality=4,
        ext={1: 1, 2: 1},
    )
    monkeypatch.setattr(
        routes.ws_fly_pet,
        "read_snapshot",
        lambda _client: fly_pet.Snapshot((pet,), {1001}),
    )
    resp = client.get("/api/fly_pet_list/dev4")
    item = resp.get_json()["pets"][0]
    assert item["lock"] == 1
    assert item["star"] == 1
    assert item["is_collected"] is True


def test_find_pair_skips_locked_and_deployed(monkeypatch):
    client, routes = _client(monkeypatch)
    pets = (
        fly_pet.Pet(id=1, config_id=1001,
                    entries=(fly_pet.Entry(10, 1), fly_pet.Entry(11, 1),
                             fly_pet.Entry(12, 1))),
        fly_pet.Pet(id=2, config_id=1001,
                    entries=(fly_pet.Entry(10, 1), fly_pet.Entry(11, 1),
                             fly_pet.Entry(12, 1))),
        fly_pet.Pet(id=3, config_id=1001, ext={2: 1},
                    entries=(fly_pet.Entry(10, 1),) * 3),
        fly_pet.Pet(id=4, config_id=1001, fight=1,
                    entries=(fly_pet.Entry(10, 1),) * 3),
    )
    monkeypatch.setattr(
        routes.ws_fly_pet, "read_snapshot",
        lambda _client: fly_pet.Snapshot(pets, set()),
    )
    monkeypatch.setattr(
        routes.ws_fly_pet, "read_breed_snapshot",
        lambda _client: fly_pet.BreedSnapshot((), (), (), (), ()),
    )
    resp = client.post("/api/fly_pet_find_pair/dev5", json={
        "criteria": {"mode": "total", "min_total_entries": 3},
    })
    data = resp.get_json()["data"]
    assert data["candidates_count"] == 2
    assert {data["pair"]["fly_a"]["id"], data["pair"]["fly_b"]["id"]} == {1, 2}


def test_resolve_route_uses_server_side_safe_selection(monkeypatch):
    client, routes = _client(monkeypatch)
    seen = {}

    def resolve(_client, ids):
        seen["ids"] = ids
        return fly_pet.Snapshot((
            fly_pet.Pet(id=101),
            fly_pet.Pet(id=202, ext={2: 1}),
        ), set()), [101], {"locked": 1, "collected": 0,
                         "deployed": 0, "missing": 0}

    monkeypatch.setattr(routes.ws_fly_pet, "resolve_pets", resolve)
    resp = client.post("/api/fly_pet_resolve/dev6", json={"ids": [101, 202]})
    assert resp.status_code == 200
    assert seen["ids"] == [101, 202]
    assert resp.get_json()["data"]["skipped"]["locked"] == 1

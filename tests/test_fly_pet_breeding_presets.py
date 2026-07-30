"""飛寵純 WS 配種方案與本地 catalog tests。"""
import importlib
import sys
import types

from ws_token import fly_pet


def _client(monkeypatch, pets=()):
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
    cpa = importlib.import_module("control_panel_app")
    client = cpa.app.test_client()
    with client.session_transaction() as sess:
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True
    import control_panel.routes_fly_pet as routes
    monkeypatch.setattr(routes, "_session_client", lambda ip: (object(), None))
    monkeypatch.setattr(
        routes.ws_fly_pet, "read_snapshot",
        lambda _client: fly_pet.Snapshot(tuple(pets), set()),
    )
    monkeypatch.setattr(
        routes.ws_fly_pet, "read_breed_snapshot",
        lambda _client: fly_pet.BreedSnapshot((), (), (), (), ()),
    )
    return client, routes


def _pet(pet_id, species, entries=(5, 6, 7)):
    return fly_pet.Pet(
        id=pet_id,
        config_id=species,
        entries=tuple(fly_pet.Entry(entry_id, 1) for entry_id in entries),
    )


def test_find_pair_species_whitelist_filters_by_config_id(monkeypatch):
    client, _ = _client(monkeypatch, [
        _pet(1, 101), _pet(2, 101), _pet(3, 999),
    ])
    resp = client.post("/api/fly_pet_find_pair/dev1", json={
        "criteria": {
            "mode": "total",
            "min_total_entries": 3,
            "species_whitelist": [101],
        },
    })
    data = resp.get_json()["data"]
    assert data["candidates_count"] == 2
    assert data["pair"]["fly_a"]["config_id"] == 101
    assert data["pair"]["fly_b"]["config_id"] == 101


def test_find_pair_empty_species_whitelist_does_not_filter(monkeypatch):
    client, _ = _client(monkeypatch, [_pet(1, 101), _pet(2, 999)])
    resp = client.post("/api/fly_pet_find_pair/dev1", json={
        "criteria": {"mode": "total", "min_total_entries": 3},
    })
    assert resp.get_json()["data"]["candidates_count"] == 2


def test_find_pair_entry_whitelist_requires_all_entries(monkeypatch):
    client, _ = _client(monkeypatch, [
        _pet(1, 101, (5, 6, 7)),
        _pet(2, 101, (5, 6, 8)),
        _pet(3, 101, (5, 8, 9)),
    ])
    resp = client.post("/api/fly_pet_find_pair/dev1", json={
        "criteria": {
            "mode": "total",
            "min_total_entries": 3,
            "entry_whitelist": [5, 6],
        },
    })
    assert resp.get_json()["data"]["candidates_count"] == 2


def test_catalog_endpoint_reads_local_config_without_ws_or_cdp(monkeypatch):
    client, routes = _client(monkeypatch)
    monkeypatch.setattr(routes, "_load_catalog", lambda: {
        "species": [{"id": 1001, "name": "月光精靈"}],
        "entries": [{"id": 301, "level": 1, "name": "採礦", "quality": 5}],
    })
    resp = client.get("/api/fly_pet_catalog/dev9")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["species"][0]["name"] == "月光精靈"


def test_catalog_endpoint_dedupes_entries_by_id(monkeypatch):
    client, routes = _client(monkeypatch)
    monkeypatch.setattr(routes, "_load_catalog", lambda: {
        "species": [],
        "entries": [
            {"id": 301, "level": 1, "name": "採礦", "quality": 5},
            {"id": 301, "level": 2, "name": "採礦", "quality": 5},
        ],
    })
    entries = client.get("/api/fly_pet_catalog/dev9").get_json()["data"]["entries"]
    assert entries == [{"id": 301, "name": "採礦", "quality": 5}]


def test_catalog_endpoint_requires_auth():
    cpa = importlib.import_module("control_panel_app")
    client = cpa.app.test_client()
    assert client.get("/api/fly_pet_catalog/dev9").status_code in (401, 403)

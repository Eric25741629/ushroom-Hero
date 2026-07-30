"""搭檔飛寵資料必須來自純 WS hybrid RPC。"""
import importlib
import sys
import types

from ws_token import fly_pet


def _app_client(monkeypatch):
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
    return client, routes


def test_fly_pet_partner_uses_role_id_and_preserves_cooldown(monkeypatch):
    client, routes = _app_client(monkeypatch)
    seen = {}

    def read(_client, role_id):
        seen["role_id"] = role_id
        return [fly_pet.Shelf(
            info=fly_pet.Pet(id=8, config_id=1001),
            state=2,
            end_time=123456,
        )]

    monkeypatch.setattr(routes.ws_fly_pet, "read_partner_shelves", read)
    resp = client.get("/api/fly_pet_partner/dev1?role_id=89551141870264")
    assert resp.status_code == 200
    assert seen["role_id"] == 89551141870264
    assert resp.get_json()["data"][0]["state"] == 2
    assert resp.get_json()["data"][0]["end_time"] == 123456


def test_fly_pet_partner_requires_role_id(monkeypatch):
    client, _ = _app_client(monkeypatch)
    assert client.get("/api/fly_pet_partner/dev1").status_code == 400


def test_fly_pet_partners_uses_hybrid_partner_list(monkeypatch):
    client, routes = _app_client(monkeypatch)
    snapshot = fly_pet.BreedSnapshot(
        (), (), (fly_pet.Partner(99, "搭檔甲"),), (), ()
    )
    monkeypatch.setattr(
        routes.ws_fly_pet, "read_breed_snapshot", lambda _client: snapshot
    )
    resp = client.get("/api/fly_pet_partners/dev2")
    assert resp.status_code == 200
    assert resp.get_json()["partners"] == [{
        "role_id": 99, "name": "搭檔甲", "head": 0,
    }]

"""TDD tests for flypet species grouping + breeding presets feature.

Mirrors tests/test_fly_pet_backend_bugs.py: stub-based import, fly_pet_auth
session, monkeypatched _cdp_json_response to capture the generated JS, then
assert on the captured JS string (CDP can't run in the test env).

Covers:
  - find_pair criteria gains species_whitelist (only pick parents whose
    config_id is in the set) and entry_whitelist (parents must contain ALL
    listed entry ids; AND semantics).
  - new /api/fly_pet_catalog endpoint enumerates configFly.datas (species)
    and configFly_entry.datas (entries, deduped by id).
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
        sess["dash_user"] = "boss"
        sess["dash_admin"] = True


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
# find_pair: species whitelist
# --------------------------------------------------------------------------
def test_find_pair_species_whitelist_filters_by_config_id(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post(
        "/api/fly_pet_find_pair/dev1",
        json={
            "criteria": {"mode": "quality_count", "species_whitelist": [101, 102]},
            "exclude_ids": [],
        },
    )
    assert resp.status_code == 200
    js = captured["expression"]
    # whitelist ids are injected as a JS array literal
    assert "[101, 102]" in js
    # built into a Set and used to skip non-matching species
    assert "speciesWhitelist" in js
    assert "config_id" in js
    # a guard that continues (skips) when species not in whitelist
    assert "speciesWhitelist.size" in js


def test_find_pair_empty_species_whitelist_does_not_filter(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post(
        "/api/fly_pet_find_pair/dev1",
        json={"criteria": {"mode": "quality_count"}, "exclude_ids": []},
    )
    assert resp.status_code == 200
    js = captured["expression"]
    # still emits the guard, but the injected list is empty
    assert "speciesWhitelist" in js
    assert "[]" in js


# --------------------------------------------------------------------------
# find_pair: entry whitelist (AND)
# --------------------------------------------------------------------------
def test_find_pair_entry_whitelist_requires_all_entries(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.post(
        "/api/fly_pet_find_pair/dev1",
        json={
            "criteria": {"mode": "quality_count", "entry_whitelist": [5, 6]},
            "exclude_ids": [],
        },
    )
    assert resp.status_code == 200
    js = captured["expression"]
    assert "[5, 6]" in js
    assert "entryWhitelist" in js
    # AND semantics -> .every over the whitelist
    assert ".every(" in js


# --------------------------------------------------------------------------
# catalog endpoint
# --------------------------------------------------------------------------
def test_catalog_endpoint_reads_config_tables(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_catalog/dev9")
    assert resp.status_code == 200
    assert captured["ip"] == "dev9"
    js = captured["expression"]
    # enumerates full catalog tables, not getDataByKey
    assert "configFly.datas" in js
    assert "configFly_entry.datas" in js
    # returns species + entries
    assert "species" in js
    assert "entries" in js


def test_catalog_endpoint_dedupes_entries_by_id(monkeypatch):
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    _login_fly_pet(client)
    captured = _capture_json_response(cpa, monkeypatch)

    resp = client.get("/api/fly_pet_catalog/dev9")
    assert resp.status_code == 200
    js = captured["expression"]
    # some dedup bookkeeping keyed by entry id
    assert "seen" in js


def test_catalog_endpoint_requires_auth():
    cpa = _import_control_panel_app()
    client = cpa.app.test_client()
    # no _login_fly_pet -> should be rejected by @_fly_pet_auth
    resp = client.get("/api/fly_pet_catalog/dev9")
    assert resp.status_code in (401, 403)

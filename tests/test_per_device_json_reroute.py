"""Tests for the per-device {ip}.json reroute through JsonDataManager.

new_main_v2.temporary_reset_cycles() and Mission.load_data/record now delegate
file IO to json_manager.JsonDataManager while preserving the flat on-disk
schema. new_main_v2 / Mission import real device / cv2 stacks and cannot be
imported in the test env, so we verify the JsonDataManager contract those call
sites depend on, plus a faithful replication of the reset logic.
"""
import json

import pytest

from json_manager import JsonDataManager


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_flat_schema_roundtrip_preserves_keys(tmp_path, monkeypatch):
    """Mission relies on load_data(default=...) keeping flat mission_* keys."""
    monkeypatch.chdir(tmp_path)
    mgr = JsonDataManager("emulator-5554")

    # Fresh load creates file with the flat default (no nesting).
    data = mgr.load_data(default_data={"mission_timestamp": 0, "mission_num": 0})
    assert data == {"mission_timestamp": 0, "mission_num": 0}

    # Update + save keeps keys flat on disk.
    data["mission_timestamp"] = 1700000000.0
    data["mission_num"] = 3
    assert mgr.save_data(data) is True

    on_disk = _read(tmp_path / "emulator-5554.json")
    assert on_disk == {"mission_timestamp": 1700000000.0, "mission_num": 3}
    assert "mission_timestamp" in on_disk  # not nested under a record dict


def test_save_data_is_atomic_no_temp_left(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = JsonDataManager("emulator-5556")
    mgr.save_data({"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_filename_matches_legacy_ip_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = JsonDataManager("127.0.0.1:5555")
    # JsonDataManager keeps the raw device_id (no colon sanitisation) so the
    # on-disk filename stays byte-identical to the legacy f"{ip}.json".
    assert mgr.get_filename() == "127.0.0.1:5555.json"


def test_reset_cycle_logic_drops_sprint_key(tmp_path, monkeypatch):
    """Replicate temporary_reset_cycles() body: load → del key → save."""
    monkeypatch.chdir(tmp_path)
    ip = "emulator-5554"
    seed = {"衝刺-發條": {"timestamp": 123}, "mission_timestamp": 5.0, "mission_num": 2}
    (tmp_path / f"{ip}.json").write_text(
        json.dumps(seed, indent=4, ensure_ascii=False), encoding="utf-8"
    )

    mgr = JsonDataManager(ip)
    data = mgr.load_data()
    for key in ["衝刺-發條"]:
        if key in data:
            del data[key]
    assert mgr.save_data(data) is True

    on_disk = _read(tmp_path / f"{ip}.json")
    assert "衝刺-發條" not in on_disk
    # Other flat keys untouched.
    assert on_disk == {"mission_timestamp": 5.0, "mission_num": 2}


def test_reset_preserves_non_ascii_keys_unescaped(tmp_path, monkeypatch):
    """save_data uses ensure_ascii=False so CJK keys stay readable on disk."""
    monkeypatch.chdir(tmp_path)
    ip = "emulator-5560"
    mgr = JsonDataManager(ip)
    mgr.save_data({"任務": 1, "mission_num": 0})
    raw = (tmp_path / f"{ip}.json").read_text(encoding="utf-8")
    assert "任務" in raw  # not 任務

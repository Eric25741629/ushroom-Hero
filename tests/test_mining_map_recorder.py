"""Tests for utils.mining_map_recorder (session JSONL + cumulative global map)."""
from __future__ import annotations

import json

import pytest

from utils.log_paths import LogPaths
from utils.mining_map_recorder import (
    CHAR_TO_LABEL,
    LABEL_TO_CHAR,
    MAP_SESSION_RETENTION_DAYS,
    MiningMapRecorder,
    compress_row,
    decompress_row,
)


DEVICE = "emulator-5554"


def _recorder(tmp_path, backend="adb", enabled=True):
    return MiningMapRecorder(
        DEVICE, backend, enabled=enabled, log_paths=LogPaths.with_root(tmp_path)
    )


def _read_events(tmp_path):
    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    files = sorted(map_dir.glob("session_*.jsonl"))
    assert files, "expected a session file"
    with open(files[-1], "r", encoding="utf-8-sig") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# -- compression round-trip -------------------------------------------------

def test_compress_row_maps_labels_to_chars():
    row = ["empty", "dirt", "rock", "reachable_pit", "unreachable_rock", "dug_pit"]
    assert compress_row(row) == ".drPRx"


def test_unknown_label_becomes_question_mark():
    assert compress_row(["totally_new_label"]) == "?"


def test_char_table_roundtrip_is_stable():
    # compress -> decompress -> compress must be identity at char level
    row = ["void", "unreachable_void", "dirt", "unreachable_dirt",
           "rock", "unreachable_rock", "reachable_pit", "unreachable_pit", "dug_pit"]
    chars = compress_row(row)
    canonical = decompress_row(chars)
    assert compress_row(canonical) == chars


def test_every_char_has_a_reverse_mapping():
    for ch in set(LABEL_TO_CHAR.values()):
        assert ch in CHAR_TO_LABEL


# -- session events ---------------------------------------------------------

def test_start_round_end_written(tmp_path):
    rec = _recorder(tmp_path)
    rec.start(planner="v1", depth_base=0, inv={"pickaxe": 50})
    rec.round(
        depth=0, uncertain=False,
        board=[["empty", "dirt", "rock", "reachable_pit", "dirt", "empty"]],
        steps=[{"type": "dig", "pos": [0, 1]}],
        exec={"ok": True, "shovels": 1, "bombs": 0, "drills": 0},
        inv={"pickaxe": 49},
    )
    rec.end()

    events = _read_events(tmp_path)
    assert [e["ev"] for e in events] == ["start", "round", "end"]
    assert events[0]["backend"] == "adb"
    assert events[0]["planner"] == "v1"
    assert events[1]["board"] == [".drPd."]
    assert events[1]["exec"]["shovels"] == 1
    assert events[2]["totals"]["rounds"] == 1
    assert events[2]["totals"]["shovels"] == 1


def test_below_rows_are_recorded(tmp_path):
    rec = _recorder(tmp_path, backend="ws")
    rec.start(planner="final_v1")
    rec.round(
        depth=100, uncertain=False,
        board=[["empty"] * 6],
        below=[["unreachable_dirt"] * 6, ["unreachable_rock"] * 6],
    )
    rec.end()
    events = _read_events(tmp_path)
    assert events[1]["below"] == ["DDDDDD", "RRRRRR"]


# -- global map -------------------------------------------------------------

def test_global_map_depth_alignment_and_overwrite(tmp_path):
    rec = _recorder(tmp_path)
    rec.start()
    rec.round(depth=0, uncertain=False, board=[["dirt"] * 6, ["rock"] * 6])
    # second round scrolled one deeper; row at depth=1 overwritten by newer view
    rec.round(depth=1, uncertain=False, board=[["empty"] * 6, ["reachable_pit"] * 6])
    rec.end()

    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    with open(map_dir / "global_map.json", "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    assert data["rows"]["0"] == "dddddd"
    assert data["rows"]["1"] == "......"  # overwritten by the deeper round's row0
    assert data["rows"]["2"] == "PPPPPP"
    assert data["max_depth"] == 2


def test_below_rows_extend_global_map(tmp_path):
    rec = _recorder(tmp_path)
    rec.start()
    rec.round(
        depth=10, uncertain=False,
        board=[["dirt"] * 6],
        below=[["unreachable_rock"] * 6],
    )
    rec.end()
    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    with open(map_dir / "global_map.json", "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    assert data["rows"]["10"] == "dddddd"
    assert data["rows"]["11"] == "RRRRRR"


def test_uncertain_round_not_written_to_global(tmp_path):
    rec = _recorder(tmp_path)
    rec.start()
    rec.round(depth=5, uncertain=True, board=[["dirt"] * 6])
    rec.end()
    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    # session still recorded
    events = _read_events(tmp_path)
    assert any(e["ev"] == "round" for e in events)
    # but global map has no such row
    global_path = map_dir / "global_map.json"
    if global_path.exists():
        with open(global_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        assert "5" not in data.get("rows", {})


def test_global_map_written_utf8_no_bom(tmp_path):
    rec = _recorder(tmp_path)
    rec.start()
    rec.round(depth=0, uncertain=False, board=[["dirt"] * 6])
    rec.end()
    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    raw = (map_dir / "global_map.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


# -- disabled + fail-safe ---------------------------------------------------

def test_disabled_recorder_writes_nothing(tmp_path):
    rec = _recorder(tmp_path, enabled=False)
    rec.start()
    rec.round(depth=0, uncertain=False, board=[["dirt"] * 6])
    rec.end()
    map_dir = LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)
    assert not map_dir.exists()


def test_exceptions_never_propagate(tmp_path, monkeypatch):
    rec = _recorder(tmp_path)
    rec.start()

    # Force an internal failure; the public method must swallow it.
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rec, "_write_event", boom)
    # Should not raise
    rec.round(depth=0, uncertain=False, board=[["dirt"] * 6])
    rec.end()
    assert rec._broken is True


def test_retention_constant_is_90_days():
    assert MAP_SESSION_RETENTION_DAYS == 90


# -- config normalization + dashboard toggle --------------------------------

def test_mining_map_record_defaults_true():
    import config_manager

    assert config_manager.DEFAULT_DEVICE_CONFIG["mining_map_record"] is True
    # dataclass default applies when the key is absent from stored config
    cfg = config_manager.DeviceConfig.from_dict({})
    assert cfg.get("mining_map_record") is True


def test_config_normalize_coerces_mining_map_record(monkeypatch):
    import config_manager

    saved: dict = {}
    monkeypatch.setattr(config_manager, "load_config", lambda: {"devices": {}})
    monkeypatch.setattr(
        config_manager, "save_config", lambda cfg, **k: saved.update(cfg)
    )
    config_manager.update_device_config(
        "emulator-maptest", {"mining_map_record": "off"}
    )
    assert saved["devices"]["emulator-maptest"]["mining_map_record"] is False


def test_dashboard_has_mining_map_toggle():
    from pathlib import Path

    html = Path("templates/dashboard.html").read_text(encoding="utf-8-sig")
    assert "chkMiningMapRecord" in html
    assert "mining_map_record" in html

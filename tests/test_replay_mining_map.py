"""Tests for tools.replay_mining_map (session listing / frame replay / map dump)."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from utils.log_paths import LogPaths
from utils.mining_map_recorder import MiningMapRecorder, decompress_row


def _load_rmap():
    # tools/ is shadowed by a root-level tools.py, so load the CLI by file path.
    path = Path(__file__).resolve().parents[1] / "tools" / "replay_mining_map.py"
    spec = importlib.util.spec_from_file_location("replay_mining_map", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rmap = _load_rmap()


DEVICE = "emulator-5554"


def _record_two_round_session(tmp_path):
    rec = MiningMapRecorder(
        DEVICE, "adb", enabled=True, log_paths=LogPaths.with_root(tmp_path)
    )
    rec.start(planner="v1", inv={"pickaxe": 10})
    rec.round(
        depth=0, uncertain=False,
        board=[["dirt", "rock", "reachable_pit", "empty", "dirt", "rock"]],
        steps=[{"type": "dig", "pos": [0, 2]}],
        exec={"ok": True, "shovels": 1, "bombs": 0, "drills": 0},
        inv={"pickaxe": 9},
    )
    rec.round(
        depth=1, uncertain=False,
        board=[["empty", "empty", "dirt", "dirt", "rock", "empty"]],
        exec={"ok": True, "shovels": 1, "bombs": 0, "drills": 0},
        inv={"pickaxe": 8},
    )
    rec.end()
    return LogPaths.with_root(tmp_path).mining_map_dir(DEVICE)


def test_list_sessions_returns_recorded_file(tmp_path):
    map_dir = _record_two_round_session(tmp_path)
    sessions = rmap.list_sessions(map_dir)
    assert len(sessions) == 1
    assert sessions[0].name.startswith("session_")


def test_replay_reconstructs_board(tmp_path):
    map_dir = _record_two_round_session(tmp_path)
    session = rmap.list_sessions(map_dir)[0]
    events = rmap.load_session(session)
    rounds = [e for e in events if e["ev"] == "round"]
    # compressed row -> canonical labels must round-trip the original char string
    assert rounds[0]["board"] == ["drP.dr"]
    assert decompress_row(rounds[0]["board"][0]) == [
        "dirt", "rock", "reachable_pit", "empty", "dirt", "rock"
    ]


def test_replay_session_dump_contains_frames(tmp_path):
    map_dir = _record_two_round_session(tmp_path)
    session = rmap.list_sessions(map_dir)[0]
    buf = io.StringIO()
    rmap.replay_session(session, animate=False, out=buf)
    text = buf.getvalue()
    assert "round #1" in text
    assert "round #2" in text
    assert "drP.dr" in text
    assert "totals" in text


def test_render_global_map_top_to_bottom(tmp_path):
    map_dir = _record_two_round_session(tmp_path)
    text = rmap.render_global_map(map_dir)
    # depth 0 then depth 1 (deeper), in ascending order
    lines = [ln for ln in text.splitlines() if ln.strip().split()[0].lstrip("-").isdigit()]
    depths = [int(ln.strip().split()[0]) for ln in lines]
    assert depths == sorted(depths)
    assert "drP.dr" in text


def test_main_map_mode(tmp_path, capsys):
    _record_two_round_session(tmp_path)
    rc = rmap.main(["--device", DEVICE, "--map", "--logs-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "drP.dr" in out


def test_main_list_mode(tmp_path, capsys):
    _record_two_round_session(tmp_path)
    rc = rmap.main(["--device", DEVICE, "--logs-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "session_" in out

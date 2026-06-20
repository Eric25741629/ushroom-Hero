"""Tests for ws_token.state — tiny per-device JSON cadence store."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import state  # noqa: E402


def test_load_missing_returns_empty(tmp_path):
    assert state.load_state("devA", state_dir=tmp_path) == {}


def test_save_then_load_roundtrip(tmp_path):
    state.save_state("devA", {"workshop": {"last_rotate_ts": 123, "parity": 1}},
                     state_dir=tmp_path)
    assert state.load_state("devA", state_dir=tmp_path) == {
        "workshop": {"last_rotate_ts": 123, "parity": 1}}


def test_load_corrupt_file_returns_empty(tmp_path):
    (tmp_path / "devA.json").write_text("{not json", encoding="utf-8")
    assert state.load_state("devA", state_dir=tmp_path) == {}


def test_save_creates_dir(tmp_path):
    state.save_state("devA", {"x": 1}, state_dir=tmp_path / "sub")
    assert state.load_state("devA", state_dir=tmp_path / "sub") == {"x": 1}


def test_load_tolerates_utf8_bom(tmp_path):
    (tmp_path / "devA.json").write_text('{"x": 1}', encoding="utf-8-sig")
    assert state.load_state("devA", state_dir=tmp_path) == {"x": 1}


def test_save_uses_atomic_replace(tmp_path, monkeypatch):
    """save_state must go through os.replace so a torn write can't reset gates."""
    calls = []
    real_replace = state.os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(state.os, "replace", spy)
    state.save_state("devA", {"x": 1}, state_dir=tmp_path)

    assert calls, "save_state must use os.replace for atomicity"
    assert calls[0][1].endswith("devA.json")
    assert state.load_state("devA", state_dir=tmp_path) == {"x": 1}
    # no temp artifact left behind after a successful save
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_overwrite_keeps_old_file_on_serialize_failure(tmp_path, monkeypatch):
    """If serialization/write fails, the previous good file must survive intact."""
    state.save_state("devA", {"good": 1}, state_dir=tmp_path)

    boom = {"bad": object()}  # not JSON-serializable -> json.dumps raises
    try:
        state.save_state("devA", boom, state_dir=tmp_path)
    except TypeError:
        pass
    # original content untouched (atomic: failed write never replaced it)
    assert state.load_state("devA", state_dir=tmp_path) == {"good": 1}

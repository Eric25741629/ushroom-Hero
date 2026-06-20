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


def test_save_torn_write_does_not_destroy_existing_file(tmp_path, monkeypatch):
    """A crash/SMB hiccup mid-write must not truncate the previous good file.

    This is the real bug the atomic write fixes: the OLD code wrote straight to
    the target, so a partial write corrupted it and load_state -> {} silently
    reset every daily/weekly gate. The atomic write hits a sibling .tmp first,
    so a torn write leaves the original intact.
    """
    state.save_state("devA", {"good": 1}, state_dir=tmp_path)

    real_write_text = Path.write_text

    def partial_then_crash(self, data, *a, **k):
        real_write_text(self, data[: len(data) // 2], *a, **k)  # write half...
        raise OSError("simulated mid-write crash")               # ...then die

    monkeypatch.setattr(Path, "write_text", partial_then_crash)
    try:
        state.save_state("devA", {"good": 1, "more": 2}, state_dir=tmp_path)
    except OSError:
        pass
    # Atomic: the torn bytes went to devA.json.tmp; devA.json is still the good file.
    assert state.load_state("devA", state_dir=tmp_path) == {"good": 1}

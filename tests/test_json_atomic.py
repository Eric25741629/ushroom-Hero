import json
import os
import pytest


def test_atomic_write_creates_file(tmp_path):
    from json_manager import _atomic_write_json
    target = tmp_path / "out.json"
    data = {"key": "value", "num": 42}
    _atomic_write_json(str(target), data, indent=4, ensure_ascii=False)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data


def test_atomic_write_preserves_original_on_error(tmp_path):
    from json_manager import _atomic_write_json
    target = tmp_path / "out.json"
    original = {"original": True}
    target.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(Exception):
        _atomic_write_json(str(target), {"bad": object()})

    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == original


def test_atomic_write_no_temp_files_left(tmp_path):
    from json_manager import _atomic_write_json
    target = tmp_path / "out.json"
    _atomic_write_json(str(target), {"ok": 1})
    files = list(tmp_path.iterdir())
    assert len(files) == 1, f"Temp files left behind: {files}"

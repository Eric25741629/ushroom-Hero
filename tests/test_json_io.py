"""Tests for utils.json_io.read_json_bom_safe.

The bot persists no-BOM JSON, but files synced from Windows tooling can
arrive carrying a UTF-8 BOM. ``read_json_bom_safe`` must decode both.
"""
import json

from utils.json_io import read_json_bom_safe


def test_reads_file_without_bom(tmp_path):
    # Arrange
    p = tmp_path / "plain.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")

    # Act
    data = read_json_bom_safe(p)

    # Assert
    assert data == {"a": 1}


def test_reads_file_with_bom(tmp_path):
    # Arrange
    p = tmp_path / "bom.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8-sig")

    # Act
    data = read_json_bom_safe(p)

    # Assert: no stray ﻿ leaks into the first key
    assert data == {"a": 1}
    assert all("﻿" not in k for k in data)


def test_reads_nested_unicode_content(tmp_path):
    # Arrange
    payload = {"裝備": {"名稱": "神燈", "items": ["劍", "盾"]}}
    p = tmp_path / "unicode.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    # Act
    data = read_json_bom_safe(p)

    # Assert
    assert data == payload


def test_accepts_str_path(tmp_path):
    # Arrange
    p = tmp_path / "strpath.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8-sig")

    # Act
    data = read_json_bom_safe(str(p))

    # Assert
    assert data == [1, 2, 3]

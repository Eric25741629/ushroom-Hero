"""utils/config_names.py — 中文名查表 + 缺檔/缺鍵 fallback 測試。"""
import json

from utils import config_names


def _write(tmp_path, data):
    p = tmp_path / "config_names.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_lookup_and_fallback(tmp_path, monkeypatch):
    path = _write(tmp_path, {
        "spirit": {"101": "格鬥犬"},
        "attr": {"1001": "攻擊"},
        "suit": {"104": "烈焰咆哮"},
        "quality": {"7": "傳奇"},
    })
    monkeypatch.setattr(config_names, "_NAMES_PATH", path)
    config_names.reload()

    assert config_names.spirit_name(101) == "格鬥犬"
    assert config_names.attr_name("1001") == "攻擊"
    assert config_names.suit_name(104) == "烈焰咆哮"
    assert config_names.quality_name(7) == "傳奇"
    # 缺鍵 -> 回編號字串，不拋例外
    assert config_names.spirit_name(999) == "999"
    assert config_names.attr_name(42) == "42"


def test_missing_file_falls_back_to_number(tmp_path, monkeypatch):
    monkeypatch.setattr(config_names, "_NAMES_PATH", tmp_path / "nope.json")
    config_names.reload()
    assert config_names.suit_name(104) == "104"
    assert config_names.quality_name(3) == "3"

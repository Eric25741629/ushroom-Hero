"""封包偵測「要的品質」掉落 (utils.lamp_drop_watch) — 封包為主、OCR 為輔。

遊戲自動開+自動賣只留 11 永恆；開到永恆時會停住留 1 顆。舊版只靠「神燈數量
停滯」察覺，本模組改用 0x0504 封包精確偵測（封包也帶 rarity+詞條，判斷免 OCR）。
純邏輯測試，不碰 device / page / cv2。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from utils.lamp_drop_watch import (
    WANTED_RARITY_DEFAULT,
    filter_high_rarity,
    find_high_rarity_drops,
    find_high_rarity_drops_in_frames,
    format_drop,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lamp_drops_0504_20lamps.bin"


def _entry(rarity, *, rarity_name="x", slot=1, slot_name="武器", uid=1, affixes=None):
    """Synthetic equipment-drop entry shaped like parse_equipment_lamp_drops output."""
    return {
        "uid": uid,
        "template_id": rarity * 10_000_000 + slot * 100_000 + 1,
        "rarity": rarity,
        "rarity_name": rarity_name,
        "slot": slot,
        "slot_name": slot_name,
        "affixes": affixes or {},
    }


def test_default_threshold_is_eternal_rarity_11():
    assert WANTED_RARITY_DEFAULT == 11


def test_filter_keeps_only_at_or_above_threshold():
    entries = [_entry(4, uid=1), _entry(11, rarity_name="永恆", uid=2), _entry(7, uid=3)]
    out = filter_high_rarity(entries, min_rarity=11)
    assert [e["uid"] for e in out] == [2]


def test_filter_lower_threshold_includes_more():
    entries = [_entry(4, uid=1), _entry(11, uid=2)]
    assert len(filter_high_rarity(entries, min_rarity=4)) == 2


def test_filter_empty_when_nothing_reaches_threshold():
    entries = [_entry(4, uid=1), _entry(7, uid=2)]
    assert filter_high_rarity(entries, min_rarity=11) == []


def test_format_drop_renders_rarity_slot_affix_and_uid():
    e = _entry(11, rarity_name="永恆", slot=10, slot_name="鞋", uid=999, affixes={1017: 132})
    s = format_drop(e)
    assert "永恆" in s
    assert "鞋" in s
    assert "999" in s
    assert "%" in s  # affix value rendered as percentage


def test_find_in_frames_ignores_non_0x0504_frames():
    frames = [{"cmd": 0x0302, "dir": "rx", "body": b"\x01\x02\x03"}]
    assert find_high_rarity_drops_in_frames(frames, min_rarity=1) == []


def test_find_in_frames_tolerates_missing_body():
    frames = [{"cmd": 0x0504, "dir": "rx"}]  # no 'body' key
    assert find_high_rarity_drops_in_frames(frames) == []


@pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="fixture lamp_drops_0504_20lamps.bin not present",
)
def test_real_fixture_threshold_behaviour():
    """The 20-lamp sample contains 稀有(4)-level drops but no 永恆(11)."""
    body = FIXTURE.read_bytes()
    assert len(find_high_rarity_drops(body, min_rarity=4)) >= 1
    assert find_high_rarity_drops(body, min_rarity=11) == []

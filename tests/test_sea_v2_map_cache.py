"""Shared season-map cache (sea_v2.map_cache).

H5 accounts each read their own scene, but the parsed *server-global* target tiles and
each account's own base get persisted to a shared JSON so the lone ADB account (no cocos
JS access) can navigate too.
"""
import json

import pytest

from sea_v2 import map_cache as mc
from sea_v2.tiles import Tile


def test_load_missing_file_returns_empty_cache(tmp_path):
    cache = mc.load(tmp_path / "nope.json")
    assert cache["season"] is None
    assert cache["targets"] == []
    assert cache["account_base"] == {}


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "sub" / "sea_map.json"  # parent dir does not exist yet
    cache = mc.empty_cache("s4")
    mc.record_account_base(cache, "emulator-5554", (-31910, -1867))
    mc.save(p, cache)
    again = mc.load(p)
    assert again["season"] == "s4"
    assert mc.get_account_base(again, "emulator-5554") == (-31910, -1867)


def test_record_targets_dedupes_by_identity(tmp_path):
    cache = mc.empty_cache("s4")
    tiles = [Tile("resource_1", -31364, -1709), Tile("remain", -29999, -1709)]
    mc.record_targets(cache, tiles)
    mc.record_targets(cache, tiles)  # second pass must not duplicate
    assert len(cache["targets"]) == 2


def test_get_targets_filters_by_type(tmp_path):
    cache = mc.empty_cache("s4")
    mc.record_targets(cache, [
        Tile("resource_1", -31364, -1709),
        Tile("resource_1", -31773, -1314),
        Tile("remain", -29999, -1709),
    ])
    assert set(mc.get_targets(cache, "resource_1")) == {(-31364, -1709), (-31773, -1314)}
    assert mc.get_targets(cache, "remain") == [(-29999, -1709)]


def test_load_corrupt_file_returns_empty_cache(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    cache = mc.load(p)
    assert cache["targets"] == []


def test_get_account_base_missing_returns_none(tmp_path):
    cache = mc.empty_cache("s4")
    assert mc.get_account_base(cache, "ghost") is None

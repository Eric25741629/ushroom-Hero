"""Tile parsing + target selection for sea_v2.tiles.

Fixtures use coordinates measured live on 5554 (2026-05-25):
home base (-31910,-1867); a neighbour base (-28088,-1867); resources scattered to the
upper-right of home; the nearest relic (`remain`) at (-29999,-1709).
"""
import pytest

from sea_v2 import tiles as T


def _raw(name, x, y):
    return {"name": name, "wp": {"x": x, "y": y}}


HOME = (-31910, -1867)
NEIGHBOUR = (-28088, -1867)
RAW_OBJS = [
    _raw("resource_2", -31500, -1156),
    _raw("s4_totem", -31091, -1235),
    _raw("resource_1", -30954, -1630),
    _raw("resource_1", -31364, -1709),
    _raw("resource_1", -31773, -1314),
    _raw("resource_1", -31773, -840),
    _raw("base", *HOME),
    _raw("base", *NEIGHBOUR),
    _raw("remain", -29999, -1709),
]


def test_parse_tiles_reads_name_and_world_coords():
    tiles = T.parse_tiles([_raw("resource_1", -30954, -1630)])
    assert len(tiles) == 1
    assert tiles[0].name == "resource_1"
    assert (tiles[0].wx, tiles[0].wy) == (-30954, -1630)


def test_parse_tiles_skips_entries_missing_world_position():
    tiles = T.parse_tiles([{"name": "base"}, _raw("remain", 1, 2)])
    assert [t.name for t in tiles] == ["remain"]


def test_home_base_is_the_base_nearest_the_camera():
    tiles = T.parse_tiles(RAW_OBJS)
    # camera centred near home on entry / after 定位
    home = T.home_base(tiles, cam_wp=(-31733, -1440))
    assert (home.wx, home.wy) == HOME


def test_home_base_returns_none_when_no_base_present():
    tiles = T.parse_tiles([_raw("remain", 1, 2)])
    assert T.home_base(tiles, cam_wp=(0, 0)) is None


def test_nearest_two_resource_lv1_to_home():
    tiles = T.parse_tiles(RAW_OBJS)
    home = T.home_base(tiles, cam_wp=(-31733, -1440))
    picks = T.nearest(tiles, "resource_1", origin=(home.wx, home.wy), n=2)
    coords = {(t.wx, t.wy) for t in picks}
    assert coords == {(-31364, -1709), (-31773, -1314)}


def test_nearest_relic_to_home():
    tiles = T.parse_tiles(RAW_OBJS)
    home = T.home_base(tiles, cam_wp=(-31733, -1440))
    relic = T.nearest(tiles, "remain", origin=(home.wx, home.wy), n=1)
    assert (relic[0].wx, relic[0].wy) == (-29999, -1709)


def test_nearest_returns_empty_when_type_absent():
    tiles = T.parse_tiles(RAW_OBJS)
    assert T.nearest(tiles, "no_such_type", origin=HOME, n=3) == []


def test_pick_daily_targets_gives_two_resources_and_one_relic():
    tiles = T.parse_tiles(RAW_OBJS)
    home = T.home_base(tiles, cam_wp=(-31733, -1440))
    plan = T.pick_daily_targets(tiles, home)
    assert len(plan.resources) == 2
    assert all(t.name == "resource_1" for t in plan.resources)
    assert plan.relic is not None and plan.relic.name == "remain"


def test_pick_daily_targets_relic_none_when_no_relic_loaded():
    raw = [r for r in RAW_OBJS if r["name"] != "remain"]
    tiles = T.parse_tiles(raw)
    home = T.home_base(tiles, cam_wp=(-31733, -1440))
    plan = T.pick_daily_targets(tiles, home)
    assert plan.relic is None
    assert len(plan.resources) == 2

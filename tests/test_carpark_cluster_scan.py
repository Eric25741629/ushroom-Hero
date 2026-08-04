"""Tests for cluster scan (抱團掃描) — carpark_plan.parse_cluster_scan +
carpark.scan_lots_same_server."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from ws_token.carpark_plan import ClusterScanConfig, parse_cluster_scan
from ws_token.carpark import (
    NullSpace, CarParkLot, Space, parse_null_spaces, scan_lots_same_server,
    silver_level_to_ceng, silver_ceng_to_level, CROSS_TYPE,
)
from ws_token import codec
import ws_token.carpark as carpark_mod


def test_enabled_device_plans_use_strict_five_and_exclude_123():
    assert carpark_mod.DEFAULT_CLUSTER_MIN == 5
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "bot_config.json")
        .read_text(encoding="utf-8-sig"))
    enabled = []
    for device, raw in config["devices"].items():
        plan = (raw.get("ws_token") or {}).get("carpark_plan") or {}
        if not plan.get("enabled"):
            continue
        enabled.append(device)
        scan = parse_cluster_scan(plan)
        assert plan["cluster_min"] == 5
        assert plan["allow_low_noncluster"] is False
        assert scan.enabled
        assert scan.min_allies == 5
        assert {1, 2, 3}.isdisjoint(scan.levels)
        assert {1, 2, 3}.issubset(scan.excluded_levels)

    assert len(enabled) == 5


# --- parse_cluster_scan -----------------------------------------------------

def test_parse_disabled_when_missing():
    assert parse_cluster_scan(None) == ClusterScanConfig()
    assert parse_cluster_scan({}) == ClusterScanConfig()
    assert not parse_cluster_scan({}).enabled


def test_parse_disabled_when_flag_false():
    cfg = {"cluster_scan": {"enabled": False, "levels": [1, 2]}}
    assert not parse_cluster_scan(cfg).enabled


def test_parse_enabled_defaults():
    cfg = {"cluster_scan": {"enabled": True}}
    cs = parse_cluster_scan(cfg)
    assert cs.enabled
    assert cs.levels == tuple(range(4, 11))
    assert cs.excluded_levels == (1, 2, 3)
    assert cs.duration == 300
    assert cs.interval == 1
    assert cs.min_allies == 5
    assert cs.fallback_level == 9


def test_parse_custom_values():
    cfg = {"cluster_scan": {
        "enabled": True, "levels": [4, 7, 9],
        "duration": 120, "interval": 10,
        "min_allies": 2, "fallback_level": 7,
    }}
    cs = parse_cluster_scan(cfg)
    assert cs.levels == (4, 7, 9)
    assert cs.duration == 120
    assert cs.interval == 10
    assert cs.min_allies == 5
    assert cs.fallback_level == 7


def test_parse_priority_levels():
    cfg = {"cluster_scan": {
        "enabled": True, "levels": list(range(1, 31)),
        "priority_levels": list(range(1, 16)), "min_allies": 2,
    }}
    cs = parse_cluster_scan(cfg)
    assert cs.levels == tuple(range(4, 31))
    assert cs.priority_levels == tuple(range(4, 16))
    assert cs.min_allies == 5
    assert cs.excluded_levels == (1, 2, 3)


def test_parse_priority_levels_defaults_empty():
    cs = parse_cluster_scan({"cluster_scan": {"enabled": True}})
    assert cs.priority_levels == ()


def test_parse_excluded_levels_always_keeps_required_1_2_3():
    cs = parse_cluster_scan({"cluster_scan": {
        "enabled": True,
        "levels": [1, 2, 3, 4, 5],
        "priority_levels": [1, 4, 5],
        "excluded_levels": [2, 9, "bad"],
    }})
    assert cs.excluded_levels == (1, 2, 3, 9)
    assert cs.levels == (4, 5)
    assert cs.priority_levels == (4, 5)


# --- scan_lots_same_server ---------------------------------------------------

def _lot(level: int, null_num: int = 5,
         same_server_count: int | None = None) -> NullSpace:
    return NullSpace(park_type=CROSS_TYPE,
                     master_id=1001001000 + silver_level_to_ceng(level),
                     null_num=null_num,
                     ceng=silver_level_to_ceng(level),
                     same_server_count=same_server_count)


def _make_detail(level: int, server_id: int, same_count: int) -> CarParkLot:
    """Build a CarParkLot with ``same_count`` occupied slots carrying server_id."""
    spaces = []
    for i in range(same_count):
        spaces.append(Space(pos=i + 1, role_id=100 + i, occupied=True,
                            attrs={1: server_id}))
    # fill remaining with non-matching
    for i in range(same_count, 8):
        spaces.append(Space(pos=same_count + i + 1, role_id=200 + i,
                            occupied=True, attrs={1: 9999}))
    return CarParkLot(type=CROSS_TYPE,
                      master_id=1001001000 + silver_level_to_ceng(level),
                      ceng=silver_level_to_ceng(level),
                      spaces=tuple(spaces))


class FakeClient:
    def __init__(self, details: dict[int, CarParkLot]):
        self._details = details  # master_id -> CarParkLot

    def call(self, cmd, body, *, timeout=None):
        # carpark.read_lot calls client.call(CMD_LOT_INFO, ...)
        # Parse master_id from the body (field #2 varint).
        # Simplified: just return the raw bytes of the matching lot.
        # Instead, we monkeypatch read_lot.
        raise NotImplementedError


def test_scan_sorts_by_count_desc_then_ceng_asc(monkeypatch):
    lots = [_lot(4), _lot(7), _lot(9)]
    details = {
        _lot(4).master_id: _make_detail(4, 1467, 2),
        _lot(7).master_id: _make_detail(7, 1467, 5),
        _lot(9).master_id: _make_detail(9, 1467, 5),
    }

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        return details[master_id]

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(None, lots, 1467, (4, 7, 9))
    assert len(ranked) == 3
    # 7 and 9 both have 5 allies; 7 should come first (lower ceng)
    assert ranked[0][1] == 5
    assert ranked[0][0].ceng == silver_level_to_ceng(7)
    assert ranked[1][1] == 5
    assert ranked[1][0].ceng == silver_level_to_ceng(9)
    assert ranked[2][1] == 2


def test_scan_priority_levels_rank_first(monkeypatch):
    # 鉑銀12 (priority) has only 2 allies; 鉑銀20 (non-priority) has 5.
    # priority_levels must win: 鉑銀12 ranks first despite fewer allies.
    lots = [_lot(12), _lot(20)]
    details = {
        _lot(12).master_id: _make_detail(12, 1467, 2),
        _lot(20).master_id: _make_detail(20, 1467, 5),
    }

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        return details[master_id]

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(None, lots, 1467, (12, 20),
                                   priority_levels=(11, 12, 13, 14, 15))
    assert ranked[0][0].ceng == silver_level_to_ceng(12)
    assert ranked[0][1] == 2
    assert ranked[1][0].ceng == silver_level_to_ceng(20)


def test_scan_priority_group_ordered_by_count(monkeypatch):
    # Two priority lots: higher ally count first within the priority group.
    lots = [_lot(3), _lot(6)]
    details = {
        _lot(3).master_id: _make_detail(3, 1467, 2),
        _lot(6).master_id: _make_detail(6, 1467, 4),
    }

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        return details[master_id]

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(None, lots, 1467, (3, 6),
                                   priority_levels=(1, 2, 3, 4, 5, 6))
    assert ranked[0][0].ceng == silver_level_to_ceng(6)  # 4 allies
    assert ranked[1][0].ceng == silver_level_to_ceng(3)  # 2 allies


def test_scan_skips_levels_not_in_filter(monkeypatch):
    lots = [_lot(1), _lot(5), _lot(9)]
    details = {
        _lot(5).master_id: _make_detail(5, 1467, 3),
        _lot(9).master_id: _make_detail(9, 1467, 1),
    }

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        return details[master_id]

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(None, lots, 1467, (5, 9))
    assert len(ranked) == 2
    assert ranked[0][0].ceng == silver_level_to_ceng(5)


def test_scan_skips_lots_that_cannot_reach_ally_threshold(monkeypatch):
    lots = [_lot(4, null_num=6), _lot(5, null_num=5)]
    read_ids = []

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        read_ids.append(master_id)
        return _make_detail(silver_ceng_to_level(ceng), 1467, 5)

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(
        None, lots, 1467, (4, 5), min_allies=5,
    )

    assert read_ids == [_lot(5).master_id]
    assert [silver_ceng_to_level(lot.ceng) for lot, _ in ranked] == [5]


def test_scan_does_not_probe_nonpriority_after_priority_qualifies(monkeypatch):
    lots = [_lot(4, null_num=5), _lot(20, null_num=5)]
    read_levels = []

    def fake_read_lot(client, *, type, master_id, ceng, timeout=None):
        level = silver_ceng_to_level(ceng)
        read_levels.append(level)
        return _make_detail(level, 1467, 5)

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(cp_mod, "read_lot", fake_read_lot)

    ranked = scan_lots_same_server(
        None, lots, 1467, (4, 20), priority_levels=(4,), min_allies=5,
    )

    assert read_levels == [4]
    assert [silver_ceng_to_level(lot.ceng) for lot, _ in ranked] == [4]


def test_scan_uses_search_group_count_without_lot_detail(monkeypatch):
    lot = _lot(4, null_num=5, same_server_count=5)

    import ws_token.carpark as cp_mod
    monkeypatch.setattr(
        cp_mod, "read_lot",
        lambda *args, **kwargs: pytest.fail("12801 detail should not be needed"),
    )

    ranked = scan_lots_same_server(
        None, [lot], 1467, (4,), priority_levels=(4,), min_allies=5,
    )

    assert ranked == [(lot, 5)]


def test_search_parser_reads_group_count_from_ext_key_two():
    ext_group_count = codec.pb_uint(1, 2) + codec.pb_uint(2, 6)
    entry = (
        codec.pb_uint(1, CROSS_TYPE)
        + codec.pb_uint(2, 1001001008)
        + codec.pb_uint(3, 4)
        + codec.pb_msg(6, ext_group_count)
        + codec.pb_uint(7, silver_level_to_ceng(4))
    )

    lots = parse_null_spaces(codec.pb_msg(1, entry))

    assert len(lots) == 1
    assert lots[0].same_server_count == 6


def test_scan_empty_when_no_lots():
    ranked = scan_lots_same_server(None, [], 1467, (1, 2, 3))
    assert ranked == []


def test_prepare_candidates_merges_sources_and_excludes_1_2_3():
    null_lots = [_lot(1), _lot(2), _lot(3), _lot(4), _lot(5)]
    collect_lots = [_lot(4, null_num=2)]
    candidates, audit = carpark_mod.prepare_cluster_scan_candidates(
        null_lots, collect_lots,
        excluded_levels=(1, 2, 3), today_parked=set(),
    )
    assert [silver_ceng_to_level(lot.ceng) for lot in candidates] == [4, 5]
    assert len({lot.master_id for lot in candidates}) == 2
    assert audit["source_null"] == 5
    assert audit["source_collect"] == 1
    assert audit["merged"] == 5
    assert audit["excluded_levels"] == [1, 2, 3]


def test_prepare_candidates_records_full_non_silver_and_today_filters():
    non_silver = NullSpace(park_type=CROSS_TYPE, master_id=77,
                           null_num=1, ceng=999)
    candidates, audit = carpark_mod.prepare_cluster_scan_candidates(
        [_lot(4, null_num=0), _lot(5), non_silver], [],
        excluded_levels=(1, 2, 3), today_parked={_lot(5).master_id},
    )
    assert candidates == []
    assert audit["removed_full"] == 1
    assert audit["removed_non_silver"] == 1
    assert audit["removed_today"] == [{
        "level": 5, "master_id": _lot(5).master_id,
    }]

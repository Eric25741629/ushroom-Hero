"""Tests for cluster scan (抱團掃描) — carpark_plan.parse_cluster_scan +
carpark.scan_lots_same_server."""
from __future__ import annotations

from ws_token.carpark_plan import ClusterScanConfig, parse_cluster_scan
from ws_token.carpark import (
    NullSpace, CarParkLot, Space, scan_lots_same_server,
    silver_level_to_ceng, CROSS_TYPE,
)


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
    assert cs.levels == tuple(range(1, 11))
    assert cs.duration == 300
    assert cs.interval == 5
    assert cs.min_allies == 3
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
    assert cs.min_allies == 2
    assert cs.fallback_level == 7


# --- scan_lots_same_server ---------------------------------------------------

def _lot(level: int, null_num: int = 5) -> NullSpace:
    return NullSpace(park_type=CROSS_TYPE,
                     master_id=1001001000 + silver_level_to_ceng(level),
                     null_num=null_num,
                     ceng=silver_level_to_ceng(level))


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


def test_scan_empty_when_no_lots():
    ranked = scan_lots_same_server(None, [], 1467, (1, 2, 3))
    assert ranked == []

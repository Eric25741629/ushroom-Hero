"""Tests for ws_token.sea_season — pure-WS sea/season tasks."""
from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

import pytest

from ws_token import codec
from ws_token import sea_season as ss


# --- body builder tests ---------------------------------------------------

def test_build_scan():
    body = ss.build_scan(30, 22)
    d = codec.walk_dict(body)
    assert d[2] == 1  # mode field
    grid = codec.walk_dict(bytes(d[1]))
    assert grid[1] == 30
    assert grid[2] == 22


def test_build_dispatch_attack():
    """Attack (進攻) = action 1, field 3=1, field 4=1. Verified live 2026-06-24."""
    body = ss.build_dispatch(ss.ACTION_ATTACK, 16, 25)
    d = codec.walk_dict(body)
    assert d[1] == 1  # ACTION_ATTACK
    assert d[3] == 1
    assert d[4] == 1
    grid = codec.walk_dict(bytes(d[2]))
    assert grid[1] == 16
    assert grid[2] == 25


def test_build_dispatch_garrison():
    """Garrison (駐守) = action 2, field 4=0, no field 3. Verified live 2026-06-24."""
    body = ss.build_dispatch(ss.ACTION_GARRISON, 20, 24)
    d = codec.walk_dict(body)
    assert d[1] == 2  # ACTION_GARRISON
    assert d[4] == 0
    assert 3 not in d
    grid = codec.walk_dict(bytes(d[2]))
    assert grid[1] == 20
    assert grid[2] == 24


def test_build_task_claim():
    body = ss.build_task_claim(109, 41515, 41503)
    d = codec.walk_dict(body)
    assert d[1] == 109
    assert d[2] == 41515
    assert d[3] == 41503


def test_build_repair():
    body = ss.build_repair(94200)
    d = codec.walk_dict(body)
    assert d[1] == 94200


def test_build_tactic():
    body = ss.build_tactic(4)
    d = codec.walk_dict(body)
    assert d[1] == 4


# --- parser tests ---------------------------------------------------------

def _make_scan_cell(cell_id: int, building_type: int) -> bytes:
    occ = codec.pb_uint(1, 99999) + codec.pb_uint(2, building_type) + codec.pb_uint(5, 50)
    return codec.pb_uint(1, cell_id) + codec.pb_uint(3, 0) + codec.pb_msg(4, occ)


def test_parse_scan_cells():
    cell1 = _make_scan_cell(148, ss.BT_REMAIN)
    cell2 = _make_scan_cell(491, ss.BT_RESOURCE)
    body = codec.pb_msg(1, cell1) + codec.pb_msg(1, cell2)
    cells = ss.parse_scan_cells(body)
    assert len(cells) == 2
    assert cells[0].cell_id == 148
    assert cells[0].building_type == ss.BT_REMAIN
    assert cells[1].cell_id == 491
    assert cells[1].building_type == ss.BT_RESOURCE


def test_parse_march():
    from_grid = codec.pb_uint(1, 30) + codec.pb_uint(2, 26)
    to_grid = codec.pb_uint(1, 15) + codec.pb_uint(2, 19)
    body = (codec.pb_uint(1, 12345)
            + codec.pb_uint(3, 4)
            + codec.pb_msg(6, from_grid)
            + codec.pb_msg(7, to_grid))
    m = ss.parse_march(body)
    assert m.from_x == 30
    assert m.from_y == 26
    assert m.to_x == 15
    assert m.to_y == 19


def test_parse_task_list():
    t1 = codec.pb_uint(1, 41515) + codec.pb_uint(2, 41503) + codec.pb_uint(3, 1) + codec.pb_uint(4, 1)
    t2 = codec.pb_uint(1, 41512) + codec.pb_uint(2, 41500) + codec.pb_uint(3, 0) + codec.pb_uint(4, 2)
    t3 = codec.pb_uint(1, 41513) + codec.pb_uint(2, 41501) + codec.pb_uint(3, 0) + codec.pb_uint(4, 0)
    body = codec.pb_uint(1, 109) + codec.pb_msg(2, t1) + codec.pb_msg(2, t2) + codec.pb_msg(2, t3)
    tasks = ss.parse_task_list(body)
    assert len(tasks) == 3
    assert tasks[0] == (41515, 41503, 1)  # claimable
    assert tasks[1] == (41512, 41500, 2)  # claimed
    assert tasks[2] == (41513, 41501, 0)  # not ready


# --- mock client helper ---------------------------------------------------

class FakeClient:
    def __init__(self, responses: dict[int, tuple[int, bytes]]):
        self._responses = responses

    def call(self, cmd, body=b"", *, expect_cmd=None, timeout=None):
        _rc, rb = self.call_for(cmd, body, expect_cmds=(cmd,), timeout=timeout)
        return rb

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        return self._responses.get(cmd, (0x0201, codec.pb_uint(1, 0)))


# --- orchestrator tests ---------------------------------------------------

def test_claim_season_tasks_claims_only_claimable():
    t1 = codec.pb_uint(1, 100) + codec.pb_uint(2, 200) + codec.pb_uint(4, 1)
    t2 = codec.pb_uint(1, 101) + codec.pb_uint(2, 201) + codec.pb_uint(4, 2)
    t3 = codec.pb_uint(1, 102) + codec.pb_uint(2, 202) + codec.pb_uint(4, 1)
    list_body = codec.pb_uint(1, 109) + codec.pb_msg(2, t1) + codec.pb_msg(2, t2) + codec.pb_msg(2, t3)

    claimed_ids = []

    class TrackingClient(FakeClient):
        def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
            if cmd == ss.CMD_TASK_LIST:
                return (ss.CMD_TASK_LIST, list_body)
            if cmd == ss.CMD_TASK_CLAIM:
                d = codec.walk_dict(body)
                claimed_ids.append(d.get(2))
                return (ss.CMD_TASK_CLAIM, b"")
            return super().call_for(cmd, body, expect_cmds=expect_cmds, timeout=timeout)

    client = TrackingClient({})
    result = ss.claim_season_tasks(client)
    assert result["claimed"] == 2
    assert result["claimable"] == 2
    assert result["total"] == 3
    assert sorted(claimed_ids) == [100, 102]


def test_dispatch_night_blocked():
    client = FakeClient({
        ss.CMD_DISPATCH: (ss.CMD_ERROR, codec.pb_uint(1, ss.ERR_NIGHT)),
    })
    r = ss._try_dispatch(client, ss.ACTION_GARRISON, 30, 22)
    assert not r.ok
    assert r.night_blocked
    assert r.error_code == ss.ERR_NIGHT


def test_dispatch_success():
    from_grid = codec.pb_uint(1, 30) + codec.pb_uint(2, 26)
    to_grid = codec.pb_uint(1, 19) + codec.pb_uint(2, 21)
    march_body = codec.pb_msg(6, from_grid) + codec.pb_msg(7, to_grid)
    client = FakeClient({
        ss.CMD_DISPATCH: (ss.CMD_MARCH_RECORD, march_body),
    })
    r = ss._try_dispatch(client, ss.ACTION_GARRISON, 19, 21)
    assert r.ok
    assert r.march.to_x == 19
    assert r.march.to_y == 21
    assert r.march.from_x == 30
    assert r.march.from_y == 26


def test_dispatch_invalid():
    client = FakeClient({
        ss.CMD_DISPATCH: (ss.CMD_ERROR, codec.pb_uint(1, ss.ERR_INVALID)),
    })
    r = ss._try_dispatch(client, ss.ACTION_GARRISON, 0, 0)
    assert not r.ok
    assert not r.night_blocked
    assert r.error_code == ss.ERR_INVALID


def test_claim_map_income():
    client = FakeClient({
        ss.CMD_MAP_INCOME_CLAIM: (ss.CMD_INVENTORY, b""),
    })
    r = ss.claim_map_income(client)
    assert r["ok"]


def test_build_repair_station_success():
    reply = codec.pb_uint(1, 3) + codec.pb_uint(2, 54130)
    client = FakeClient({
        ss.CMD_REPAIR_BUILD: (ss.CMD_REPAIR_BUILD, reply),
    })
    r = ss.build_repair_station(client, 94200)
    assert r["ok"]
    assert r["new_level"] == 3
    assert r["wood_spent"] == 94200


def test_build_repair_station_no_wood():
    client = FakeClient({})
    r = ss.build_repair_station(client, 0)
    assert "skipped" in r

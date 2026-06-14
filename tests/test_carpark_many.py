"""Tests for ws_token.carpark.auto_select_and_park_many — multi-mount parking.

search (12808 type=4 returns ALL pools) -> silver-tier filter (ceng 5..34) ->
prefer 鉑銀9/10 (ceng 13/14) then other silver by ceng -> per-lot 12801 ->
park up to ``count`` mounts via 12847, spilling across lots; a rejected park
falls through to the next lot.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.carpark import (  # noqa: E402
    CMD_CAR_INFO,
    CMD_COLLECT_BAG,
    CMD_CROSS_NEW_START,
    CMD_ERROR,
    CMD_LOT_INFO,
    CMD_SEARCH,
    SILVER_PREFERRED_LEVELS,
    auto_select_and_park_many,
    collect_bag_rewards,
    is_silver_ceng,
    silver_level_to_ceng,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _space(pos, role_id):
    return codec.pb_uint(1, pos) + codec.pb_uint(2, role_id)


def _lot_body(type_, master_id, spaces, ceng=0):
    body = codec.pb_uint(1, type_) + codec.pb_uint(2, master_id)
    body += b"".join(codec.pb_msg(7, s) for s in spaces)
    body += codec.pb_uint(12, ceng)
    return body


def _car(mount_id):
    return (codec.pb_uint(1, mount_id) + codec.pb_uint(2, 1)
            + codec.pb_msg(5, codec.pb_uint(1, 0)))


def _car_list_body(cars):
    return b"".join(codec.pb_msg(1, c) for c in cars)


def _null_entry(master_id, null_num, ceng=13, park_type=3):
    return (codec.pb_uint(1, park_type) + codec.pb_uint(2, master_id)
            + codec.pb_uint(3, null_num) + codec.pb_uint(7, ceng))


def _search_body(entries):
    return b"".join(codec.pb_msg(1, e) for e in entries)


def _ok_start(body):
    park_id = codec.walk_dict(body)[1]
    reply = codec.pb_uint(1, park_id) + codec.pb_msg(2, _space(1, 42))
    return [s2c(CMD_CROSS_NEW_START, reply)]


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_many_parks_count_mounts_in_one_lot():
    # Lot 900 has 10 free slots; 3 idle mounts; count=2 -> exactly 2 parks.
    search = _search_body([_null_entry(900, null_num=10)])
    lot = _lot_body(3, 900, [])
    cars = _car_list_body([_car(11), _car(22), _car(33)])
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=2)
        assert out["parked_count"] == 2
        assert out["requested"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        # distinct mounts into distinct positions of lot 900
        assert [d[1] for d in sent] == [900, 900]
        assert [d[2] for d in sent] == [1, 2]      # pos 1 then 2
        assert [d[3] for d in sent] == [11, 22]    # mounts in order
    finally:
        c.close()


def test_many_spills_across_lots_when_first_fills():
    # Lot 900 has only pos 10 free (1..9 occupied); lot 901 empty. count=2.
    search = _search_body([_null_entry(900, null_num=1, ceng=13),
                           _null_entry(901, null_num=10, ceng=14)])
    details = {
        900: _lot_body(3, 900, [_space(p, p + 50) for p in range(1, 10)]),
        901: _lot_body(3, 901, []),
    }
    cars = _car_list_body([_car(11), _car(22)])
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=2)
        assert out["parked_count"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert [(d[1], d[2]) for d in sent] == [(900, 10), (901, 1)]
    finally:
        c.close()


def test_many_stops_when_mounts_run_out():
    search = _search_body([_null_entry(900, null_num=10)])
    cars = _car_list_body([_car(11)])  # only one idle mount, count=3
    c, _ = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, _lot_body(3, 900, []))],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=3)
        assert out["parked_count"] == 1
        assert out["reason"] == "no_more_mounts"
    finally:
        c.close()


def test_many_no_mount_short_circuits():
    c, fake = _client({
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, b"")],
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, b"")],
    })
    try:
        out = auto_select_and_park_many(c, count=2)
        assert out["parked_count"] == 0
        assert out["reason"] == "no_available_mount"
        assert CMD_SEARCH not in fake.sent_cmds()
    finally:
        c.close()


def test_many_park_error_falls_to_next_lot():
    search = _search_body([_null_entry(900, null_num=10, ceng=13),
                           _null_entry(901, null_num=10, ceng=14)])
    details = {900: _lot_body(3, 900, []), 901: _lot_body(3, 901, [])}

    def _start(b):
        d = codec.walk_dict(b)
        if d[1] == 900:
            return [s2c(CMD_ERROR, codec.pb_uint(1, 9))]
        return _ok_start(b)

    c, _ = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO,
                                      _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _start,
    })
    try:
        out = auto_select_and_park_many(c, count=1)
        assert out["parked_count"] == 1
        assert out["results"][-1]["target_id"] == 901
    finally:
        c.close()


def test_many_silver_only_filters_non_silver_lots():
    # ceng=2 (diamond range) and ceng=40 (bronze range) are dropped; only the
    # silver lot (ceng=13 == 鉑銀9) is attempted.
    search = _search_body([_null_entry(800, null_num=10, ceng=2),
                           _null_entry(900, null_num=10, ceng=13),
                           _null_entry(801, null_num=10, ceng=40)])
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, _lot_body(3, 900, [], ceng=13))],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=1)
        assert out["parked_count"] == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_LOT_INFO]
        assert [d[2] for d in sent] == [900]
    finally:
        c.close()


def test_many_prefers_levels_9_10_then_other_silver():
    # lots: 鉑銀5 (ceng 9), 鉑銀10 (ceng 14), 鉑銀9 (ceng 13) — order in the
    # search reply is shuffled; expected attempt order: 13, 14, then 9.
    search = _search_body([_null_entry(905, null_num=10, ceng=9),
                           _null_entry(910, null_num=10, ceng=14),
                           _null_entry(909, null_num=10, ceng=13)])
    details = {909: _lot_body(3, 909, [], ceng=13),
               910: _lot_body(3, 910, [], ceng=14),
               905: _lot_body(3, 905, [], ceng=9)}
    cars = _car_list_body([_car(11), _car(22), _car(33)])
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=3)
        assert out["parked_count"] == 3
        # one mount per... no: count=3 fills lot 909 first (pos 1,2,3)
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert [d[1] for d in sent] == [909, 909, 909]
    finally:
        c.close()


def test_many_falls_back_to_other_silver_when_preferred_full():
    # 鉑銀9/10 absent from parkable list (full) -> parks into 鉑銀1 (ceng 5).
    search = _search_body([_null_entry(901, null_num=10, ceng=5)])
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, _lot_body(3, 901, [], ceng=5))],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=1)
        assert out["parked_count"] == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert sent[0][1] == 901
    finally:
        c.close()


def test_many_prefers_cluster_within_preferred_levels():
    # 鉑銀9 (ceng 13) nearly empty vs 鉑銀10 (ceng 14) with only 2 free slots:
    # the cluster (fewest free = most occupied) wins within the preferred group.
    search = _search_body([_null_entry(909, null_num=9, ceng=13),
                           _null_entry(910, null_num=2, ceng=14)])
    details = {910: _lot_body(3, 910, [_space(p, p + 50) for p in range(1, 9)],
                              ceng=14)}
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=1)
        assert out["parked_count"] == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert sent[0][1] == 910 and sent[0][2] == 9  # first free pos
    finally:
        c.close()


def _space_with_server(pos, role_id, server_id):
    # info_list#6 (p_role_change) -> kv#1 (p_key_value {k#1, v#2})
    kv = codec.pb_msg(1, codec.pb_uint(1, 1002) + codec.pb_uint(2, server_id))
    return (codec.pb_uint(1, pos) + codec.pb_uint(2, role_id)
            + codec.pb_msg(6, kv))


def test_count_same_server_counts_matching_attr_values():
    from ws_token.carpark import count_same_server, parse_car_park_info
    body = _lot_body(3, 900, [
        _space_with_server(1, 11, 1467),
        _space_with_server(2, 12, 8888),
        _space_with_server(3, 13, 1467),
        _space(4, 0),  # empty, no attrs
    ])
    lot = parse_car_park_info(body)
    assert count_same_server(lot, 1467) == 2
    assert count_same_server(lot, 9999) == 0
    assert count_same_server(lot, 0) == 0


def test_many_cluster_prefers_lot_with_same_server_occupants():
    # Both 鉑銀9 (ceng13) and 鉑銀10 (ceng14) parkable; 13 fuller (null=5) but
    # its occupants are FOREIGN; 14 (null=7) has 2 same-server (1467) cars ->
    # cluster_server_id=1467 must pick 14 despite lower occupancy.
    search = _search_body([_null_entry(913, null_num=5, ceng=13),
                           _null_entry(914, null_num=7, ceng=14)])
    details = {
        913: _lot_body(3, 913, [_space_with_server(p, p + 50, 8888)
                                for p in range(1, 6)], ceng=13),
        914: _lot_body(3, 914, [_space_with_server(1, 61, 1467),
                                _space_with_server(2, 62, 1467),
                                _space_with_server(3, 63, 8888)], ceng=14),
    }
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=1, cluster_server_id=1467)
        assert out["parked_count"] == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert sent[0][1] == 914  # same-server cluster wins
        assert sent[0][2] == 4    # first free pos (1-3 occupied)
    finally:
        c.close()


def test_many_cluster_no_attr_data_degrades_to_occupancy():
    # No server attrs anywhere -> counts all 0 -> fuller lot (13) wins as before.
    search = _search_body([_null_entry(913, null_num=5, ceng=13),
                           _null_entry(914, null_num=10, ceng=14)])
    details = {
        913: _lot_body(3, 913, [_space(p, p + 50) for p in range(1, 6)], ceng=13),
        914: _lot_body(3, 914, [], ceng=14),
    }
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: _ok_start,
    })
    try:
        out = auto_select_and_park_many(c, count=1, cluster_server_id=1467)
        assert out["parked_count"] == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_CROSS_NEW_START]
        assert sent[0][1] == 913
    finally:
        c.close()


def test_collect_bag_rewards_success():
    reply = codec.pb_uint(1, 1)
    c, fake = _client({CMD_COLLECT_BAG:
                       lambda _b: [s2c(CMD_COLLECT_BAG, reply)]})
    try:
        out = collect_bag_rewards(c)
        assert out["success"] is True
        sent = [b for _s, cmd, b in fake.framed_sent()
                if cmd == CMD_COLLECT_BAG]
        assert sent == [b""]  # empty c2s
    finally:
        c.close()


def test_collect_bag_rewards_declined_is_not_an_error():
    c, _ = _client({CMD_COLLECT_BAG:
                    lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 5))]})
    try:
        out = collect_bag_rewards(c)
        assert out["success"] is False
        assert out["error_code"] == 5
    finally:
        c.close()


def test_silver_level_ceng_mapping():
    assert SILVER_PREFERRED_LEVELS == (9, 10)
    assert silver_level_to_ceng(1) == 5
    assert silver_level_to_ceng(9) == 13
    assert silver_level_to_ceng(10) == 14
    assert silver_level_to_ceng(30) == 34
    assert is_silver_ceng(5) and is_silver_ceng(34)
    assert not is_silver_ceng(4) and not is_silver_ceng(35)

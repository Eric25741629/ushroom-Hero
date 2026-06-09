"""Tests for ws_token.carpark — read a cross (跨界) lot and park into a free slot.

Scope is STRICTLY: read a parking lot, find a cross free slot, park my mount in.
No collecting, no warehouse, no auto-collect toggle. Cross == type 3.

Schemas are the live-exported truth (docs/protocol/CARPARK_PROTO_SCHEMA.json +
TYPE_PROTO_SCHEMA.json):
  car_park_info_s2c { type#1, master_id#2, ..., space_num#6,
                      space_list#7:repeated p_car_park_space, ..., ceng#12, ... }
  p_car_park_space  { pos#1, role_id#2:uint64, mount_id#3:uint32, mount_lev#4,
                      start_time#5, ..., car_master_name#9:string }
        -> a slot is EMPTY iff role_id == 0.
  car_park_car_info_s2c { car_list#1:repeated p_car_park_car }
  p_car_park_car    { mount_id#1:uint32, car_lev#2, car_exp#3, minute#4,
                      parking_data#5:p_car_park_parking (present iff parking) }

  cross_car_park_new_parking_start_c2s 12847 { park_id#1:uint64, pos#2:uint32, mount_id#3:uint64 }
  cross_car_park_parking_start_c2s     12832 { id#1:uint64, mount_id#2:uint64, pos#3:uint32 }
        -> NEW has pos#2 / mount_id#3; OLD has mount_id#2 / pos#3 (swapped!).
  cross_car_park_new_parking_start_s2c { park_id#1, space#2:p_car_park_space }
  cross_car_park_parking_start_s2c     { id#1, space#2:p_car_park_space }
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
    CMD_CROSS_NEW_START,
    CMD_CROSS_OLD_START,
    CMD_ERROR,
    CMD_LOT_INFO,
    CarParkLot,
    Mount,
    ParkResult,
    Space,
    auto_park_cross,
    build_cross_new_start_body,
    build_cross_old_start_body,
    build_lot_info_body,
    parse_car_park_info,
    parse_my_mounts,
    park_into_cross,
    read_lot,
    read_my_mounts,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _space(pos, role_id, mount_id=0, mount_lev=0, start_time=0, name=""):
    out = (codec.pb_uint(1, pos) + codec.pb_uint(2, role_id)
           + codec.pb_uint(3, mount_id) + codec.pb_uint(4, mount_lev)
           + codec.pb_uint(5, start_time))
    if name:
        out += codec.pb_str(9, name)
    return out


def _lot_body(type_, master_id, spaces, ceng=0):
    body = codec.pb_uint(1, type_) + codec.pb_uint(2, master_id)
    body += codec.pb_uint(6, len(spaces))
    body += b"".join(codec.pb_msg(7, s) for s in spaces)
    body += codec.pb_uint(12, ceng)
    return body


def _car(mount_id, car_lev=1, parking=False):
    out = codec.pb_uint(1, mount_id) + codec.pb_uint(2, car_lev)
    if parking:
        # parking_data#5 present -> mount is busy parking somewhere
        out += codec.pb_msg(5, codec.pb_uint(1, 3) + codec.pb_uint(3, 0))
    return out


def _car_list_body(cars):
    return b"".join(codec.pb_msg(1, c) for c in cars)


# --- parse_car_park_info: type==3 is cross, role_id==0 is empty --------------

def test_parse_car_park_info_marks_cross_and_empty_slots():
    # Arrange: a cross lot (type 3), pos 0 occupied, pos 1 free.
    body = _lot_body(3, 5001, [
        _space(0, 777, mount_id=12, name="占用者"),
        _space(1, 0, name=""),
    ], ceng=2)

    # Act
    lot = parse_car_park_info(body)

    # Assert
    assert isinstance(lot, CarParkLot)
    assert lot.type == 3
    assert lot.is_cross is True
    assert lot.master_id == 5001
    assert lot.ceng == 2
    assert len(lot.spaces) == 2
    assert lot.spaces[0] == Space(pos=0, role_id=777, occupied=True)
    assert lot.spaces[1] == Space(pos=1, role_id=0, occupied=False)


def test_parse_car_park_info_non_cross_type_is_not_cross():
    lot = parse_car_park_info(_lot_body(1, 9, [_space(0, 0)]))
    assert lot.type == 1
    assert lot.is_cross is False


def test_car_park_lot_free_positions_lists_only_empty():
    lot = parse_car_park_info(_lot_body(3, 1, [
        _space(0, 100), _space(1, 0), _space(2, 0), _space(3, 200),
    ]))
    assert lot.free_positions() == [1, 2]
    assert lot.first_free_pos() == 1


def test_first_free_pos_returns_none_when_full():
    lot = parse_car_park_info(_lot_body(3, 1, [_space(0, 1), _space(1, 2)]))
    assert lot.free_positions() == []
    assert lot.first_free_pos() is None


# --- build_cross_*_start_body: field-number order (the easy-to-swap trap) -----

def test_build_cross_new_start_body_field_order():
    # NEW 12847: park_id#1, pos#2, mount_id#3
    body = build_cross_new_start_body(park_id=900, pos=4, mount_id=55)
    assert codec.walk_dict(body) == {1: 900, 2: 4, 3: 55}
    # and the literal wire is park_id, then pos, then mount_id
    assert body == (codec.pb_uint(1, 900) + codec.pb_uint(2, 4)
                    + codec.pb_uint(3, 55))


def test_build_cross_old_start_body_field_order_is_swapped():
    # OLD 12832: id#1, mount_id#2, pos#3  (mount_id/pos swapped vs NEW)
    body = build_cross_old_start_body(id_=900, mount_id=55, pos=4)
    assert codec.walk_dict(body) == {1: 900, 2: 55, 3: 4}
    assert body == (codec.pb_uint(1, 900) + codec.pb_uint(2, 55)
                    + codec.pb_uint(3, 4))


def test_new_and_old_bodies_differ_for_same_logical_args():
    # Same logical (target=900, pos=4, mount=55) must NOT produce equal wire.
    new = build_cross_new_start_body(park_id=900, pos=4, mount_id=55)
    old = build_cross_old_start_body(id_=900, mount_id=55, pos=4)
    assert new != old


# --- parse_my_mounts: exclude mounts already parking -------------------------

def test_parse_my_mounts_excludes_parking_mounts():
    body = _car_list_body([
        _car(11, parking=False),
        _car(22, parking=True),   # busy -> excluded
        _car(33, parking=False),
    ])
    mounts = parse_my_mounts(body)
    assert [m.mount_id for m in mounts] == [11, 33]
    assert all(isinstance(m, Mount) for m in mounts)
    assert mounts[0].parking is False


def test_parse_my_mounts_empty():
    assert parse_my_mounts(b"") == []


def test_build_lot_info_body_carries_type_master_ceng():
    body = build_lot_info_body(type_=3, master_id=4242, ceng=1)
    assert codec.walk_dict(body) == {1: 3, 2: 4242, 3: 1}


# --- client roundtrips against the fake transport ----------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_read_lot_roundtrip_sends_info_request():
    body = _lot_body(3, 5001, [_space(0, 0), _space(1, 9)])
    c, fake = _client({CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, body)]})
    try:
        lot = read_lot(c, type=3, master_id=5001, ceng=0)
        assert lot.is_cross is True
        assert lot.first_free_pos() == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_LOT_INFO]
        assert codec.walk_dict(sent[0]) == {1: 3, 2: 5001, 3: 0}
    finally:
        c.close()


def test_read_my_mounts_roundtrip():
    body = _car_list_body([_car(11), _car(22, parking=True)])
    c, _ = _client({CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, body)]})
    try:
        mounts = read_my_mounts(c)
        assert [m.mount_id for m in mounts] == [11]
    finally:
        c.close()


# --- park_into_cross: new vs old, success and 0x0201 error -------------------

def test_park_into_cross_new_sends_new_cmd_and_succeeds():
    space = _space(2, 89555436834913, mount_id=11, name="me")
    reply = codec.pb_uint(1, 900) + codec.pb_msg(2, space)  # park_id#1, space#2
    c, fake = _client({CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_CROSS_NEW_START, reply)]})
    try:
        r = park_into_cross(c, target_id=900, pos=2, mount_id=11, new=True)
        assert isinstance(r, ParkResult)
        assert r.success is True
        assert r.response_cmd == CMD_CROSS_NEW_START
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_NEW_START]
        assert codec.walk_dict(sent[0]) == {1: 900, 2: 2, 3: 11}  # park_id, pos, mount
    finally:
        c.close()


def test_park_into_cross_old_sends_old_cmd_with_swapped_fields():
    space = _space(2, 1, mount_id=11)
    reply = codec.pb_uint(1, 900) + codec.pb_msg(2, space)  # id#1, space#2
    c, fake = _client({CMD_CROSS_OLD_START: lambda _b: [s2c(CMD_CROSS_OLD_START, reply)]})
    try:
        r = park_into_cross(c, target_id=900, pos=2, mount_id=11, new=False)
        assert r.success is True
        assert r.response_cmd == CMD_CROSS_OLD_START
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_OLD_START]
        # OLD: id#1, mount_id#2, pos#3
        assert codec.walk_dict(sent[0]) == {1: 900, 2: 11, 3: 2}
    finally:
        c.close()


def test_park_into_cross_error_channel_fails():
    c, _ = _client({CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 7))]})
    try:
        r = park_into_cross(c, target_id=900, pos=2, mount_id=11, new=True)
        assert r.success is False
        assert r.error_code == 7
        assert r.response_cmd == CMD_ERROR
    finally:
        c.close()


# --- auto_park_cross: read lot -> first free -> grab mount -> park -----------

def test_auto_park_cross_parks_first_free_slot():
    lot_body = _lot_body(3, 5001, [_space(0, 100), _space(1, 0), _space(2, 0)])
    cars_body = _car_list_body([_car(11), _car(22, parking=True)])
    space = _space(1, 89555436834913, mount_id=11, name="me")
    start_reply = codec.pb_uint(1, 5001) + codec.pb_msg(2, space)
    c, fake = _client({
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot_body)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars_body)],
        CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_CROSS_NEW_START, start_reply)],
    })
    try:
        result = auto_park_cross(c, target_id=5001, new=True)
        assert result["parked"] is True
        assert result["pos"] == 1          # first free slot
        assert result["mount_id"] == 11    # first non-parking mount
        assert result["result"].success is True
        # confirm we actually framed the NEW start with the right fields
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_NEW_START]
        assert codec.walk_dict(sent[0]) == {1: 5001, 2: 1, 3: 11}
    finally:
        c.close()


def test_auto_park_cross_no_free_slot_does_not_park():
    lot_body = _lot_body(3, 5001, [_space(0, 100), _space(1, 200)])
    c, fake = _client({
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot_body)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_CROSS_NEW_START, b"")],
    })
    try:
        result = auto_park_cross(c, target_id=5001, new=True)
        assert result["parked"] is False
        assert result["reason"] == "no_free_slot"
        # never sent a start
        assert CMD_CROSS_NEW_START not in fake.sent_cmds()
    finally:
        c.close()


def test_auto_park_cross_no_mount_does_not_park():
    lot_body = _lot_body(3, 5001, [_space(0, 0)])
    cars_body = _car_list_body([_car(11, parking=True)])  # only mount is busy
    c, fake = _client({
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot_body)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars_body)],
        CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_CROSS_NEW_START, b"")],
    })
    try:
        result = auto_park_cross(c, target_id=5001, new=True)
        assert result["parked"] is False
        assert result["reason"] == "no_available_mount"
        assert CMD_CROSS_NEW_START not in fake.sent_cmds()
    finally:
        c.close()


def test_auto_park_cross_server_error_marks_failure():
    lot_body = _lot_body(3, 5001, [_space(0, 0)])
    cars_body = _car_list_body([_car(11)])
    c, _ = _client({
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot_body)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars_body)],
        CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 4))],
    })
    try:
        result = auto_park_cross(c, target_id=5001, new=True)
        assert result["parked"] is False
        assert result["reason"] == "park_failed"
        assert result["result"].error_code == 4
    finally:
        c.close()

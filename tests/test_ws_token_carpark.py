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
    CMD_CROSS_INFO,
    CMD_CROSS_NEW_START,
    CMD_CROSS_OLD_START,
    CMD_CROSS_PREVIEW,
    CMD_ERROR,
    CMD_LOT_INFO,
    CMD_SEARCH,
    SEARCH_TYPE_CROSS,
    CarParkLot,
    CrossLotDetail,
    CrossLotPreview,
    CROSS_TYPE,
    Mount,
    NullSpace,
    ParkResult,
    ParkingInfo,
    Space,
    auto_park_cross,
    auto_select_and_park,
    build_cross_info_body,
    build_cross_new_start_body,
    build_cross_old_start_body,
    build_lot_info_body,
    build_search_body,
    parse_all_cars,
    parse_car_park_info,
    parse_cross_info,
    parse_cross_preview,
    parse_my_mounts,
    parse_null_spaces,
    parse_parked_cross,
    park_into_cross,
    read_cross_info,
    read_cross_null_spaces,
    read_cross_preview,
    read_lot,
    read_my_mounts,
    read_parked_cross,
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
        # parking_data#5 with a NON-ZERO field -> mount is busy parking somewhere
        out += codec.pb_msg(5, codec.pb_uint(1, 3) + codec.pb_uint(3, 0))
    else:
        # The live server ALWAYS sends parking_data#5, all-zero, for an IDLE mount
        # (verified on 小寶). Mirror that so the "present != parked" rule is tested.
        out += codec.pb_msg(5, codec.pb_uint(1, 0) + codec.pb_uint(2, 0)
                            + codec.pb_uint(3, 0) + codec.pb_uint(4, 0)
                            + codec.pb_uint(5, 0) + codec.pb_uint(6, 0))
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


def test_first_free_cross_pos_derives_from_capacity_minus_occupied():
    # CROSS lot lists ONLY occupied slots; pos is 1-based. Occupied {1,2,3,4,6,8,10}
    # (live 小寶 lot 1001001045) -> first free is 5.
    lot = parse_car_park_info(_lot_body(3, 1001001045, [
        _space(1, 11), _space(2, 12), _space(3, 13), _space(4, 14),
        _space(6, 16), _space(8, 18), _space(10, 20),
    ]))
    assert lot.occupied_positions() == {1, 2, 3, 4, 6, 8, 10}
    assert lot.first_free_cross_pos() == 5
    # space_list-based first_free_pos is WRONG for cross (empties absent) -> None
    assert lot.first_free_pos() is None


def test_first_free_cross_pos_empty_lot_is_pos_1():
    lot = parse_car_park_info(_lot_body(3, 1, []))
    assert lot.first_free_cross_pos() == 1


def test_first_free_cross_pos_full_lot_is_none():
    lot = parse_car_park_info(_lot_body(3, 1, [_space(p, p + 100) for p in range(1, 11)]))
    assert lot.first_free_cross_pos() is None


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


def test_parse_my_mounts_keeps_idle_mounts_with_allzero_parking_data():
    # Regression (live 小寶 2026-06-09): the server sends parking_data#5 ALWAYS,
    # all-zero for an idle mount. Real captured car_list entry for mount_id=1:
    #   f1=1 (mount_id) f2=104 (car_lev) f3=2148817 (car_exp) f4=0 (minute)
    #   f5={1:0,2:0,3:0,4:0,5:0,6:0} (parking_data, all zero -> NOT parked)
    real_entry = bytes.fromhex(
        "0801106818d193830120002a0c080010001800200028003000")
    body = codec.pb_msg(1, real_entry)
    mounts = parse_my_mounts(body)

    assert len(mounts) == 1                       # was 0 before the fix
    assert mounts[0].mount_id == 1
    assert mounts[0].car_lev == 104
    assert mounts[0].parking is False


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


# --- cross preview (12830) / cross info (12831) ------------------------------
#
# Schemas (live-exported, docs/protocol/CARPARK_PROTO_SCHEMA.json +
# TYPE_PROTO_SCHEMA.json):
#   cross_car_park_preview_c2s {}            -> s2c { park_list#1:
#       repeated p_cross_car_park_preview {id#1:u64, server_id#2:u64,
#           protect_end#3:u64, def_num#4:u32, atk_num#5:u32, ext#6} }
#   cross_car_park_info_c2s {id#1:u64}       -> s2c { park_info#1:
#       p_cross_car_park {id#1:u64, server_id#2:u64,
#           space_list#3:repeated p_car_park_space, def_list#4, atk_list#5, ext#6} }

def _preview_entry(id_, server_id=1, protect_end=0, def_num=0, atk_num=0):
    return (codec.pb_uint(1, id_) + codec.pb_uint(2, server_id)
            + codec.pb_uint(3, protect_end) + codec.pb_uint(4, def_num)
            + codec.pb_uint(5, atk_num))


def _preview_body(entries):
    return b"".join(codec.pb_msg(1, e) for e in entries)


def _cross_info_body(id_, spaces, server_id=1):
    park = (codec.pb_uint(1, id_) + codec.pb_uint(2, server_id)
            + b"".join(codec.pb_msg(3, s) for s in spaces))
    return codec.pb_msg(1, park)


def test_parse_cross_preview_lists_lots():
    body = _preview_body([
        _preview_entry(900, server_id=7, protect_end=123, def_num=2, atk_num=1),
        _preview_entry(901, server_id=8),
    ])
    lots = parse_cross_preview(body)
    assert lots == [
        CrossLotPreview(id=900, server_id=7, protect_end=123, def_num=2, atk_num=1),
        CrossLotPreview(id=901, server_id=8, protect_end=0, def_num=0, atk_num=0),
    ]


def test_parse_cross_preview_empty():
    assert parse_cross_preview(b"") == []


def test_parse_cross_info_extracts_spaces():
    body = _cross_info_body(900, [_space(0, 77), _space(1, 0)], server_id=7)
    detail = parse_cross_info(body)
    assert isinstance(detail, CrossLotDetail)
    assert detail.id == 900
    assert detail.server_id == 7
    assert detail.spaces == (Space(pos=0, role_id=77, occupied=True),
                             Space(pos=1, role_id=0, occupied=False))
    assert detail.free_positions() == [1]
    assert detail.first_free_pos() == 1


def test_build_cross_info_body():
    assert codec.walk_dict(build_cross_info_body(id_=900)) == {1: 900}


def test_read_cross_preview_roundtrip():
    body = _preview_body([_preview_entry(900)])
    c, fake = _client({CMD_CROSS_PREVIEW: lambda _b: [s2c(CMD_CROSS_PREVIEW, body)]})
    try:
        lots = read_cross_preview(c)
        assert [lot.id for lot in lots] == [900]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_PREVIEW]
        assert sent == [b""]  # empty c2s
    finally:
        c.close()


def test_read_cross_info_roundtrip():
    body = _cross_info_body(900, [_space(0, 0)])
    c, fake = _client({CMD_CROSS_INFO: lambda _b: [s2c(CMD_CROSS_INFO, body)]})
    try:
        detail = read_cross_info(c, id_=900)
        assert detail.first_free_pos() == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_INFO]
        assert codec.walk_dict(sent[0]) == {1: 900}
    finally:
        c.close()


# --- car_park_search (12808, type=4) -> null_space (NEW cross flow source) ---
#
# car_park_search_c2s {type#1, park_name#2} -> _s2c {null_space#1:p_car_park_null}
# p_car_park_null {park_type#1, master_id#2, null_num#3, info_list#4, skin_plus#5,
#                  ext#6, ceng#7}  -> parkable iff null_num > 0.

def _null_entry(master_id, null_num, ceng=0, park_type=3):
    return (codec.pb_uint(1, park_type) + codec.pb_uint(2, master_id)
            + codec.pb_uint(3, null_num) + codec.pb_uint(7, ceng))


def _search_body(entries):
    return b"".join(codec.pb_msg(1, e) for e in entries)


def test_build_search_body_cross():
    assert codec.walk_dict(build_search_body(type_=SEARCH_TYPE_CROSS)) == {1: 4}
    assert SEARCH_TYPE_CROSS == 4


def test_parse_null_spaces_lists_lots():
    body = _search_body([
        _null_entry(900, null_num=3, ceng=1),
        _null_entry(901, null_num=0, ceng=2),
    ])
    lots = parse_null_spaces(body)
    assert lots == [
        NullSpace(park_type=3, master_id=900, null_num=3, ceng=1),
        NullSpace(park_type=3, master_id=901, null_num=0, ceng=2),
    ]


def test_read_cross_null_spaces_roundtrip():
    body = _search_body([_null_entry(900, null_num=2)])
    c, fake = _client({CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, body)]})
    try:
        lots = read_cross_null_spaces(c)
        assert [lot.master_id for lot in lots] == [900]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SEARCH]
        assert codec.walk_dict(sent[0]) == {1: 4}
    finally:
        c.close()


# --- auto_select_and_park: search -> per-lot info -> park into first free ----

def test_auto_select_and_park_picks_first_parkable_lot():
    search = _search_body([
        _null_entry(900, null_num=0, ceng=1),   # full -> skipped
        _null_entry(901, null_num=1, ceng=2),   # parkable
    ])
    # CROSS lot: 1-based pos, space_list lists ONLY occupied slots. pos 1 & 2
    # occupied -> first free is 3.
    lot_detail = _lot_body(3, 901, [_space(1, 5), _space(2, 7)], ceng=2)
    cars = _car_list_body([_car(11)])
    space = _space(3, 42, mount_id=11)
    start_reply = codec.pb_uint(1, 901) + codec.pb_msg(2, space)
    c, fake = _client({
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda _b: [s2c(CMD_LOT_INFO, lot_detail)],
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_CROSS_NEW_START: lambda _b: [s2c(CMD_CROSS_NEW_START, start_reply)],
    })
    try:
        result = auto_select_and_park(c, new=True)
        assert result["parked"] is True
        assert result["target_id"] == 901
        assert result["pos"] == 3          # first free 1-based pos (1,2 occupied)
        assert result["mount_id"] == 11
        # read_lot for 901 used master_id=901, ceng=2
        info_sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_LOT_INFO]
        assert codec.walk_dict(info_sent[0]) == {1: 3, 2: 901, 3: 2}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CROSS_NEW_START]
        assert codec.walk_dict(sent[0]) == {1: 901, 2: 3, 3: 11}
    finally:
        c.close()


def test_auto_select_and_park_no_mount_short_circuits():
    cars = _car_list_body([_car(11, parking=True)])
    c, fake = _client({
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, cars)],
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, b"")],
    })
    try:
        result = auto_select_and_park(c)
        assert result["parked"] is False
        assert result["reason"] == "no_available_mount"
        # never even searched
        assert CMD_SEARCH not in fake.sent_cmds()
    finally:
        c.close()


def test_auto_select_and_park_all_lots_full():
    search = _search_body([_null_entry(900, null_num=0)])
    c, fake = _client({
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
    })
    try:
        result = auto_select_and_park(c)
        assert result["parked"] is False
        assert result["reason"] == "no_cross_lot"   # 0 parkable (null_num all 0)
        assert CMD_CROSS_NEW_START not in fake.sent_cmds()
    finally:
        c.close()


def test_auto_select_and_park_empty_search():
    c, _ = _client({
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, b"")],
    })
    try:
        result = auto_select_and_park(c)
        assert result["parked"] is False
        assert result["reason"] == "no_cross_lot"
    finally:
        c.close()


def test_auto_select_and_park_falls_through_on_park_error():
    # Lot 900 claims a free slot but the park is rejected (race) -> try lot 901.
    search = _search_body([_null_entry(900, null_num=10, ceng=0),
                           _null_entry(901, null_num=10, ceng=0)])
    # both empty (no occupied slots) -> first_free_cross_pos == 1 for each
    details = {
        900: _lot_body(3, 900, [], ceng=0),
        901: _lot_body(3, 901, [], ceng=0),
    }
    space = _space(1, 42, mount_id=11)
    ok_reply = codec.pb_uint(1, 901) + codec.pb_msg(2, space)

    def _start(b):
        park_id = codec.walk_dict(b)[1]
        if park_id == 900:
            return [s2c(CMD_ERROR, codec.pb_uint(1, 9))]
        return [s2c(CMD_CROSS_NEW_START, ok_reply)]

    c, _ = _client({
        CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, _car_list_body([_car(11)]))],
        CMD_SEARCH: lambda _b: [s2c(CMD_SEARCH, search)],
        CMD_LOT_INFO: lambda b: [s2c(CMD_LOT_INFO,
                                     details[codec.walk_dict(b)[2]])],
        CMD_CROSS_NEW_START: _start,
    })
    try:
        result = auto_select_and_park(c)
        assert result["parked"] is True
        assert result["target_id"] == 901
        assert result["attempts"] == 2
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


# --- parking_data (#5 p_car_park_parking): read MY cross car's start_time -----
#
# p_car_park_car { mount_id#1, car_lev#2, car_exp#3, minute#4, parking_data#5 }
# p_car_park_parking { type#1(3=cross), master_id#2, pos#3, start_time#4(epoch) }
# An IDLE mount's parking_data is present-but-all-zero (verified on 小寶); only a
# non-zero parking_data means the mount is actually parked.

def _parked_car(mount_id, ptype=CROSS_TYPE, master_id=0, pos=0,
                start_time=0, car_lev=1):
    pdata = (codec.pb_uint(1, ptype) + codec.pb_uint(2, master_id)
             + codec.pb_uint(3, pos) + codec.pb_uint(4, start_time))
    return (codec.pb_uint(1, mount_id) + codec.pb_uint(2, car_lev)
            + codec.pb_msg(5, pdata))


def test_parse_all_cars_idle_mount_has_no_parking_info():
    cars = parse_all_cars(_car_list_body([_car(11)]))
    assert len(cars) == 1
    assert cars[0].mount_id == 11
    assert cars[0].parking is False
    assert cars[0].parking_info is None


def test_parse_parked_cross_reads_start_time():
    body = _car_list_body([
        _car(11),                                                   # idle
        _parked_car(22, ptype=CROSS_TYPE, master_id=1001001013,
                    pos=4, start_time=1_700_000_000),               # cross parked
    ])
    parked = parse_parked_cross(body)
    assert len(parked) == 1
    m = parked[0]
    assert m.mount_id == 22
    assert m.parking is True
    assert m.parking_info == ParkingInfo(
        type=CROSS_TYPE, master_id=1001001013, pos=4, start_time=1_700_000_000)


def test_parse_parked_cross_excludes_non_cross_park():
    # A 本服 (type=2) parked car is NOT a cross car -> excluded.
    body = _car_list_body([
        _parked_car(22, ptype=2, master_id=1467, pos=1, start_time=123),
        _parked_car(33, ptype=CROSS_TYPE, master_id=900, pos=2, start_time=456),
    ])
    parked = parse_parked_cross(body)
    assert [m.mount_id for m in parked] == [33]
    assert parked[0].parking_info.start_time == 456


def test_parse_parked_cross_ignores_idle_allzero_parking_data():
    # Idle mounts (all-zero parking_data) must never count as parked cross cars.
    body = _car_list_body([_car(11), _car(22)])
    assert parse_parked_cross(body) == []


def test_parse_my_mounts_still_excludes_parked_cross_car():
    # read_my_mounts (idle-only) must not regress: a cross-parked car is excluded.
    body = _car_list_body([
        _car(11),
        _parked_car(22, ptype=CROSS_TYPE, start_time=999),
    ])
    assert [m.mount_id for m in parse_my_mounts(body)] == [11]


def test_read_parked_cross_roundtrip():
    body = _car_list_body([
        _car(11),
        _parked_car(22, ptype=CROSS_TYPE, master_id=900, pos=3,
                    start_time=1_700_000_500),
    ])
    c, fake = _client({CMD_CAR_INFO: lambda _b: [s2c(CMD_CAR_INFO, body)]})
    try:
        parked = read_parked_cross(c)
        assert [m.mount_id for m in parked] == [22]
        assert parked[0].parking_info.start_time == 1_700_000_500
        # empty c2s body, cmd 12802
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CAR_INFO]
        assert sent == [b""]
    finally:
        c.close()

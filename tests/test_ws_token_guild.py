"""Tests for ws_token.guild — family (家族) daily over pure WS.

Schemas are the authoritative exported truth
(docs/protocol/GUILD_PROTO_SCHEMA.json + docs/protocol/TYPE_PROTO_SCHEMA.json):
  guild_donate         7441  c2s {}        -> s2c {donate_sum#1, donate_week#2, donate_count#3}
  guild_help_info      7452  c2s {type#1}  -> s2c {type#1, daily_count#2, help_list#3:p_guild_help[]}
  guild_help           7454  c2s {help_id#1}-> s2c {new_daily_count#1}
  guild_help_status    7456  c2s {}        -> s2c {status#1:p_guild_help_status[]}
  guild_treasure_info  7459  c2s {}        -> s2c {is_new#1, round#2, cfg_id#3, my_open#4,
                                                   countdown#5, box_list#6:p_guild_treasure_box[]}
  guild_treasure_open  7460  c2s {round#1, n#2} -> s2c {round#1, n#2, code#3}
  error.error_info_s2c 0x0201 {error_code#1}
  p_guild_treasure_box {n#1, pos#2:p_pos, open_num#3, open_limit#4, role_list#5:uint64[]}
  p_guild_help         {id#1, role_id#2, name#3, head#4:p_head, type#5, sub_type#6,
                        end_time#7, help_num#8, helpers#9:uint64[]}
  p_guild_help_status  {type#1, sub_type#2, status#3}
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.guild import (  # noqa: E402
    CMD_DONATE,
    CMD_ERROR,
    CMD_HELP,
    CMD_HELP_INFO,
    CMD_HELP_STATUS,
    CMD_TREASURE_INFO,
    CMD_TREASURE_OPEN,
    DonateResult,
    GuildHelp,
    HelpResult,
    HelpStatus,
    OpenResult,
    TreasureBox,
    TreasureInfo,
    build_help_body,  # noqa: F401  (re-exported for the body-contract test)
    build_help_info_body,
    build_treasure_open_body,
    donate,
    donate_until_capped,
    help_all,
    help_one,
    list_help,
    list_help_status,
    list_treasure,
    open_all_treasure,
    open_treasure,
    parse_donate,
    parse_help_info,
    parse_help_result,
    parse_help_status,
    parse_open_result,
    parse_treasure_info,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- builders for s2c body fixtures -----------------------------------------

def _help_entry(id_, role_id, name, type_=1, sub_type=0, end_time=0, help_num=0):
    return (codec.pb_uint(1, id_) + codec.pb_uint(2, role_id) + codec.pb_str(3, name)
            + codec.pb_uint(5, type_) + codec.pb_uint(6, sub_type)
            + codec.pb_uint(7, end_time) + codec.pb_uint(8, help_num))


def _help_info_body(type_, daily_count, entries):
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, daily_count)
            + b"".join(codec.pb_msg(3, e) for e in entries))


def _box_entry(n, open_num, open_limit, x=0, y=0):
    pos = codec.pb_uint(1, x) + codec.pb_uint(2, y)
    return (codec.pb_uint(1, n) + codec.pb_msg(2, pos)
            + codec.pb_uint(3, open_num) + codec.pb_uint(4, open_limit))


def _treasure_info_body(round_, my_open, boxes, is_new=0, cfg_id=0, countdown=0):
    return (codec.pb_uint(1, is_new) + codec.pb_uint(2, round_) + codec.pb_uint(3, cfg_id)
            + codec.pb_uint(4, my_open) + codec.pb_uint(5, countdown)
            + b"".join(codec.pb_msg(6, b) for b in boxes))


def _status_entry(type_, sub_type, status):
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, sub_type) + codec.pb_uint(3, status))


# --- cmd ids match the authoritative schema ---------------------------------

def test_cmd_ids_match_schema():
    assert (CMD_DONATE, CMD_HELP_INFO, CMD_HELP, CMD_HELP_STATUS,
            CMD_TREASURE_INFO, CMD_TREASURE_OPEN, CMD_ERROR) == (
        7441, 7452, 7454, 7456, 7459, 7460, 0x0201)


# --- build_* body byte structure (contract) ---------------------------------

def test_build_help_info_body_is_uint_type():
    # guild_help_info_c2s {type#1:uint32} ; type=0 -> tag(08) value(00)
    assert build_help_info_body(0) == bytes.fromhex("0800")
    assert build_help_info_body(2) == codec.pb_uint(1, 2)


def test_build_help_body_is_uint64_help_id():
    # guild_help_c2s {help_id#1:uint64}
    assert build_help_body(help_id=89616640123660) == codec.pb_uint(1, 89616640123660)


def test_build_treasure_open_body_is_round_then_n():
    # guild_treasure_open_c2s {round#1, n#2} -> 08 03 (round) 10 02 (n)
    assert build_treasure_open_body(round_=3, n=2) == bytes.fromhex("08031002")


# --- parse_donate -----------------------------------------------------------

def test_parse_donate_reads_three_fields():
    body = codec.pb_uint(1, 5000) + codec.pb_uint(2, 800) + codec.pb_uint(3, 4)
    r = parse_donate(CMD_DONATE, body)
    assert isinstance(r, DonateResult)
    assert (r.donate_sum, r.donate_week, r.donate_count) == (5000, 800, 4)
    assert r.success is True
    assert r.error_code is None


def test_parse_donate_error_channel_is_failure():
    r = parse_donate(CMD_ERROR, codec.pb_uint(1, 7))
    assert r.success is False and r.error_code == 7


# --- parse_help_info (repeated p_guild_help) --------------------------------

def test_parse_help_info_decodes_repeated_help_list():
    body = _help_info_body(2, 3, [
        _help_entry(11, 555, "甲", type_=1, sub_type=0, help_num=2),
        _help_entry(22, 666, "乙", type_=2, sub_type=5, help_num=0),
    ])
    info = parse_help_info(body)
    assert info.help_type == 2
    assert info.daily_count == 3
    assert len(info.help_list) == 2
    first = info.help_list[0]
    assert isinstance(first, GuildHelp)
    assert (first.help_id, first.role_id, first.name) == (11, 555, "甲")
    assert (first.type, first.sub_type, first.help_num) == (1, 0, 2)
    assert info.help_list[1].help_id == 22


def test_parse_help_info_empty_list():
    info = parse_help_info(_help_info_body(0, 0, []))
    assert info.help_list == []
    assert info.daily_count == 0


# --- parse_help_result ------------------------------------------------------

def test_parse_help_result_success_reads_new_daily_count():
    r = parse_help_result(CMD_HELP, codec.pb_uint(1, 4), help_id=11)
    assert isinstance(r, HelpResult)
    assert r.success is True and r.new_daily_count == 4 and r.help_id == 11


def test_parse_help_result_error_channel_is_failure():
    r = parse_help_result(CMD_ERROR, codec.pb_uint(1, 9), help_id=11)
    assert r.success is False and r.error_code == 9


# --- parse_help_status (repeated p_guild_help_status) -----------------------

def test_parse_help_status_decodes_repeated():
    body = (codec.pb_msg(1, _status_entry(1, 0, 1))
            + codec.pb_msg(1, _status_entry(2, 3, 0)))
    statuses = parse_help_status(body)
    assert len(statuses) == 2
    assert isinstance(statuses[0], HelpStatus)
    assert (statuses[0].type, statuses[0].sub_type, statuses[0].status) == (1, 0, 1)
    assert (statuses[1].type, statuses[1].sub_type, statuses[1].status) == (2, 3, 0)


# --- parse_treasure_info (repeated p_guild_treasure_box) --------------------

def test_parse_treasure_info_decodes_round_and_boxes():
    body = _treasure_info_body(7, my_open=3, boxes=[
        _box_entry(1, open_num=0, open_limit=5, x=2, y=3),
        _box_entry(2, open_num=5, open_limit=5),
    ])
    info = parse_treasure_info(body)
    assert isinstance(info, TreasureInfo)
    assert info.round == 7
    assert info.my_open == 3
    assert len(info.box_list) == 2
    box0 = info.box_list[0]
    assert isinstance(box0, TreasureBox)
    assert (box0.n, box0.open_num, box0.open_limit) == (1, 0, 5)
    assert box0.pos == (2, 3)
    assert box0.has_room is True
    assert info.box_list[1].has_room is False


# --- parse_open_result ------------------------------------------------------

def test_parse_open_result_success_code_zero():
    body = codec.pb_uint(1, 7) + codec.pb_uint(2, 1) + codec.pb_uint(3, 0)
    r = parse_open_result(CMD_TREASURE_OPEN, body, round_=7, n=1)
    assert isinstance(r, OpenResult)
    assert r.success is True and r.code == 0 and (r.round, r.n) == (7, 1)


def test_parse_open_result_nonzero_code_is_failure():
    body = codec.pb_uint(1, 7) + codec.pb_uint(2, 1) + codec.pb_uint(3, 2)
    r = parse_open_result(CMD_TREASURE_OPEN, body, round_=7, n=1)
    assert r.success is False and r.code == 2


def test_parse_open_result_error_channel_is_failure():
    r = parse_open_result(CMD_ERROR, codec.pb_uint(1, 3), round_=7, n=1)
    assert r.success is False and r.error_code == 3


# --- orchestrators over the fake transport ----------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_donate_roundtrip_sends_empty_body():
    body = codec.pb_uint(1, 100) + codec.pb_uint(2, 20) + codec.pb_uint(3, 1)
    c, fake = _client({CMD_DONATE: lambda _b: [s2c(CMD_DONATE, body)]})
    try:
        r = donate(c)
        assert r.success is True and r.donate_count == 1
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DONATE]
        assert sent == [b""]  # empty c2s body
    finally:
        c.close()


def test_donate_until_capped_stops_when_count_stops_increasing():
    # count climbs 1,2,3 then plateaus at 3 -> loop must stop on the plateau.
    counts = iter([1, 2, 3, 3, 3])

    def resp(_b):
        c = next(counts)
        return [s2c(CMD_DONATE, codec.pb_uint(1, c * 10) + codec.pb_uint(2, c)
                    + codec.pb_uint(3, c))]

    c, fake = _client({CMD_DONATE: resp})
    try:
        summary = donate_until_capped(c, max_attempts=10, spacing=0)
        # 3 increasing donations counted; the 4th (plateau) detected and stopped.
        assert summary["donated"] == 3
        assert summary["final_count"] == 3
        assert summary["stopped_reason"] == "no_increase"
    finally:
        c.close()


def test_donate_until_capped_stops_on_error_channel():
    seq = iter([1, 2])

    def resp(_b):
        try:
            c = next(seq)
            return [s2c(CMD_DONATE, codec.pb_uint(1, c) + codec.pb_uint(2, c)
                        + codec.pb_uint(3, c))]
        except StopIteration:
            return [s2c(CMD_ERROR, codec.pb_uint(1, 2))]  # daily cap reached

    c, _ = _client({CMD_DONATE: resp})
    try:
        summary = donate_until_capped(c, max_attempts=10, spacing=0)
        assert summary["donated"] == 2
        assert summary["stopped_reason"] == "error"
        assert summary["error_code"] == 2
    finally:
        c.close()


def test_list_treasure_roundtrip():
    body = _treasure_info_body(4, my_open=2, boxes=[_box_entry(1, 0, 3)])
    c, _ = _client({CMD_TREASURE_INFO: lambda _b: [s2c(CMD_TREASURE_INFO, body)]})
    try:
        info = list_treasure(c)
        assert info.round == 4 and len(info.box_list) == 1
    finally:
        c.close()


def test_open_treasure_sends_round_and_n():
    ok = codec.pb_uint(1, 4) + codec.pb_uint(2, 1) + codec.pb_uint(3, 0)
    c, fake = _client({CMD_TREASURE_OPEN: lambda _b: [s2c(CMD_TREASURE_OPEN, ok)]})
    try:
        r = open_treasure(c, round_=4, n=1)
        assert r.success is True
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_TREASURE_OPEN]
        assert codec.walk_dict(sent[0]) == {1: 4, 2: 1}
    finally:
        c.close()


def test_open_all_treasure_skips_full_boxes_and_respects_my_open():
    # 3 boxes: #1 has room, #2 full (skip), #3 has room. my_open=1 -> only 1 open.
    info_body = _treasure_info_body(5, my_open=1, boxes=[
        _box_entry(1, 0, 3),
        _box_entry(2, 3, 3),
        _box_entry(3, 1, 3),
    ])
    opened: list[int] = []

    def open_resp(body):
        n = codec.walk_dict(body).get(2)
        opened.append(n)
        return [s2c(CMD_TREASURE_OPEN, codec.pb_uint(1, 5) + codec.pb_uint(2, n)
                    + codec.pb_uint(3, 0))]

    c, _ = _client({CMD_TREASURE_INFO: lambda _b: [s2c(CMD_TREASURE_INFO, info_body)],
                    CMD_TREASURE_OPEN: open_resp})
    try:
        summary = open_all_treasure(c, spacing=0)
        assert summary["opened"] == 1          # my_open guard caps at 1
        assert opened == [1]                   # the full box (#2) was never sent
        assert summary["stopped_reason"] == "my_open_reached"
    finally:
        c.close()


def test_open_all_treasure_stops_on_error_channel():
    info_body = _treasure_info_body(5, my_open=9, boxes=[
        _box_entry(1, 0, 3), _box_entry(3, 0, 3)])

    def open_resp(_b):
        return [s2c(CMD_ERROR, codec.pb_uint(1, 4))]

    c, _ = _client({CMD_TREASURE_INFO: lambda _b: [s2c(CMD_TREASURE_INFO, info_body)],
                    CMD_TREASURE_OPEN: open_resp})
    try:
        summary = open_all_treasure(c, spacing=0)
        assert summary["opened"] == 0
        assert summary["stopped_reason"] == "error"
        assert summary["error_code"] == 4
    finally:
        c.close()


def test_list_help_roundtrip():
    body = _help_info_body(0, 1, [_help_entry(11, 555, "求助者")])
    c, fake = _client({CMD_HELP_INFO: lambda _b: [s2c(CMD_HELP_INFO, body)]})
    try:
        helps = list_help(c, help_type=0)
        assert len(helps) == 1 and helps[0].name == "求助者"
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_HELP_INFO]
        assert codec.walk_dict(sent[0]) == {1: 0}
    finally:
        c.close()


def test_help_one_sends_help_id():
    c, fake = _client({CMD_HELP: lambda _b: [s2c(CMD_HELP, codec.pb_uint(1, 3))]})
    try:
        r = help_one(c, help_id=11)
        assert r.success is True and r.new_daily_count == 3
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_HELP]
        assert codec.walk_dict(sent[0]) == {1: 11}
    finally:
        c.close()


def test_help_all_limited_by_daily_count():
    # daily_count starts at 3 (used) -> 2 helps remain of the daily allowance.
    # Provide 5 listed helps; only 2 should be sent.
    list_body = _help_info_body(0, 3, [_help_entry(i, 1, f"h{i}") for i in range(1, 6)])
    sent_ids: list[int] = []

    def help_resp(body):
        hid = codec.walk_dict(body).get(1)
        sent_ids.append(hid)
        # server reports the post-help used count climbing toward the cap (5).
        return [s2c(CMD_HELP, codec.pb_uint(1, 3 + len(sent_ids)))]

    c, _ = _client({CMD_HELP_INFO: lambda _b: [s2c(CMD_HELP_INFO, list_body)],
                    CMD_HELP: help_resp})
    try:
        summary = help_all(c, help_type=0, daily_limit=5, spacing=0)
        assert summary["helped"] == 2
        assert sent_ids == [1, 2]
        assert summary["stopped_reason"] == "daily_limit_reached"
    finally:
        c.close()


def test_help_all_stops_on_error_channel():
    list_body = _help_info_body(0, 0, [_help_entry(1, 1, "a"), _help_entry(2, 1, "b")])

    def help_resp(_b):
        return [s2c(CMD_ERROR, codec.pb_uint(1, 6))]

    c, _ = _client({CMD_HELP_INFO: lambda _b: [s2c(CMD_HELP_INFO, list_body)],
                    CMD_HELP: help_resp})
    try:
        summary = help_all(c, help_type=0, daily_limit=10, spacing=0)
        assert summary["helped"] == 0
        assert summary["stopped_reason"] == "error"
        assert summary["error_code"] == 6
    finally:
        c.close()


def test_list_help_status_roundtrip():
    body = (codec.pb_msg(1, _status_entry(1, 0, 1))
            + codec.pb_msg(1, _status_entry(2, 0, 0)))
    c, _ = _client({CMD_HELP_STATUS: lambda _b: [s2c(CMD_HELP_STATUS, body)]})
    try:
        statuses = list_help_status(c)
        assert len(statuses) == 2 and statuses[0].type == 1
    finally:
        c.close()

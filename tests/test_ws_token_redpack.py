"""Tests for ws_token.redpack — list + grab red packets over pure WS.

Schemas are the live-exported truth (docs/protocol/RED_PROTO_SCHEMA.json,
verified by a real claim on 2026-06-09):
  red_brief_list_s2c { send_list#1:p_brief_red[], grab_list#2:p_brief_red[] }
  p_brief_red        { id#1, cfg_id#2, state#3, role_id#4, name#5, expiry_time#6, min#7, max#8 }
  red_grab_c2s       { type#1:uint32, id#2:uint64 }   <- type, then id (NOT id-first)
  red_grab_s2c       { code#1, num#2, red_envelope#3:p_red }
The grab `type` = configRed_packet[cfg_id].type (bundled in ws_token/data/red_packet_types.json).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.redpack import (  # noqa: E402
    CMD_BRIEF_LIST,
    CMD_ERROR,
    CMD_GRAB,
    GrabResult,
    RedBag,
    build_grab_body,
    grab,
    grab_claimable,
    grab_type_for,
    list_red_bags,
    parse_brief_list,
    parse_grab_result,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _brief(id_, cfg_id, state, role_id, name, expiry, mn, mx):
    return (codec.pb_uint(1, id_) + codec.pb_uint(2, cfg_id) + codec.pb_uint(3, state)
            + codec.pb_uint(4, role_id) + codec.pb_str(5, name) + codec.pb_uint(6, expiry)
            + codec.pb_uint(7, mn) + codec.pb_uint(8, mx))


def _brief_list_body(grab_entries, send_entries=()):
    return (b"".join(codec.pb_msg(1, e) for e in send_entries)
            + b"".join(codec.pb_msg(2, e) for e in grab_entries))


# --- build_grab_body: type-first wire (the bug that was fixed) --------------

def test_build_grab_body_is_type_then_id():
    # red_grab_c2s {type#1, id#2}: build_grab_body(bag_id=1, type_=2) -> 08 02 (type) 10 01 (id)
    assert build_grab_body(1, 2) == bytes.fromhex("08021001")


def test_build_grab_body_real_claim_wire():
    assert build_grab_body(89616640123660, 0) == (
        codec.pb_uint(1, 0) + codec.pb_uint(2, 89616640123660))


# --- grab_type_for: cfg_id -> configRed_packet.type ------------------------

def test_grab_type_for_known_cfg_is_zero():
    assert grab_type_for(120504) == 0  # the bag actually claimed on 2026-06-09


def test_grab_type_for_loads_full_map():
    m = json.loads((ROOT / "ws_token" / "data" / "red_packet_types.json")
                   .read_text(encoding="utf-8"))
    type1_cfg = next(int(k) for k, v in m.items() if v == 1)
    assert grab_type_for(type1_cfg) == 1


def test_grab_type_for_unknown_defaults_zero():
    assert grab_type_for(999999999) == 0


# --- parse_brief_list: grab_list (field 2) ---------------------------------

def test_parse_brief_list_decodes_grab_list_only():
    body = _brief_list_body(
        grab_entries=[_brief(89616640123660, 120504, 2, 555, "全家禮服店", 1781020252, 100, 200)],
        send_entries=[_brief(1, 1, 1, 9, "me", 1, 0, 0)],  # field 1 -> ignored
    )
    bags = parse_brief_list(body)
    assert len(bags) == 1
    b = bags[0]
    assert b.bag_id == 89616640123660
    assert b.cfg_id == 120504
    assert b.state == 2
    assert b.sender_id == 555
    assert b.sender_name == "全家禮服店"
    assert b.expiry_time == 1781020252
    assert (b.min_amount, b.max_amount) == (100, 200)


def test_parse_brief_list_empty():
    assert parse_brief_list(b"") == []


# --- parse_grab_result: red_grab_s2c {code#1, num#2} vs 0x0201 -------------

def test_parse_grab_result_success_has_amount():
    body = codec.pb_uint(1, 0) + codec.pb_uint(2, 102)  # code=0, num=102
    r = parse_grab_result(CMD_GRAB, body, bag_id=89616640123660)
    assert r.success is True
    assert r.amount == 102
    assert r.error_code is None


def test_parse_grab_result_grab_nonzero_code_is_failure():
    r = parse_grab_result(CMD_GRAB, codec.pb_uint(1, 2), bag_id=1)
    assert r.success is False and r.error_code == 2 and r.amount is None


def test_parse_grab_result_error_channel():
    r = parse_grab_result(CMD_ERROR, codec.pb_uint(1, 2), bag_id=1)
    assert r.success is False and r.error_code == 2


# --- list_red_bags / grab against the fake transport -----------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_list_red_bags_roundtrip():
    body = _brief_list_body([_brief(7, 120504, 2, 9, "送禮者", 1781, 1, 9)])
    c, _ = _client({CMD_BRIEF_LIST: lambda _b: [s2c(CMD_BRIEF_LIST, body)]})
    try:
        bags = list_red_bags(c)
        assert len(bags) == 1 and bags[0].sender_name == "送禮者"
    finally:
        c.close()


def test_grab_sends_type_first_wire_and_resolves_type_from_cfg():
    ok = codec.pb_uint(1, 0) + codec.pb_uint(2, 88)  # code=0, num=88
    c, fake = _client({CMD_GRAB: lambda _b: [s2c(CMD_GRAB, ok)]})
    try:
        bag = RedBag(bag_id=89616640123660, cfg_id=120504, state=2, sender_id=9,
                     sender_name="x", expiry_time=1, min_amount=0, max_amount=0)
        r = grab(c, bag)
        assert isinstance(r, GrabResult)
        assert r.success is True and r.amount == 88
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_GRAB]
        # {type#1 = configRed_packet[120504].type = 0, id#2 = bag_id}
        assert codec.walk_dict(sent[0]) == {1: 0, 2: 89616640123660}
    finally:
        c.close()


def test_grab_error_path():
    c, _ = _client({CMD_GRAB: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 2))]})
    try:
        bag = RedBag(bag_id=42, cfg_id=120504, state=2, sender_id=9, sender_name="x",
                     expiry_time=1, min_amount=0, max_amount=0)
        r = grab(c, bag)
        assert r.success is False and r.error_code == 2
    finally:
        c.close()


def test_grab_claimable_summary_counts_only_successes():
    entries = _brief_list_body([_brief(1, 120504, 2, 9, "a", 1, 0, 0),
                                _brief(2, 120504, 2, 9, "b", 1, 0, 0)])

    def grab_resp(body):
        # id is field 2 now; bag 1 succeeds, bag 2 already-claimed (0x0201 code=2)
        if codec.walk_dict(body).get(2) == 1:
            return [s2c(CMD_GRAB, codec.pb_uint(1, 0) + codec.pb_uint(2, 50))]
        return [s2c(CMD_ERROR, codec.pb_uint(1, 2))]

    c, _ = _client({CMD_BRIEF_LIST: lambda _b: [s2c(CMD_BRIEF_LIST, entries)],
                    CMD_GRAB: grab_resp})
    try:
        summary = grab_claimable(c, spacing=0)
        assert summary["attempted"] == 2
        assert summary["claimed"] == 1
        assert len(summary["results"]) == 2
    finally:
        c.close()

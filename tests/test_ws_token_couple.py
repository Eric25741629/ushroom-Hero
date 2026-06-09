"""Tests for ws_token.couple — 比格先生 / 伴侶 (spouse-favor) over pure WS.

Field numbers are the live-recon truth (docs/protocol/MARRY_RECON.md, marry
module 59, c2s and s2c share the same cmd id):
  marry_status_c2s      15105 {}
  marry_status_s2c            { state#1, lover_id#2:uint64, ... }
  favor_friend_info_c2s 15139 {}
  favor_friend_info_s2c       { left_times#1, page#2, num#3,
                                friend_list#4:repeated p_favor_friend,
                                reward_times#5:repeated p_key_value }
  p_favor_friend { role_id#1:uint64, name#2:string, head#3, favor_lv#4:uint32,
                   favor#5:uint32, cur_power#6:uint64, gender#7, list#8 }
  favor_give_flower_c2s 15140 { friend_id#1:uint64, flower_id#2:uint64, num#3:uint64 }
  favor_give_flower_s2c       { update_info#1:p_favor_friend }
  favor_reward_info_c2s 15141 {} (or {friend_id})
  favor_reward_fetch_c2s 15142 { friend_id#1:uint64, favor_lv#2:uint32 }
  marry_ring_info_c2s   15134 {}
  marry_ring_info_s2c         { level#1, use#2, exp#3, skin_list#4, ... }
  marry_ring_levup_c2s  15135 { type#1:uint32 }
  marry_ring_levup_s2c        { exp#1, old_lev#2, new_lev#3 }

A rejected mutate (give_flower / reward_fetch / ring_levup) replies on the 0x0201
error channel (error_code 90/159/173/2/3), NOT on the action cmd — so the module
must wait for EITHER cmd, never crash. Reads use plain ``call``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.couple import (  # noqa: E402
    CMD_ERROR,
    CMD_FAVOR_INFO,
    CMD_GIVE_FLOWER,
    CMD_REWARD_FETCH,
    CMD_REWARD_INFO,
    CMD_RING_INFO,
    CMD_RING_LEVUP,
    CMD_STATUS,
    ERR_NOT_ENOUGH_ITEM,
    FLOWER,
    LOVE_STONE,
    MILK_TEA,
    Partner,
    build_give_flower_body,
    build_reward_fetch_body,
    build_ring_levup_body,
    fetch_favor_reward,
    forge_ring_until_empty,
    give_all,
    give_flower,
    parse_favor_info,
    read_partner,
    read_ring,
    ring_levup,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- wire helpers (build server bodies the parser must decode) --------------

def _favor_friend(role_id, name, favor_lv, favor):
    """One p_favor_friend {role_id#1, name#2, favor_lv#4, favor#5}."""
    return (codec.pb_uint(1, role_id) + codec.pb_str(2, name)
            + codec.pb_uint(4, favor_lv) + codec.pb_uint(5, favor))


def _favor_info_s2c(friends):
    """favor_friend_info_s2c {left_times#1, friend_list#4:repeated p_favor_friend}."""
    out = codec.pb_uint(1, 3)  # left_times#1
    for role_id, name, lv, fav in friends:
        out += codec.pb_msg(4, _favor_friend(role_id, name, lv, fav))
    return out


def _status_s2c(state, lover_id):
    """marry_status_s2c {state#1, lover_id#2}."""
    return codec.pb_uint(1, state) + codec.pb_uint(2, lover_id)


def _ring_info_s2c(level, use, exp):
    """marry_ring_info_s2c {level#1, use#2, exp#3}."""
    return codec.pb_uint(1, level) + codec.pb_uint(2, use) + codec.pb_uint(3, exp)


def _ring_levup_s2c(exp, old_lev, new_lev):
    """marry_ring_levup_s2c {exp#1, old_lev#2, new_lev#3}."""
    return codec.pb_uint(1, exp) + codec.pb_uint(2, old_lev) + codec.pb_uint(3, new_lev)


# --- cmd / goods constants --------------------------------------------------

def test_cmd_constants_match_captured_values():
    # marry module 59 cmds captured live 2026-06-09 (cmd = module*256 + N).
    assert CMD_STATUS == 15105
    assert CMD_FAVOR_INFO == 15139
    assert CMD_GIVE_FLOWER == 15140
    assert CMD_REWARD_INFO == 15141
    assert CMD_REWARD_FETCH == 15142
    assert CMD_RING_INFO == 15134
    assert CMD_RING_LEVUP == 15135
    assert CMD_ERROR == 0x0201


def test_goods_ids_match_config():
    assert FLOWER == 1031
    assert MILK_TEA == 1106
    assert LOVE_STONE == 1114


# --- read_partner: marry_status -> lover_id ---------------------------------

def test_read_partner_returns_lover_id():
    c, _ = _client({CMD_STATUS: lambda _b: [s2c(CMD_STATUS, _status_s2c(2, 89555436834913))]})
    try:
        assert read_partner(c) == 89555436834913
    finally:
        c.close()


def test_read_partner_zero_means_no_partner():
    c, _ = _client({CMD_STATUS: lambda _b: [s2c(CMD_STATUS, _status_s2c(0, 0))]})
    try:
        assert read_partner(c) == 0
    finally:
        c.close()


def test_read_partner_sends_empty_body():
    c, fake = _client({CMD_STATUS: lambda _b: [s2c(CMD_STATUS, _status_s2c(2, 7))]})
    try:
        read_partner(c)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_STATUS]
        assert sent == [b""]
    finally:
        c.close()


# --- parse_favor_info: friend_list#4 -> Partner list (first = partner) ------

def test_parse_favor_info_reads_partner_list_first_is_partner():
    body = _favor_info_s2c([(111, "Alice", 5, 320), (222, "Bob", 1, 10)])
    partners = parse_favor_info(body)
    assert partners == [
        Partner(role_id=111, name="Alice", favor_lv=5, favor=320),
        Partner(role_id=222, name="Bob", favor_lv=1, favor=10),
    ]
    assert partners[0].role_id == 111  # first = the partner (99.99% case)


def test_parse_favor_info_empty_body_is_empty_list():
    assert parse_favor_info(b"") == []


# --- build_*_body field order ------------------------------------------------

def test_build_give_flower_body_field_order():
    # favor_give_flower_c2s {friend_id#1, flower_id#2, num#3}
    body = build_give_flower_body(111, MILK_TEA, 9)
    assert body == codec.pb_uint(1, 111) + codec.pb_uint(2, MILK_TEA) + codec.pb_uint(3, 9)
    fields = codec.walk_dict(body)
    assert fields[1] == 111
    assert fields[2] == MILK_TEA
    assert fields[3] == 9


def test_build_reward_fetch_body_field_order():
    # favor_reward_fetch_c2s {friend_id#1, favor_lv#2}
    body = build_reward_fetch_body(222, 5)
    assert body == codec.pb_uint(1, 222) + codec.pb_uint(2, 5)
    fields = codec.walk_dict(body)
    assert fields[1] == 222
    assert fields[2] == 5


def test_build_ring_levup_body_field_order():
    # marry_ring_levup_c2s {type#1}
    body = build_ring_levup_body(1)
    assert body == codec.pb_uint(1, 1)
    assert codec.walk_dict(body)[1] == 1


# --- give_flower: success + 0x0201 rejection --------------------------------

def test_give_flower_success_returns_ok():
    c, fake = _client({
        CMD_GIVE_FLOWER: lambda _b: [s2c(CMD_GIVE_FLOWER,
                                         codec.pb_msg(1, _favor_friend(111, "Alice", 5, 350)))],
    })
    try:
        out = give_flower(c, friend_id=111, flower_id=MILK_TEA, num=9)
        assert out["ok"] is True
        assert out["error_code"] == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_GIVE_FLOWER]
        assert sent == [build_give_flower_body(111, MILK_TEA, 9)]
    finally:
        c.close()


def test_give_flower_rejection_on_0x0201_returns_ok_false_error_code_3():
    # num=0 / 物品不足 -> server replies 0x0201 code 3, NOT on 15140.
    c, _ = _client({CMD_GIVE_FLOWER: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 3))]})
    try:
        out = give_flower(c, friend_id=111, flower_id=MILK_TEA, num=0)
        assert out["ok"] is False
        assert out["error_code"] == ERR_NOT_ENOUGH_ITEM
        assert out["error_code"] == 3
    finally:
        c.close()


# --- give_all: one give_flower of num (server caps to inventory) ------------

def test_give_all_sends_single_give_flower_of_num():
    c, fake = _client({
        CMD_GIVE_FLOWER: lambda _b: [s2c(CMD_GIVE_FLOWER,
                                         codec.pb_msg(1, _favor_friend(111, "Alice", 5, 350)))],
    })
    try:
        out = give_all(c, friend_id=111, flower_id=FLOWER, num=20)
        assert out["ok"] is True
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_GIVE_FLOWER]
        assert sent == [build_give_flower_body(111, FLOWER, 20)]
    finally:
        c.close()


# --- fetch_favor_reward: success + rejection --------------------------------

def test_fetch_favor_reward_success():
    c, fake = _client({CMD_REWARD_FETCH: lambda _b: [s2c(CMD_REWARD_FETCH, b"")]})
    try:
        out = fetch_favor_reward(c, friend_id=222, favor_lv=5)
        assert out["ok"] is True
        assert out["error_code"] == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_REWARD_FETCH]
        assert sent == [build_reward_fetch_body(222, 5)]
    finally:
        c.close()


def test_fetch_favor_reward_rejection_on_0x0201():
    # 159 次數不足 (already claimed this week) -> 0x0201, no crash.
    c, _ = _client({CMD_REWARD_FETCH: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))]})
    try:
        out = fetch_favor_reward(c, friend_id=222, favor_lv=5)
        assert out["ok"] is False
        assert out["error_code"] == 159
    finally:
        c.close()


# --- read_ring: ring_info -> {level, exp, use} ------------------------------

def test_read_ring_parses_level_exp_use():
    c, _ = _client({CMD_RING_INFO: lambda _b: [s2c(CMD_RING_INFO, _ring_info_s2c(7, 1, 4200))]})
    try:
        out = read_ring(c)
        assert out == {"level": 7, "use": 1, "exp": 4200}
    finally:
        c.close()


# --- ring_levup: parse s2c {exp, old_lev, new_lev} + rejection --------------

def test_ring_levup_success_parses_new_lev():
    c, fake = _client({
        CMD_RING_LEVUP: lambda _b: [s2c(CMD_RING_LEVUP, _ring_levup_s2c(120, 7, 8))],
    })
    try:
        out = ring_levup(c, 1)
        assert out["ok"] is True
        assert out["error_code"] == 0
        assert out["exp"] == 120
        assert out["old_lev"] == 7
        assert out["new_lev"] == 8
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_RING_LEVUP]
        assert sent == [build_ring_levup_body(1)]
    finally:
        c.close()


def test_ring_levup_rejection_on_0x0201_code_3():
    # 真愛之石 used up -> 0x0201 code 3 物品不足, no crash.
    c, _ = _client({CMD_RING_LEVUP: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 3))]})
    try:
        out = ring_levup(c, 1)
        assert out["ok"] is False
        assert out["error_code"] == 3
    finally:
        c.close()


# --- forge_ring_until_empty: loop then STOP on 0x0201 code 3 ----------------

def test_forge_ring_until_empty_loops_then_stops_on_0x0201():
    # First N levups succeed, then 物品不足 (code 3) stops the loop.
    calls = {"n": 0}

    def _levup(_b):
        calls["n"] += 1
        if calls["n"] <= 3:
            lev = 7 + calls["n"]
            return [s2c(CMD_RING_LEVUP, _ring_levup_s2c(0, lev - 1, lev))]
        return [s2c(CMD_ERROR, codec.pb_uint(1, 3))]

    c, _ = _client({CMD_RING_LEVUP: _levup})
    try:
        out = forge_ring_until_empty(c)
        assert out["forges"] == 3
        assert out["last_new_lev"] == 10
        assert out["stopped_reason"] == "error_code=3"
    finally:
        c.close()


def test_forge_ring_until_empty_respects_max_forges():
    # Always succeeds -> the loop must stop at max_forges, not run forever.
    def _levup(_b):
        return [s2c(CMD_RING_LEVUP, _ring_levup_s2c(0, 7, 8))]

    c, fake = _client({CMD_RING_LEVUP: _levup})
    try:
        out = forge_ring_until_empty(c, max_forges=5)
        assert out["forges"] == 5
        assert out["stopped_reason"] == "max_forges"
        sent = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_RING_LEVUP]
        assert len(sent) == 5
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake

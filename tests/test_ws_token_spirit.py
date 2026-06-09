"""Tests for ws_token.spirit — 守護靈 (guardian spirit) draws over pure WS.

Field numbers are the live-exported truth (docs/protocol/SPIRIT_PROTO_SCHEMA.json,
spirit module 77, c2s and s2c share the same cmd id):
  spirit_draw_info_c2s 19743 {}            (empty)
  spirit_draw_info_s2c       { draw_list#1:repeated p_spirit_draw }
  spirit_draw_c2s      19744 { draw_id#1:uint32, count#2:uint32 }
  spirit_draw_s2c            { new_draw#1:p_spirit_draw, reward#2:repeated p_reward }
  p_spirit_draw { draw_id#1:uint32, free_times#2:uint32, must_info#3:repeated p_key_value }
  p_reward      { gtid#1:int32, num#2:int64 }
  shop_buy_c2s  6914  { shop_type#1, shop_id#2, num#3 }  (招喚貨幣 purchase)

free_times = remaining FREE draws today for that pool. A rejected draw / buy
replies on the 0x0201 error channel (error_code 90/159/173), NOT on the action
cmd — so the module must wait for EITHER cmd, never crash.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.spirit import (  # noqa: E402
    CMD_BUY_SUMMON,
    CMD_DRAW,
    CMD_DRAW_INFO,
    CMD_ERROR,
    SpiritDrawPool,
    build_buy_summon_body,
    build_draw_body,
    buy_summon_currency,
    draw,
    draw_all_free,
    parse_draw_info,
    read_draw_info,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- wire helpers (build server bodies the parser must decode) --------------

def _spirit_draw(draw_id, free_times, must=()):
    """One p_spirit_draw {draw_id#1, free_times#2, must_info#3:p_key_value[]}."""
    out = codec.pb_uint(1, draw_id) + codec.pb_uint(2, free_times)
    for k, v in must:
        out += codec.pb_msg(3, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return out


def _reward(gtid, num):
    """One p_reward {gtid#1, num#2}."""
    return codec.pb_uint(1, gtid) + codec.pb_uint(2, num)


def _draw_info_s2c(pools):
    """spirit_draw_info_s2c {draw_list#1:repeated p_spirit_draw}."""
    out = b""
    for did, free in pools:
        out += codec.pb_msg(1, _spirit_draw(did, free))
    return out


def _draw_s2c(new_draw, rewards):
    """spirit_draw_s2c {new_draw#1:p_spirit_draw, reward#2:repeated p_reward}."""
    did, free = new_draw
    out = codec.pb_msg(1, _spirit_draw(did, free))
    for gtid, num in rewards:
        out += codec.pb_msg(2, _reward(gtid, num))
    return out


# --- cmd constants ----------------------------------------------------------

def test_cmd_constants_match_captured_values():
    # spirit module 77 cmds captured live 2026-06-09 (cmd = module*256 + N).
    assert CMD_DRAW_INFO == 19743
    assert CMD_DRAW == 19744
    assert CMD_BUY_SUMMON == 6914     # shop_buy (module 27)
    assert CMD_ERROR == 0x0201


# --- build_draw_body: {draw_id#1, count#2} — draw_id FIRST ------------------

def test_build_draw_body_field_order_is_draw_id_then_count():
    # Arrange / Act
    body = build_draw_body(7, 2)
    # Assert — draw_id#1 then count#2 (NOT count-first)
    assert body == codec.pb_uint(1, 7) + codec.pb_uint(2, 2)
    fields = codec.walk_dict(body)
    assert fields[1] == 7
    assert fields[2] == 2


def test_build_buy_summon_body_field_order():
    # shop_buy_c2s {shop_type#1, shop_id#2, num#3}
    body = build_buy_summon_body(3, 4201, 10)
    assert body == codec.pb_uint(1, 3) + codec.pb_uint(2, 4201) + codec.pb_uint(3, 10)
    fields = codec.walk_dict(body)
    assert fields[1] == 3
    assert fields[2] == 4201
    assert fields[3] == 10


# --- parse_draw_info: draw_list#1 repeated p_spirit_draw --------------------

def test_parse_draw_info_reads_multiple_pools():
    # Arrange — two pools, different free_times
    body = _draw_info_s2c([(101, 2), (102, 0)])
    # Act
    pools = parse_draw_info(body)
    # Assert
    assert pools == [SpiritDrawPool(draw_id=101, free_times=2),
                     SpiritDrawPool(draw_id=102, free_times=0)]


def test_parse_draw_info_free_times_drives_has_free():
    pools = parse_draw_info(_draw_info_s2c([(5, 2), (6, 0)]))
    assert pools[0].has_free is True
    assert pools[1].has_free is False


def test_parse_draw_info_empty_body_is_empty_list():
    assert parse_draw_info(b"") == []


# --- read_draw_info sends an EMPTY body -------------------------------------

def test_read_draw_info_sends_empty_body():
    c, fake = _client({CMD_DRAW_INFO: lambda _b: [s2c(CMD_DRAW_INFO,
                                                      _draw_info_s2c([(1, 2)]))]})
    try:
        pools = read_draw_info(c)
        assert pools == [SpiritDrawPool(draw_id=1, free_times=2)]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DRAW_INFO]
        assert sent == [b""]
    finally:
        c.close()


# --- draw: success parses reward#2 -> {gtid: num} ---------------------------

def test_draw_success_parses_rewards():
    c, _ = _client({
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((101, 1), [(2001, 1), (3001, 50)]))],
    })
    try:
        out = draw(c, 101, 1)
        assert out["ok"] is True
        assert out["error_code"] == 0
        assert out["rewards"] == {2001: 1, 3001: 50}
    finally:
        c.close()


def test_draw_sums_repeated_reward_for_same_gtid():
    c, _ = _client({
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((101, 0), [(2001, 1), (2001, 2)]))],
    })
    try:
        out = draw(c, 101, 1)
        assert out["rewards"] == {2001: 3}
    finally:
        c.close()


def test_draw_rejection_on_0x0201_returns_ok_false_with_error_code():
    # Live: a rejected draw replies on 0x0201 (e.g. 173 活動已結束), NOT on 19744.
    # draw must surface ok=False + error_code and NOT crash with WSTimeoutError.
    c, _ = _client({CMD_DRAW: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]})
    try:
        out = draw(c, 101, 1)
        assert out["ok"] is False
        assert out["error_code"] == 173
        assert out["rewards"] == {}
    finally:
        c.close()


def test_draw_body_carries_draw_id_and_count():
    c, fake = _client({
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((9, 0), [(1, 1)]))],
    })
    try:
        draw(c, 9, 3)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DRAW]
        assert sent == [build_draw_body(9, 3)]
    finally:
        c.close()


# --- draw_all_free: only pools with free_times>0, aggregate -----------------

def test_draw_all_free_only_draws_pools_with_free_times():
    # pool 101 has 2 free draws, pool 102 has 0 -> only 101 is drawn (count=2)
    captured: list[bytes] = []

    def _draw_responder(b):
        captured.append(b)
        return [s2c(CMD_DRAW, _draw_s2c((101, 0), [(2001, 5)]))]

    c, _ = _client({
        CMD_DRAW_INFO: lambda _b: [s2c(CMD_DRAW_INFO, _draw_info_s2c([(101, 2), (102, 0)]))],
        CMD_DRAW: _draw_responder,
    })
    try:
        out = draw_all_free(c)
        assert out["pools_drawn"] == 1
        assert out["rewards"] == {2001: 5}
        assert len(out["results"]) == 1
        # the one draw used count == the pool's free_times (2)
        assert captured == [build_draw_body(101, 2)]
    finally:
        c.close()


def test_draw_all_free_aggregates_rewards_across_pools():
    seen: list[int] = []

    def _draw_responder(b):
        draw_id = codec.walk_dict(b).get(1)
        seen.append(draw_id)
        # different reward per pool, plus an overlapping gtid to test summing
        if draw_id == 101:
            return [s2c(CMD_DRAW, _draw_s2c((101, 0), [(2001, 1), (9000, 10)]))]
        return [s2c(CMD_DRAW, _draw_s2c((103, 0), [(2002, 2), (9000, 5)]))]

    c, _ = _client({
        CMD_DRAW_INFO: lambda _b: [s2c(CMD_DRAW_INFO,
                                       _draw_info_s2c([(101, 1), (102, 0), (103, 2)]))],
        CMD_DRAW: _draw_responder,
    })
    try:
        out = draw_all_free(c)
        assert out["pools_drawn"] == 2
        assert seen == [101, 103]            # 102 (free=0) skipped
        assert out["rewards"] == {2001: 1, 2002: 2, 9000: 15}
    finally:
        c.close()


def test_draw_all_free_skips_when_no_free_pools():
    c, fake = _client({
        CMD_DRAW_INFO: lambda _b: [s2c(CMD_DRAW_INFO, _draw_info_s2c([(101, 0), (102, 0)]))],
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((0, 0), [(1, 1)]))],
    })
    try:
        out = draw_all_free(c)
        assert out["pools_drawn"] == 0
        assert out["rewards"] == {}
        draws = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_DRAW]
        assert draws == []
    finally:
        c.close()


def test_draw_all_free_records_rejected_pool_without_crashing():
    # free=2 pool, but the draw is rejected (0x0201 code 159 次數不足) -> result
    # recorded ok=False, no rewards, no crash.
    c, _ = _client({
        CMD_DRAW_INFO: lambda _b: [s2c(CMD_DRAW_INFO, _draw_info_s2c([(101, 2)]))],
        CMD_DRAW: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))],
    })
    try:
        out = draw_all_free(c)
        assert out["pools_drawn"] == 0
        assert out["rewards"] == {}
        assert out["results"][0]["ok"] is False
        assert out["results"][0]["error_code"] == 159
    finally:
        c.close()


# --- buy_summon_currency: shop_buy 6914 + 0x0201 handling -------------------

def test_buy_summon_currency_success_returns_ok():
    c, fake = _client({CMD_BUY_SUMMON: lambda _b: [s2c(CMD_BUY_SUMMON, b"")]})
    try:
        out = buy_summon_currency(c, shop_type=3, shop_id=4201, num=10)
        assert out["ok"] is True
        assert out["error_code"] == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_BUY_SUMMON]
        assert sent == [build_buy_summon_body(3, 4201, 10)]
    finally:
        c.close()


def test_buy_summon_currency_rejection_on_0x0201():
    c, _ = _client({CMD_BUY_SUMMON: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))]})
    try:
        out = buy_summon_currency(c, shop_type=3, shop_id=4201, num=10)
        assert out["ok"] is False
        assert out["error_code"] == 159
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake

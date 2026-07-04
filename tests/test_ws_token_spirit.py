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
    CMD_INFO,
    SpiritCard,
    SpiritDrawPool,
    SpiritInventory,
    SpiritPosition,
    build_buy_summon_body,
    build_draw_body,
    buy_summon_currency,
    draw,
    draw_all_free,
    parse_draw_info,
    parse_spirit_info,
    read_draw_info,
    read_spirit_info,
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

def test_draw_all_free_draws_each_free_pool_one_pull_at_a_time():
    # pool 101 free=2 -> TWO single (count=1) pulls (server rejects count>1 with
    # 0x0201 code 2); pool 102 free=0 -> skipped.
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
        assert out["rewards"] == {2001: 10}        # 5 per pull * 2 free pulls
        assert out["results"][0]["drew"] == 2
        # two SINGLE-pull draws (count=1 each), never count>1
        assert captured == [build_draw_body(101, 1), build_draw_body(101, 1)]
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
        # 101 free=1 -> 1 pull; 103 free=2 -> 2 pulls; 102 (free=0) skipped
        assert seen == [101, 103, 103]
        assert out["rewards"] == {2001: 1, 2002: 4, 9000: 20}
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


# --- spirit_info: 守護靈倉庫 + 每位置詞條 (Task 4) --------------------------

def _kv(k, v):
    """One p_key_value {k#1, v#2}."""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _spirit_pos(pos, cur_id, cur_attrs=(), reshape_id=0, reshape_attrs=()):
    """One p_spirit_pos {pos#1, cur_id#2, cur_attr_list#3, reshape_id#4, reshape_attr_list#5}."""
    out = codec.pb_uint(1, pos) + codec.pb_uint(2, cur_id)
    for k, v in cur_attrs:
        out += codec.pb_msg(3, _kv(k, v))
    if reshape_id:
        out += codec.pb_uint(4, reshape_id)
    for k, v in reshape_attrs:
        out += codec.pb_msg(5, _kv(k, v))
    return out


def _spirit_card(id_, config_id, level, is_lock, positions=()):
    """One p_spirit_card {id#1, config_id#2, level#3, is_lock#4, pos_list#5}."""
    out = (codec.pb_uint(1, id_) + codec.pb_uint(2, config_id)
           + codec.pb_uint(3, level) + codec.pb_uint(4, is_lock))
    for p in positions:
        out += codec.pb_msg(5, p)
    return out


def _spirit_info_s2c(spirits, *, reset_times=0, reshape_times=0, tab=0):
    """spirit_info_s2c {reset_times#1, reshape_times#2, tab#3, spirit_list#5}."""
    out = (codec.pb_uint(1, reset_times) + codec.pb_uint(2, reshape_times)
           + codec.pb_uint(3, tab))
    for s in spirits:
        out += codec.pb_msg(5, s)
    return out


def test_cmd_info_constant_matches_captured_value():
    assert CMD_INFO == 19713


def test_parse_spirit_info_reads_cards_levels_locks():
    body = _spirit_info_s2c(
        [_spirit_card(8001, 30101, 5, 1),
         _spirit_card(8002, 30102, 1, 0)],
        reset_times=3, reshape_times=2, tab=1)
    inv = parse_spirit_info(body)
    assert isinstance(inv, SpiritInventory)
    assert (inv.reset_times, inv.reshape_times, inv.tab) == (3, 2, 1)
    assert len(inv.spirits) == 2
    first = inv.spirits[0]
    assert (first.id, first.config_id, first.level, first.is_lock) == (8001, 30101, 5, True)
    assert inv.spirits[1].is_lock is False


def test_parse_spirit_info_parses_per_position_affixes():
    # cur_attr_list = the 詞條 the user filters on; reshape_attr_list = 重塑 preview
    card = _spirit_card(8001, 30101, 5, 1, positions=[
        _spirit_pos(1, 501, cur_attrs=[(1001, 120), (1002, 8)],
                    reshape_id=502, reshape_attrs=[(1003, 15)]),
        _spirit_pos(2, 503, cur_attrs=[(1004, 999)]),
    ])
    inv = parse_spirit_info(_spirit_info_s2c([card]))
    sc = inv.spirits[0]
    assert len(sc.positions) == 2
    p1 = sc.positions[0]
    assert (p1.pos, p1.cur_id) == (1, 501)
    assert p1.cur_attrs == {1001: 120, 1002: 8}     # affix_id -> value
    assert p1.reshape_id == 502
    assert p1.reshape_attrs == {1003: 15}
    p2 = sc.positions[1]
    assert isinstance(p2, SpiritPosition)
    assert p2.cur_attrs == {1004: 999}
    assert p2.reshape_attrs == {}


def test_parse_spirit_info_empty_body_is_empty_inventory():
    inv = parse_spirit_info(b"")
    assert inv.spirits == ()
    assert (inv.reset_times, inv.reshape_times, inv.tab) == (0, 0, 0)


def test_read_spirit_info_sends_empty_body_and_parses():
    body = _spirit_info_s2c([_spirit_card(8001, 30101, 5, 0,
                                          positions=[_spirit_pos(1, 501,
                                                                 cur_attrs=[(1001, 7)])])])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        inv = read_spirit_info(c)
        assert len(inv.spirits) == 1
        assert inv.spirits[0].config_id == 30101
        assert inv.spirits[0].positions[0].cur_attrs == {1001: 7}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert sent == [b""]
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake

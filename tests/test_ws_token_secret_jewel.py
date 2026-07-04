"""Tests for ws_token.secret_jewel — 秘寶 (尋寶) draws + 尋寶圖 purchase over pure WS.

Field numbers are the live-exported truth (docs/protocol/SECRET_JEWEL_RECON.md,
secret_jewel module 85, c2s and s2c share the same cmd id):
  secret_jewel_info_c2s 21761 {}                  (empty)
  secret_jewel_info_s2c       { pool_list#3:repeated p_secret_jewel_pool }
  secret_jewel_draw_c2s 21764 { pool_type#1:uint32, count#2:uint32 }
  secret_jewel_draw_s2c       { pool#1:p_secret_jewel_pool, reward_list#2:repeated p_reward }
  p_secret_jewel_pool { pool_type#1, free_times#2, must_info#3:p_key_value[] }
  p_reward            { gtid#1:int32, num#2:int64 }
  shop_buy_c2s  6914 { shop_type#1=26, shop_id#2=2600001, num#3 }   (買尋寶圖 1340)
  shop_info_c2s 6913 { shop_type#1=26 }
  shop_info_s2c      { shop_type#1, buy_list#2:repeated {shop_id#1, bought#2} }

free_times = remaining FREE draws today for that pool. A rejected draw / buy
replies on 0x0201 (error_code), NOT on the action cmd — the module must wait
for EITHER cmd and never crash.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.secret_jewel import (  # noqa: E402
    CMD_DRAW,
    CMD_ERROR,
    CMD_INFO,
    CMD_SHOP_BUY,
    CMD_SHOP_INFO,
    DAILY_BUY_CAP,
    POOL_DUST,
    SHOP_ID_MAP,
    SHOP_TYPE_MAP,
    JewelPool,
    build_buy_body,
    build_draw_body,
    build_shop_info_body,
    buy_daily_maps,
    buy_one_map,
    draw,
    draw_free,
    parse_info,
    parse_shop_bought,
    read_info,
    read_shop_bought,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- wire helpers (build server bodies the parser must decode) --------------

def _pool(pool_type, free_times, must=()):
    """One p_secret_jewel_pool {pool_type#1, free_times#2, must_info#3:p_key_value[]}."""
    out = codec.pb_uint(1, pool_type) + codec.pb_uint(2, free_times)
    for k, v in must:
        out += codec.pb_msg(3, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return out


def _reward(gtid, num):
    """One p_reward {gtid#1, num#2}."""
    return codec.pb_uint(1, gtid) + codec.pb_uint(2, num)


def _info_s2c(pools):
    """secret_jewel_info_s2c {pool_list#3:repeated p_secret_jewel_pool}."""
    out = b""
    for ptype, free in pools:
        out += codec.pb_msg(3, _pool(ptype, free))
    return out


def _draw_s2c(pool, rewards):
    """secret_jewel_draw_s2c {pool#1, reward_list#2:repeated p_reward}."""
    ptype, free = pool
    out = codec.pb_msg(1, _pool(ptype, free))
    for gtid, num in rewards:
        out += codec.pb_msg(2, _reward(gtid, num))
    return out


def _shop_info_s2c(shop_type, entries):
    """shop_info_s2c {shop_type#1, buy_list#2:{shop_id#1, bought#2}[]}."""
    out = codec.pb_uint(1, shop_type)
    for shop_id, bought in entries:
        out += codec.pb_msg(2, codec.pb_uint(1, shop_id) + codec.pb_uint(2, bought))
    return out


# --- cmd constants ----------------------------------------------------------

def test_cmd_constants_match_captured_values():
    # secret_jewel module 85 cmds captured live 2026-06-27 (cmd = module*256 + N).
    assert CMD_INFO == 21761
    assert CMD_DRAW == 21764
    assert CMD_SHOP_BUY == 6914     # shop_buy (module 27)
    assert CMD_SHOP_INFO == 6913
    assert CMD_ERROR == 0x0201
    assert (POOL_DUST, SHOP_TYPE_MAP, SHOP_ID_MAP, DAILY_BUY_CAP) == (1, 26, 2600001, 10)


# --- body builders ----------------------------------------------------------

def test_build_draw_body_field_order_is_pool_type_then_count():
    body = build_draw_body(1, 1)
    assert body == codec.pb_uint(1, 1) + codec.pb_uint(2, 1)
    fields = codec.walk_dict(body)
    assert fields[1] == 1 and fields[2] == 1


def test_build_buy_body_field_order():
    body = build_buy_body(26, 2600001, 1)
    assert body == (codec.pb_uint(1, 26) + codec.pb_uint(2, 2600001)
                    + codec.pb_uint(3, 1))
    fields = codec.walk_dict(body)
    assert (fields[1], fields[2], fields[3]) == (26, 2600001, 1)


# --- parsers ----------------------------------------------------------------

def test_parse_info_reads_pool_list_field3():
    body = _info_s2c([(1, 2), (2, 0)])
    pools = parse_info(body)
    assert pools == [JewelPool(pool_type=1, free_times=2),
                     JewelPool(pool_type=2, free_times=0)]
    assert pools[0].has_free is True
    assert pools[1].has_free is False


def test_parse_info_empty_body_is_empty_list():
    assert parse_info(b"") == []


def test_parse_shop_bought_finds_target_shop_id():
    body = _shop_info_s2c(26, [(2600001, 3), (2600002, 9)])
    assert parse_shop_bought(body, 2600001) == 3
    assert parse_shop_bought(body, 2600002) == 9
    assert parse_shop_bought(body, 9999999) == 0   # absent -> 0


# --- read_info sends an EMPTY body ------------------------------------------

def test_read_info_sends_empty_body():
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_s2c([(1, 2)]))]})
    try:
        pools = read_info(c)
        assert pools == [JewelPool(pool_type=1, free_times=2)]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert sent == [b""]
    finally:
        c.close()


# --- draw -------------------------------------------------------------------

def test_draw_success_parses_rewards():
    c, _ = _client({
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((1, 1), [(1347, 5), (211002, 5)]))],
    })
    try:
        out = draw(c, 1, 1)
        assert out["ok"] is True and out["error_code"] == 0
        assert out["rewards"] == {1347: 5, 211002: 5}
    finally:
        c.close()


def test_draw_sums_repeated_reward_for_same_gtid():
    c, _ = _client({
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((1, 0), [(1347, 5), (1347, 5)]))],
    })
    try:
        assert draw(c, 1, 1)["rewards"] == {1347: 10}
    finally:
        c.close()


def test_draw_rejection_on_0x0201_returns_ok_false_with_error_code():
    c, _ = _client({CMD_DRAW: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))]})
    try:
        out = draw(c, 1, 1)
        assert out["ok"] is False and out["error_code"] == 159 and out["rewards"] == {}
    finally:
        c.close()


def test_draw_body_carries_pool_type_and_count():
    c, fake = _client({CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((1, 0), [(1, 1)]))]})
    try:
        draw(c, 1, 10)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DRAW]
        assert sent == [build_draw_body(1, 10)]
    finally:
        c.close()


# --- draw_free: only the 塵世 pool, count=1 per pull, free_times pulls --------

def test_draw_free_draws_dust_pool_one_pull_at_a_time():
    captured: list[bytes] = []

    def _draw_responder(b):
        captured.append(b)
        return [s2c(CMD_DRAW, _draw_s2c((1, 0), [(1347, 5)]))]

    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_s2c([(1, 2), (2, 2), (3, 2)]))],
        CMD_DRAW: _draw_responder,
    })
    try:
        out = draw_free(c)
        assert out["drew"] == 2
        assert out["rewards"] == {1347: 10}          # 5 per pull * 2 free
        # ONLY pool 1 (塵世) drawn — locked pools 2/3 (free=2 in info) untouched.
        assert captured == [build_draw_body(1, 1), build_draw_body(1, 1)]
    finally:
        c.close()


def test_draw_free_skips_when_dust_pool_has_no_free():
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_s2c([(1, 0), (2, 2)]))],
        CMD_DRAW: lambda _b: [s2c(CMD_DRAW, _draw_s2c((1, 0), [(1, 1)]))],
    })
    try:
        out = draw_free(c)
        assert out["drew"] == 0 and out["rewards"] == {}
        draws = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_DRAW]
        assert draws == []                            # never sent a draw
    finally:
        c.close()


def test_draw_free_stops_on_rejection_without_crashing():
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_s2c([(1, 2)]))],
        CMD_DRAW: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))],
    })
    try:
        out = draw_free(c)
        assert out["drew"] == 0 and out["ok"] is False and out["error_code"] == 159
    finally:
        c.close()


# --- buy_daily_maps: read bought -> buy remainder -> stop on 0x0201 ----------

def test_buy_daily_maps_buys_remaining_up_to_cap():
    buys: list[bytes] = []

    def _buy_responder(b):
        buys.append(b)
        return [s2c(CMD_SHOP_BUY, b"")]

    c, _ = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, _shop_info_s2c(26, [(2600001, 7)]))],
        CMD_SHOP_BUY: _buy_responder,
    })
    try:
        out = buy_daily_maps(c)               # already bought 7 today -> buy 3 more
        assert out["bought_before"] == 7 and out["bought"] == 3
        assert buys == [build_buy_body(26, 2600001, 1)] * 3
    finally:
        c.close()


def test_buy_daily_maps_already_at_cap_buys_nothing():
    c, fake = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, _shop_info_s2c(26, [(2600001, 10)]))],
        CMD_SHOP_BUY: lambda _b: [s2c(CMD_SHOP_BUY, b"")],
    })
    try:
        out = buy_daily_maps(c)
        assert out["bought"] == 0
        sent_buys = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_SHOP_BUY]
        assert sent_buys == []                 # nothing bought
    finally:
        c.close()


def test_buy_daily_maps_stops_early_on_0x0201():
    calls = {"n": 0}

    def _buy_responder(_b):
        calls["n"] += 1
        if calls["n"] >= 2:                    # 2nd buy rejected (e.g. 粉鑽不足)
            return [s2c(CMD_ERROR, codec.pb_uint(1, 159))]
        return [s2c(CMD_SHOP_BUY, b"")]

    c, _ = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, _shop_info_s2c(26, [(2600001, 0)]))],
        CMD_SHOP_BUY: _buy_responder,
    })
    try:
        out = buy_daily_maps(c)
        assert out["bought"] == 1 and out["error_code"] == 159
    finally:
        c.close()


def test_buy_daily_maps_falls_back_when_shop_info_read_fails():
    # shop_info read raises -> bought_before None -> still buys up to cap, server caps.
    calls = {"n": 0}

    def _buy_responder(_b):
        calls["n"] += 1
        if calls["n"] > 4:                     # server rejects after 4 (already had some)
            return [s2c(CMD_ERROR, codec.pb_uint(1, 159))]
        return [s2c(CMD_SHOP_BUY, b"")]

    def _shop_info_raises(_b):
        return [s2c(CMD_ERROR, codec.pb_uint(1, 1))]   # not the info cmd -> call() times out? -> use raise

    c, _ = _client({
        CMD_SHOP_BUY: _buy_responder,
        # no CMD_SHOP_INFO responder -> read_shop_bought swallows the error -> None
    })
    try:
        out = buy_daily_maps(c, timeout=0.2)
        assert out["bought_before"] is None
        assert out["bought"] == 4 and out["error_code"] == 159
    finally:
        c.close()


def test_buy_one_map_success_and_reject():
    c, fake = _client({CMD_SHOP_BUY: lambda _b: [s2c(CMD_SHOP_BUY, b"")]})
    try:
        out = buy_one_map(c)
        assert out["ok"] is True and out["error_code"] == 0
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SHOP_BUY]
        assert sent == [build_buy_body(26, 2600001, 1)]
    finally:
        c.close()

    c2, _ = _client({CMD_SHOP_BUY: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))]})
    try:
        out = buy_one_map(c2)
        assert out["ok"] is False and out["error_code"] == 159
    finally:
        c2.close()


# --- read_shop_bought read-failure returns None -----------------------------

def test_read_shop_bought_returns_none_on_read_failure():
    # No CMD_SHOP_INFO responder -> client.call times out -> swallowed -> None.
    c, _ = _client({})
    try:
        assert read_shop_bought(c, timeout=0.2) is None
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake

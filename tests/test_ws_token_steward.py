"""Tests for ws_token.steward — 家園管家採購掃蕩 over pure WS (worker_common 0x49).

Field numbers are the live-exported truth (docs/protocol/WORKER_COMMON_PROTO_SCHEMA.json,
module 73). Summary of the pinned schema:
  housekeeper_info_s2c     { buy_housekeeper_info#1 repeated p_key_value(k=service_id, v=expiry_ts) }
  buy_service_c2s          { day_num#1 uint32, id#2 uint32 }
  shopping_s2c             { shop_result#1 repeated p_housekeeper_shopping_shop_result{id#1,code#2,item_list#3[]} }
  shop_info_s2c            { shop_type#1, item_list#2 repeated p_key_value }
  set_shop_item_c2s        { shop_type#1, item_list#2 repeated p_key_value }
  set_shop_item_s2c        { result#1 }
  dungeon_setting_info_s2c { setting#1 repeated p_key_kv_list{k#1 int32, list#2 repeated p_key_value} }
  dungeon_sweep_c2s        { sweep_list#1 repeated p_housekeeper_shopping_sweep_req{id#1,level#2,times#3,use_ad#4} }
  dungeon_sweep_s2c        { result#1 repeated p_housekeeper_shopping_sweep_result{id#1,code#2,reward_list#3[]} }
  p_key_value              { k#1 int64, v#2 int64 }
Service ids (configHousekeeper): 購物管家=1, 副本管家=2.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.steward import (  # noqa: E402
    CMD_BUY_SERVICE,
    CMD_DUNGEON_SETTING_INFO,
    CMD_DUNGEON_SWEEP,
    CMD_INFO,
    CMD_SET_SHOP_ITEM,
    CMD_SHOPPING,
    SERVICE_DUNGEON,
    SERVICE_SHOPPING,
    HousekeeperInfo,
    ShoppingResult,
    SweepResult,
    build_buy_service_body,
    build_set_shop_item_body,
    build_sweep_body,
    ensure_active,
    is_active,
    read_dungeon_setting,
    read_info,
    run,
    run_dungeon_sweep,
    run_shopping,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- helpers ----------------------------------------------------------------

def _kv(k, v):
    return codec.pb_msg(1, codec.pb_uint(1, k) + codec.pb_uint(2, v))  # one p_key_value @ field 1


def _kv_at(fid, k, v):
    return codec.pb_msg(fid, codec.pb_uint(1, k) + codec.pb_uint(2, v))


def _info_body(pairs):
    """housekeeper_info_s2c {buy_housekeeper_info#1 repeated p_key_value}."""
    return b"".join(_kv_at(1, k, v) for k, v in pairs)


def _shop_result(id_, code, items=()):
    sub = codec.pb_uint(1, id_) + codec.pb_uint(2, code)
    for k, v in items:
        sub += _kv_at(3, k, v)
    return sub


def _shopping_body(results):
    return b"".join(codec.pb_msg(1, r) for r in results)


def _sweep_result(id_, code, rewards=()):
    sub = codec.pb_uint(1, id_) + codec.pb_uint(2, code)
    for k, v in rewards:
        sub += _kv_at(3, k, v)
    return sub


def _sweep_resp_body(results):
    return b"".join(codec.pb_msg(1, r) for r in results)


def _setting_kv_list(k, kvs):
    sub = codec.pb_uint(1, k)
    for kk, vv in kvs:
        sub += _kv_at(2, kk, vv)
    return sub


def _setting_body(entries):
    """dungeon_setting_info_s2c {setting#1 repeated p_key_kv_list}."""
    return b"".join(codec.pb_msg(1, e) for e in entries)


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


# --- build_buy_service_body: {day_num#1, id#2} -----------------------------

def test_build_buy_service_body_day_num_then_id():
    # buy_service_c2s {day_num#1, id#2}: day_num=30, id=1 -> 08 1e (day_num) 10 01 (id)
    assert build_buy_service_body(30, 1) == bytes.fromhex("081e1001")


def test_build_buy_service_body_decodes_back():
    body = build_buy_service_body(30, SERVICE_DUNGEON)
    assert codec.walk_dict(body) == {1: 30, 2: 2}


# --- build_set_shop_item_body: {shop_type#1, item_list#2 repeated p_key_value} ---

def test_build_set_shop_item_body_nested_repeated_kv():
    body = build_set_shop_item_body(5, [(101, 2), (102, 3)])
    fields = codec.walk(body)
    # shop_type#1 first, then two item_list#2 nested messages
    assert fields[0] == (1, 5)
    kv_entries = [v for fn, v in fields if fn == 2]
    assert len(kv_entries) == 2
    assert codec.walk_dict(bytes(kv_entries[0])) == {1: 101, 2: 2}
    assert codec.walk_dict(bytes(kv_entries[1])) == {1: 102, 2: 3}


def test_build_set_shop_item_body_empty_list():
    body = build_set_shop_item_body(3, [])
    assert codec.walk(body) == [(1, 3)]


# --- build_sweep_body: nested repeated sweep_req ---------------------------

def test_build_sweep_body_single_req_wire():
    # sweep_list#1 -> p_housekeeper_shopping_sweep_req {id#1,level#2,times#3,use_ad#4}
    body = build_sweep_body([(1, 5, 10, 0)])
    fields = codec.walk(body)
    assert len(fields) == 1 and fields[0][0] == 1
    req = bytes(fields[0][1])
    assert codec.walk_dict(req) == {1: 1, 2: 5, 3: 10, 4: 0}


def test_build_sweep_body_multiple_reqs_are_repeated_field_1():
    body = build_sweep_body([(1, 5, 10, 0), (2, 3, 4, 1)])
    fields = codec.walk(body)
    assert [fn for fn, _ in fields] == [1, 1]  # repeated field 1, twice
    assert codec.walk_dict(bytes(fields[0][1])) == {1: 1, 2: 5, 3: 10, 4: 0}
    assert codec.walk_dict(bytes(fields[1][1])) == {1: 2, 2: 3, 3: 4, 4: 1}


def test_build_sweep_body_use_ad_defaults_zero_when_triple():
    # caller may pass a 3-tuple (id, level, times); use_ad defaults 0
    body = build_sweep_body([(7, 1, 2)])
    assert codec.walk_dict(bytes(codec.walk(body)[0][1])) == {1: 7, 2: 1, 3: 2, 4: 0}


def test_build_sweep_body_empty():
    assert build_sweep_body([]) == b""


# --- read_info / is_active -------------------------------------------------

def test_read_info_decodes_repeated_key_value():
    body = _info_body([(1, 1781020000), (2, 1781030000)])
    c, _ = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        info = read_info(c)
        assert isinstance(info, HousekeeperInfo)
        assert info.expiry == {1: 1781020000, 2: 1781030000}
    finally:
        c.close()


def test_read_info_empty_is_empty_map():
    c, _ = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, b"")]})
    try:
        assert read_info(c).expiry == {}
    finally:
        c.close()


def test_is_active_true_when_expiry_after_serv_time():
    info = HousekeeperInfo(expiry={1: 2000, 2: 500})
    assert is_active(info, 1, serv_time=1000) is True
    assert is_active(info, 2, serv_time=1000) is False


def test_is_active_false_when_service_absent():
    info = HousekeeperInfo(expiry={1: 2000})
    assert is_active(info, 2, serv_time=1) is False


# --- run_shopping: parse shop_result --------------------------------------

def test_run_shopping_parses_shop_results():
    body = _shopping_body([
        _shop_result(1, 0, [(501, 2)]),
        _shop_result(2, 3, []),
    ])
    c, fake = _client({CMD_SHOPPING: lambda _b: [s2c(CMD_SHOPPING, body)]})
    try:
        result = run_shopping(c)
        assert isinstance(result, ShoppingResult)
        assert len(result.shops) == 2
        assert result.shops[0].shop_id == 1 and result.shops[0].code == 0
        assert result.shops[0].items == {501: 2}
        assert result.shops[1].shop_id == 2 and result.shops[1].code == 3
        # shopping is an empty-body request
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SHOPPING]
        assert sent == [b""]
    finally:
        c.close()


def test_run_shopping_empty_result():
    c, _ = _client({CMD_SHOPPING: lambda _b: [s2c(CMD_SHOPPING, b"")]})
    try:
        assert run_shopping(c).shops == ()
    finally:
        c.close()


# --- run_dungeon_sweep: send sweep_list, parse result ----------------------

def test_run_dungeon_sweep_sends_body_and_parses_result():
    resp = _sweep_resp_body([_sweep_result(1, 0, [(601, 5)])])
    c, fake = _client({CMD_DUNGEON_SWEEP: lambda _b: [s2c(CMD_DUNGEON_SWEEP, resp)]})
    try:
        result = run_dungeon_sweep(c, [(1, 5, 10, 0)])
        assert isinstance(result, SweepResult)
        assert len(result.results) == 1
        assert result.results[0].chapter_id == 1
        assert result.results[0].code == 0
        assert result.results[0].rewards == {601: 5}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DUNGEON_SWEEP]
        # the sent body is the sweep_list we built
        assert sent[0] == build_sweep_body([(1, 5, 10, 0)])
    finally:
        c.close()


def test_run_dungeon_sweep_empty_sweep_list_still_calls():
    c, fake = _client({CMD_DUNGEON_SWEEP: lambda _b: [s2c(CMD_DUNGEON_SWEEP, b"")]})
    try:
        result = run_dungeon_sweep(c, [])
        assert result.results == ()
        assert [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_DUNGEON_SWEEP] == [b""]
    finally:
        c.close()


# --- read_dungeon_setting --------------------------------------------------

def test_read_dungeon_setting_decodes_kv_list():
    body = _setting_body([
        _setting_kv_list(1, [(10, 100), (11, 110)]),
        _setting_kv_list(2, [(20, 200)]),
    ])
    c, _ = _client({CMD_DUNGEON_SETTING_INFO: lambda _b: [s2c(CMD_DUNGEON_SETTING_INFO, body)]})
    try:
        setting = read_dungeon_setting(c)
        assert setting == {1: {10: 100, 11: 110}, 2: {20: 200}}
    finally:
        c.close()


def test_read_dungeon_setting_empty():
    c, _ = _client({CMD_DUNGEON_SETTING_INFO: lambda _b: [s2c(CMD_DUNGEON_SETTING_INFO, b"")]})
    try:
        assert read_dungeon_setting(c) == {}
    finally:
        c.close()


# --- ensure_active ---------------------------------------------------------

def test_ensure_active_returns_true_when_already_active_no_renew():
    body = _info_body([(SERVICE_SHOPPING, 5000)])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        ok = ensure_active(c, SERVICE_SHOPPING, serv_time=1000, renew=False)
        assert ok is True
        # no buy_service was sent
        assert CMD_BUY_SERVICE not in fake.sent_cmds()
    finally:
        c.close()


def test_ensure_active_expired_no_renew_returns_false_and_skips_buy():
    body = _info_body([(SERVICE_SHOPPING, 500)])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        ok = ensure_active(c, SERVICE_SHOPPING, serv_time=1000, renew=False)
        assert ok is False
        assert CMD_BUY_SERVICE not in fake.sent_cmds()
    finally:
        c.close()


def test_ensure_active_expired_with_renew_sends_buy_then_rechecks():
    # First info read: expired (500). buy_service flips it. Second read: active.
    state = {"calls": 0}

    def info_resp(_b):
        state["calls"] += 1
        expiry = 500 if state["calls"] == 1 else 9999
        return [s2c(CMD_INFO, _info_body([(SERVICE_SHOPPING, expiry)]))]

    def buy_resp(_b):
        return [s2c(CMD_BUY_SERVICE, _info_body([(SERVICE_SHOPPING, 9999)]))]

    c, fake = _client({CMD_INFO: info_resp, CMD_BUY_SERVICE: buy_resp})
    try:
        ok = ensure_active(c, SERVICE_SHOPPING, serv_time=1000, renew=True)
        assert ok is True
        sent_buy = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_BUY_SERVICE]
        # buy_service{day_num=30, id=service} (day_num is the renewal length)
        assert codec.walk_dict(sent_buy[0]) == {1: 30, 2: SERVICE_SHOPPING}
    finally:
        c.close()


# --- run orchestrator ------------------------------------------------------

def test_run_active_shopping_does_shopping():
    info = _info_body([(SERVICE_SHOPPING, 9999)])
    shopping = _shopping_body([_shop_result(1, 0, [(501, 1)])])

    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info)],
        CMD_SHOPPING: lambda _b: [s2c(CMD_SHOPPING, shopping)],
    })
    try:
        summary = run(c, do_shopping=True, do_dungeon=False, serv_time=1000)
        assert summary["shopping_active"] is True
        assert summary["shopping"] is not None
        assert summary["shopping"].shops[0].shop_id == 1
        assert CMD_SHOPPING in fake.sent_cmds()
    finally:
        c.close()


def test_run_expired_shopping_no_renew_skips():
    info = _info_body([(SERVICE_SHOPPING, 100)])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, info)]})
    try:
        summary = run(c, do_shopping=True, do_dungeon=False, serv_time=1000)
        assert summary["shopping_active"] is False
        assert summary["shopping"] is None
        assert CMD_SHOPPING not in fake.sent_cmds()
    finally:
        c.close()


def test_run_expired_shopping_with_renew_buys_then_shops():
    state = {"calls": 0}

    def info_resp(_b):
        state["calls"] += 1
        expiry = 100 if state["calls"] == 1 else 9999
        return [s2c(CMD_INFO, _info_body([(SERVICE_SHOPPING, expiry)]))]

    shopping = _shopping_body([_shop_result(1, 0, [])])
    c, fake = _client({
        CMD_INFO: info_resp,
        CMD_BUY_SERVICE: lambda _b: [s2c(CMD_BUY_SERVICE, b"")],
        CMD_SHOPPING: lambda _b: [s2c(CMD_SHOPPING, shopping)],
    })
    try:
        summary = run(c, do_shopping=True, do_dungeon=False, renew=True, serv_time=1000)
        assert summary["shopping_active"] is True
        assert summary["shopping"] is not None
        cmds = fake.sent_cmds()
        assert CMD_BUY_SERVICE in cmds and CMD_SHOPPING in cmds
        assert cmds.index(CMD_BUY_SERVICE) < cmds.index(CMD_SHOPPING)
    finally:
        c.close()


def test_run_dungeon_active_with_sweep_list():
    info = _info_body([(SERVICE_DUNGEON, 9999)])
    sweep = _sweep_resp_body([_sweep_result(1, 0, [(601, 2)])])
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info)],
        CMD_DUNGEON_SWEEP: lambda _b: [s2c(CMD_DUNGEON_SWEEP, sweep)],
    })
    try:
        summary = run(c, do_shopping=False, do_dungeon=True,
                      sweep_list=[(1, 5, 10, 0)], serv_time=1000)
        assert summary["dungeon_active"] is True
        assert summary["sweep"] is not None
        assert summary["sweep"].results[0].chapter_id == 1
        assert CMD_DUNGEON_SWEEP in fake.sent_cmds()
    finally:
        c.close()


def test_run_dungeon_active_but_no_sweep_list_skips_sweep():
    info = _info_body([(SERVICE_DUNGEON, 9999)])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, info)]})
    try:
        summary = run(c, do_shopping=False, do_dungeon=True,
                      sweep_list=None, serv_time=1000)
        assert summary["dungeon_active"] is True
        assert summary["sweep"] is None
        assert CMD_DUNGEON_SWEEP not in fake.sent_cmds()
    finally:
        c.close()


# --- derive_sweep_list (2026-06-12 auto-derive from in-game setting) --------

def test_derive_sweep_list_keeps_enabled_levels_only():
    from ws_token.steward import derive_sweep_list
    setting = {1: {2: 1}, 7: {1: 1, 3: 0}, 12: {1: 1}}
    assert derive_sweep_list(setting) == [(1, 2, 1), (7, 1, 1), (12, 1, 1)]


def test_derive_sweep_list_empty_setting_returns_empty():
    from ws_token.steward import derive_sweep_list
    assert derive_sweep_list({}) == []

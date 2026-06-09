"""Tests for ws_token.workshop — 加工坊 (worker_processing_workshop, module 72).

Field numbers are the live-exported truth
(docs/protocol/WORKER_PROCESSING_WORKSHOP_PROTO_SCHEMA.json, module 72;
CDP fake-cnet capture 2026-06-09). c2s and s2c share the same cmd id.

  worker_pw_info_s2c        18434 { auto_use_food_list#1 repeated uint32,
                                    food_info#2 repeated p_worker }
  worker_pw_choose_food_c2s 18435 { food_list#1 p_key_value{k,v}, workshop_id#2 }
  worker_pw_cancel_work_c2s 18438 { workshop_id#1 }
  worker_pw_crops_transfer_c2s 18440 { materials#1 uint32, materials_num#2 uint32 }
  worker_pw_dining_hall_s2c 18441 { food_list#1 repeated p_key_value }
  p_worker      { team_cfg_id#1, worker_base#2, worker_status#3, auto_feed#4,
                  unlock_slot_num#5, ..., pw_worker_info#7:p_worker_pw_food_info }
  p_key_value   { k#1 int64, v#2 int64 }
Failures reply on 0x0201 error.error_info_s2c {error_code#1}.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.workshop import (  # noqa: E402
    CMD_ADD_MATERIALS,
    CMD_AUTO_ADD_MATERIALS,
    CMD_CANCEL_WORK,
    CMD_CHOOSE_FOOD,
    CMD_CROPS_AUTO_TRANSFER,
    CMD_CROPS_TRANSFER,
    CMD_DINING_HALL,
    CMD_ERROR,
    CMD_FOOD_AUTO_USE,
    CMD_INFO,
    CMD_UNLOCK_WORKSHOP,
    FOOD_CRISPY_COOKIE,
    FOOD_ELITE_PLATTER,
    RECIPE_FOOD_IDS,
    Workshop,
    WorkshopInfo,
    build_cancel_body,
    build_choose_food_body,
    build_collect_body,
    cancel_work,
    choose_food,
    collect,
    parse_dining_hall,
    parse_info,
    read_dining_hall,
    read_info,
    switch_recipe,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- wire helpers (build server bodies the parser must decode) --------------

def _kv(k, v):
    """One p_key_value {k#1 int64, v#2 int64} body."""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _kv_at(fid, k, v):
    return codec.pb_msg(fid, _kv(k, v))


def _worker(team_cfg_id, worker_status, *, auto_feed=0, unlock_slot_num=0,
            pw_info=None):
    """One p_worker {team_cfg_id#1, worker_status#3, auto_feed#4,
    unlock_slot_num#5, pw_worker_info#7}."""
    out = codec.pb_uint(1, team_cfg_id) + codec.pb_uint(3, worker_status)
    if auto_feed:
        out += codec.pb_uint(4, auto_feed)
    if unlock_slot_num:
        out += codec.pb_uint(5, unlock_slot_num)
    if pw_info is not None:
        out += codec.pb_msg(7, pw_info)
    return out


def _info_body(auto_use_food_list=(), workers=()):
    """worker_pw_info_s2c {auto_use_food_list#1 repeated uint32,
    food_info#2 repeated p_worker}."""
    out = b""
    for fid in auto_use_food_list:
        out += codec.pb_uint(1, fid)
    for w in workers:
        out += codec.pb_msg(2, w)
    return out


def _dining_body(pairs):
    """worker_pw_dining_hall_s2c {food_list#1 repeated p_key_value}."""
    return b"".join(_kv_at(1, k, v) for k, v in pairs)


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


# --- parse_info: walk food_info#2 repeated p_worker ------------------------

def test_parse_info_reads_single_workshop():
    body = _info_body(workers=[_worker(101, 1, auto_feed=1, unlock_slot_num=3)])
    info = parse_info(body)
    assert isinstance(info, WorkshopInfo)
    assert len(info.workshops) == 1
    w = info.workshops[0]
    assert isinstance(w, Workshop)
    assert w.team_cfg_id == 101
    assert w.worker_status == 1
    assert w.auto_feed == 1
    assert w.unlock_slot_num == 3


def test_parse_info_worker_status_drives_is_running():
    info = parse_info(_info_body(workers=[
        _worker(101, 0),   # idle
        _worker(202, 2),   # running
    ]))
    assert info.workshops[0].is_running is False
    assert info.workshops[1].is_running is True


def test_parse_info_multiple_workshops_in_order():
    info = parse_info(_info_body(workers=[
        _worker(1, 0), _worker(2, 1), _worker(3, 0),
    ]))
    assert [w.team_cfg_id for w in info.workshops] == [1, 2, 3]
    assert [w.is_running for w in info.workshops] == [False, True, False]


def test_parse_info_exposes_auto_use_food_list():
    info = parse_info(_info_body(auto_use_food_list=[501, 502], workers=[]))
    assert info.auto_use_food_list == (501, 502)


def test_parse_info_keeps_pw_worker_info_raw_bytes():
    pw = codec.pb_uint(1, 9001) + codec.pb_uint(2, 7)  # opaque p_worker_pw_food_info
    info = parse_info(_info_body(workers=[_worker(101, 1, pw_info=pw)]))
    assert info.workshops[0].pw_worker_info == pw


def test_parse_info_empty_is_empty():
    info = parse_info(b"")
    assert info.workshops == ()
    assert info.auto_use_food_list == ()


# --- parse_dining_hall: food_list#1 repeated p_key_value -------------------

def test_parse_dining_hall_reads_kv_pairs():
    body = _dining_body([(501, 3), (502, 10)])
    assert parse_dining_hall(body) == [(501, 3), (502, 10)]


def test_parse_dining_hall_empty():
    assert parse_dining_hall(b"") == []


# --- build_choose_food_body: {food_list#1 p_key_value, workshop_id#2} ------

def test_build_choose_food_body_wire_order():
    # food_list#1 = nested p_key_value{k#1, v#2}; workshop_id#2 after it.
    body = build_choose_food_body(501, 3, 2)
    fields = codec.walk(body)
    assert fields[0][0] == 1          # food_list#1 first
    assert fields[1] == (2, 2)        # workshop_id#2 = 2
    assert codec.walk_dict(bytes(fields[0][1])) == {1: 501, 2: 3}


def test_build_choose_food_body_matches_codec():
    body = build_choose_food_body(7, 9, 1)
    assert body == codec.pb_msg(1, _kv(7, 9)) + codec.pb_uint(2, 1)


# --- build_cancel_body: {workshop_id#1} ------------------------------------

def test_build_cancel_body_single_field():
    assert build_cancel_body(4) == codec.pb_uint(1, 4)
    assert build_cancel_body(4) == bytes.fromhex("0804")


# --- build_collect_body: crops_transfer {materials#1, materials_num#2} -----

def test_build_collect_body_wire_order():
    body = build_collect_body(6001, 50)
    assert codec.walk_dict(body) == {1: 6001, 2: 50}
    assert body == codec.pb_uint(1, 6001) + codec.pb_uint(2, 50)


# --- read_info / read_dining_hall send empty bodies ------------------------

def test_read_info_sends_empty_and_parses():
    body = _info_body(workers=[_worker(101, 1)])
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        info = read_info(c)
        assert info.workshops[0].team_cfg_id == 101
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert sent == [b""]
    finally:
        c.close()


def test_read_dining_hall_sends_empty_and_parses():
    body = _dining_body([(501, 3)])
    c, fake = _client({CMD_DINING_HALL: lambda _b: [s2c(CMD_DINING_HALL, body)]})
    try:
        foods = read_dining_hall(c)
        assert foods == [(501, 3)]
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_DINING_HALL]
        assert sent == [b""]
    finally:
        c.close()


# --- choose_food: success + 0x0201 rejection -------------------------------

def test_choose_food_success_reports_ok_and_sends_body():
    c, fake = _client({CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")]})
    try:
        result = choose_food(c, food_k=501, food_v=3, workshop_id=2)
        assert result["ok"] is True
        assert result["error_code"] is None
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_CHOOSE_FOOD]
        assert sent[0] == build_choose_food_body(501, 3, 2)
    finally:
        c.close()


def test_choose_food_0x0201_rejection_is_failure_no_crash():
    # 90 = 冷卻時間未到. The reply lands on the 0x0201 error channel, not 18435.
    c, _ = _client({CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 90))]})
    try:
        result = choose_food(c, food_k=501, food_v=3, workshop_id=2)
        assert result["ok"] is False
        assert result["error_code"] == 90
    finally:
        c.close()


# --- cancel_work: success + rejection --------------------------------------

def test_cancel_work_success():
    c, fake = _client({CMD_CANCEL_WORK: lambda _b: [s2c(CMD_CANCEL_WORK, b"")]})
    try:
        result = cancel_work(c, 4)
        assert result["ok"] is True
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_CANCEL_WORK]
        assert sent[0] == build_cancel_body(4)
    finally:
        c.close()


def test_cancel_work_0x0201_rejection_is_failure():
    c, _ = _client({CMD_CANCEL_WORK: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))]})
    try:
        result = cancel_work(c, 4)
        assert result["ok"] is False
        assert result["error_code"] == 159
    finally:
        c.close()


# --- collect (crops_transfer): success + rejection -------------------------

def test_collect_success_sends_body():
    c, fake = _client({CMD_CROPS_TRANSFER: lambda _b: [s2c(CMD_CROPS_TRANSFER, b"")]})
    try:
        result = collect(c, material_id=6001, num=50)
        assert result["ok"] is True
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_CROPS_TRANSFER]
        assert sent[0] == build_collect_body(6001, 50)
    finally:
        c.close()


def test_collect_0x0201_rejection_is_failure():
    c, _ = _client({CMD_CROPS_TRANSFER: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]})
    try:
        result = collect(c, material_id=6001, num=50)
        assert result["ok"] is False
        assert result["error_code"] == 173
    finally:
        c.close()


# --- cmd constants (18433-18445 range) -------------------------------------

def test_cmd_constants_match_recon():
    assert CMD_CROPS_AUTO_TRANSFER == 18433
    assert CMD_INFO == 18434
    assert CMD_CHOOSE_FOOD == 18435
    assert CMD_CANCEL_WORK == 18438
    assert CMD_AUTO_ADD_MATERIALS == 18439
    assert CMD_CROPS_TRANSFER == 18440
    assert CMD_DINING_HALL == 18441
    assert CMD_UNLOCK_WORKSHOP == 18443
    assert CMD_ADD_MATERIALS == 18444
    assert CMD_FOOD_AUTO_USE == 18445
    assert CMD_ERROR == 0x0201


def test_cmd_ids_match_module_72_formula():
    # cmd = module*256 + N ; module 72 -> 18432 base
    assert CMD_INFO == 72 * 256 + 2
    assert CMD_CHOOSE_FOOD == 72 * 256 + 3
    assert CMD_DINING_HALL == 72 * 256 + 9


# --- switch_recipe: 取消 BEFORE 換 (in-game rule) ---------------------------

def test_recipe_food_ids():
    assert FOOD_CRISPY_COOKIE == 8001          # 脆脆餅乾
    assert FOOD_ELITE_PLATTER == 8005          # 精英拼盤
    assert RECIPE_FOOD_IDS == (8001, 8005)


def test_switch_recipe_cancels_before_choosing():
    # The in-game rule: you MUST 取消 (cancel_work) before changing the recipe.
    # switch_recipe must send cancel FIRST, then read the dining-hall qty, then
    # choose the new food with that count.
    order: list[str] = []
    c, fake = _client({
        CMD_CANCEL_WORK: lambda _b: (order.append("cancel") or [s2c(CMD_CANCEL_WORK, b"")]),
        CMD_DINING_HALL: lambda _b: [s2c(CMD_DINING_HALL, _kv_at(1, 8005, 56))],
        CMD_CHOOSE_FOOD: lambda _b: (order.append("choose") or [s2c(CMD_CHOOSE_FOOD, b"")]),
    })
    try:
        out = switch_recipe(c, workshop_id=6001, food_id=FOOD_ELITE_PLATTER)
        assert order == ["cancel", "choose"]       # cancel FIRST, then choose
        assert out["food_id"] == 8005
        assert out["count"] == 56                    # qty read from the dining hall
        assert out["cancelled"]["ok"] is True
        assert out["chosen"]["ok"] is True
        # the choose body carried food_list{k=8005, v=56}, workshop_id=6001
        chose = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_CHOOSE_FOOD][0]
        d = codec.walk_dict(chose)
        assert codec.walk_dict(bytes(d[1])) == {1: 8005, 2: 56}
        assert d[2] == 6001
    finally:
        c.close()


def test_switch_recipe_surfaces_cancel_rejection():
    # If cancel is rejected (0x0201), switch_recipe still returns without crashing
    # and surfaces ok=False on the cancelled sub-result.
    c, _ = _client({
        CMD_CANCEL_WORK: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 90))],
        CMD_DINING_HALL: lambda _b: [s2c(CMD_DINING_HALL, _kv_at(1, 8001, 0))],
        CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")],
    })
    try:
        out = switch_recipe(c, workshop_id=6001, food_id=FOOD_CRISPY_COOKIE)
        assert out["cancelled"]["ok"] is False
        assert out["cancelled"]["error_code"] == 90
        assert out["food_id"] == 8001
    finally:
        c.close()

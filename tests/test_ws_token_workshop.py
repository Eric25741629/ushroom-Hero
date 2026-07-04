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
    RECIPE_APPROACH,
    RECIPE_FOOD_IDS,
    TEAM_TO_WORKSHOP_ID,
    Workshop,
    WorkshopInfo,
    assign_idle_workshops,
    build_cancel_body,
    build_choose_food_body,
    build_collect_body,
    cancel_work,
    choose_food,
    collect,
    parse_dining_hall,
    parse_info,
    producible_count,
    read_dining_hall,
    read_info,
    team_cfg_id_to_workshop_id,
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


def test_parse_info_selected_food_drives_is_running():
    # is_running is driven by selected_food (pw_worker_info#7.f2), NOT worker_status:
    # live 2026-06-19 worker_status reads ~602 whether idle or busy.
    idle_pw = codec.pb_uint(1, 1) + codec.pb_uint(2, 0)       # selected_food=0 idle
    busy_pw = codec.pb_uint(1, 1) + codec.pb_uint(2, 8005)    # producing 8005
    info = parse_info(_info_body(workers=[
        _worker(101, 602, pw_info=idle_pw),   # idle despite status 602
        _worker(202, 602, pw_info=busy_pw),   # running (selected_food set)
    ]))
    assert info.workshops[0].is_running is False
    assert info.workshops[1].is_running is True


def test_parse_info_multiple_workshops_in_order():
    busy_pw = codec.pb_uint(1, 1) + codec.pb_uint(2, 8001)
    info = parse_info(_info_body(workers=[
        _worker(1, 0), _worker(2, 602, pw_info=busy_pw), _worker(3, 0),
    ]))
    assert [w.team_cfg_id for w in info.workshops] == [1, 2, 3]
    assert [w.is_running for w in info.workshops] == [False, True, False]


def test_parse_info_exposes_auto_use_food_list():
    info = parse_info(_info_body(auto_use_food_list=[501, 502], workers=[]))
    assert info.auto_use_food_list == (501, 502)


def test_parse_info_keeps_pw_worker_info_raw_bytes():
    pw = codec.pb_uint(1, 9001) + codec.pb_uint(2, 8005)  # p_worker_pw_food_info
    info = parse_info(_info_body(workers=[_worker(101, 1, pw_info=pw)]))
    assert info.workshops[0].pw_worker_info == pw


# --- selected_food: pw_worker_info#7.f2 (0=idle, else=running that food) ----

def test_parse_info_reads_selected_food_from_pw_worker_info_f2():
    # pw_worker_info#7 {f1, f2=selected_food}; f2>0 means this workshop is busy
    # producing that food id (the real "is processing" signal — NOT worker_status).
    pw = codec.pb_uint(1, 1) + codec.pb_uint(2, 8005)
    info = parse_info(_info_body(workers=[_worker(6001, 602, pw_info=pw)]))
    assert info.workshops[0].selected_food == 8005


def test_parse_info_selected_food_zero_when_idle():
    pw = codec.pb_uint(1, 2) + codec.pb_uint(2, 0)
    info = parse_info(_info_body(workers=[_worker(6002, 602, pw_info=pw)]))
    assert info.workshops[0].selected_food == 0


def test_parse_info_selected_food_defaults_zero_without_pw_info():
    info = parse_info(_info_body(workers=[_worker(6002, 0)]))
    assert info.workshops[0].selected_food == 0


def test_parse_info_real_18434_capture_5554():
    # Real worker_pw_info_s2c bytes captured live on 5554 (2026-06-19): 6001 手動
    # is busy producing 8005, 6002 小隊 is idle (selected_food=0) — the bug case.
    hx = ("08c43e08c13e08c53e08c33e122f08f12e120208001800200128003a20080110c5"
          "3e1800220508852f1000220508842f1000220508832f100028003000123308f22e"
          "121b08e81610c6978480d8ae1410bbfe8480d8ae1410c6dc8480d8ae1418da0420"
          "0128033a0a08021000180028013000")
    info = parse_info(bytes.fromhex(hx))
    by_team = {w.team_cfg_id: w for w in info.workshops}
    assert by_team[6001].selected_food == 8005   # 手動加工 busy
    assert by_team[6002].selected_food == 0       # 小隊加工 idle (the stuck case)
    assert by_team[6002].worker_status == 602      # status is NOT the busy signal


# --- producible_count: ⌊stock / per_unit⌋ across all approach materials ------

def test_producible_count_exact_division():
    # 8005 needs [(6019,2),(6020,2),(6021,2)]; stock 118/118/1138 -> min(59,59,569)=59
    mats = {6019: 118, 6020: 118, 6021: 1138}
    assert producible_count(mats, 8005) == 59


def test_producible_count_floor_division():
    # 8001 needs [(6017,2)]; stock 7 -> 7//2 = 3
    assert producible_count({6017: 7}, 8001) == 3


def test_producible_count_missing_material_is_zero():
    # 6021 absent from the snapshot -> that material contributes 0 -> producible 0
    assert producible_count({6019: 118, 6020: 118}, 8005) == 0


def test_producible_count_insufficient_is_zero():
    assert producible_count({6017: 1}, 8001) == 0   # needs 2 per unit


def test_producible_count_empty_approach_is_zero():
    # 8003 活力精華 has no approach materials -> not producible via this path
    assert producible_count({6017: 999}, 8003) == 0


def test_producible_count_unknown_food_is_zero():
    assert producible_count({6017: 999}, 99999) == 0


def test_recipe_approach_matches_live_config_food():
    assert RECIPE_APPROACH[8001] == [(6017, 2)]
    assert RECIPE_APPROACH[8002] == [(6017, 1), (6019, 2)]
    assert RECIPE_APPROACH[8004] == [(6017, 1), (6019, 2), (6020, 2)]
    assert RECIPE_APPROACH[8005] == [(6019, 2), (6020, 2), (6021, 2)]


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


# --- choose_food: re-read 18434 confirms success (NOT an 18435 ack) ---------
# Live truth: choose's success ack does NOT come back on cmd 18435 (waiting for
# it times out); the only reliable signal is re-reading 18434's
# pw_worker_info#7.f2 == food_id. So choose_food fires-and-rereads.

def _info_with_selected(team_cfg_id, food_id, *, status=602):
    """worker_pw_info_s2c with one team workshop whose selected_food = food_id."""
    pw = codec.pb_uint(1, 1) + codec.pb_uint(2, food_id)
    return _info_body(workers=[_worker(team_cfg_id, status, pw_info=pw)])


def test_choose_food_success_when_reread_shows_selected_food():
    # After choose, the re-read 18434 shows workshop_id=2 (team 6002) now producing
    # 8005 -> ok=True. choose_food sends the request, then reads info to confirm.
    c, fake = _client({
        CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")],
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_with_selected(6002, 8005))],
    })
    try:
        result = choose_food(c, food_k=8005, food_v=59, workshop_id=2)
        assert result["ok"] is True
        assert result["food_id"] == 8005
        assert result["count"] == 59
        assert result["workshop_id"] == 2
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_CHOOSE_FOOD]
        assert sent[0] == build_choose_food_body(8005, 59, 2)
    finally:
        c.close()


def test_choose_food_failure_when_reread_still_idle():
    # Server rejected (selected_food still 0 after the request) -> ok=False, no crash
    # even though an 0x0201 may also have come back.
    c, _ = _client({
        CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 3))],
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_with_selected(6002, 0))],
    })
    try:
        result = choose_food(c, food_k=8005, food_v=59, workshop_id=2)
        assert result["ok"] is False
        assert result["food_id"] == 8005
    finally:
        c.close()


def test_choose_food_count_below_one_does_not_send_request():
    # count<1 must NEVER hit the server (0 triggers 0x0201 error_code=3 道具不足).
    c, fake = _client({CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")]})
    try:
        result = choose_food(c, food_k=8005, food_v=0, workshop_id=2)
        assert result["ok"] is False
        assert result["reason"] == "no_count"
        assert CMD_CHOOSE_FOOD not in fake.sent_cmds()
        assert CMD_INFO not in fake.sent_cmds()   # not even a confirmation read
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


# --- recipe ids (value-high first) -----------------------------------------

def test_recipe_food_ids():
    assert FOOD_CRISPY_COOKIE == 8001          # 脆脆餅乾
    assert FOOD_ELITE_PLATTER == 8005          # 精英拼盤
    # value-high first: 8005 精英拼盤 preferred over 8001 脆脆餅乾 when both possible
    assert RECIPE_FOOD_IDS == (8005, 8001)


# --- configWorkshop mapping: team_cfg_id -> workshop_id --------------------

def test_team_to_workshop_id_map_has_three_entries():
    # configWorkshop exported: id=1/team=6001 手動加工, id=2/team=6002 小隊加工,
    # id=3/team=6003 小隊加工
    assert TEAM_TO_WORKSHOP_ID == {6001: 1, 6002: 2, 6003: 3}


def test_team_cfg_id_to_workshop_id_converts_all_three():
    assert team_cfg_id_to_workshop_id(6001) == 1
    assert team_cfg_id_to_workshop_id(6002) == 2
    assert team_cfg_id_to_workshop_id(6003) == 3


def test_team_cfg_id_to_workshop_id_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        team_cfg_id_to_workshop_id(9999)


def test_workshop_dataclass_exposes_workshop_id_property():
    # Workshop.workshop_id is derived from team_cfg_id via TEAM_TO_WORKSHOP_ID
    w6001 = Workshop(team_cfg_id=6001, worker_status=0)
    w6002 = Workshop(team_cfg_id=6002, worker_status=1)
    w6003 = Workshop(team_cfg_id=6003, worker_status=0)
    assert w6001.workshop_id == 1
    assert w6002.workshop_id == 2
    assert w6003.workshop_id == 3


# --- assign_idle_workshops: 閒置才補、絕不動 running 工坊 --------------------
# The fixed strategy. read_info; for each team workshop {6002,6003} (NOT手動 6001):
#   selected_food != 0 -> busy, skip untouched (never cancel a running workshop).
#   selected_food == 0 -> idle, pick the first producible food from prefer_order
#   (8005 then 8001) and choose it with that producible count.

def _pw_food_info(selected_food):
    """p_worker_pw_food_info#7 {f1, f2=selected_food}."""
    return codec.pb_uint(1, 1) + codec.pb_uint(2, selected_food)


def _info_s2c_body(teams):
    """worker_pw_info_s2c, one p_worker per team.

    Each item is (team_cfg_id, selected_food); worker_status is always 602 (it is
    NOT the busy signal — selected_food is). selected_food=0 means idle.
    """
    out = b""
    for team_id, selected_food in teams:
        pw = _pw_food_info(selected_food)
        out += codec.pb_msg(2, codec.pb_uint(1, team_id) + codec.pb_uint(3, 602)
                            + codec.pb_msg(7, pw))
    return out


def _assign_client(teams, *, reread=None):
    """Fake client for assign_idle_workshops tests.

    ``teams`` = initial 18434 state (list of (team_cfg_id, selected_food)).
    ``reread`` (optional) = the 18434 state choose_food's confirmation read sees;
    defaults to the same as the initial read (so a chosen food only confirms when
    the reread reflects it). Each CMD_INFO call advances through the reread list.
    """
    info_bodies = [_info_s2c_body(teams)]
    if reread is not None:
        info_bodies += [_info_s2c_body(t) for t in reread]
    seq = {"i": 0}

    def _info(_b):
        i = min(seq["i"], len(info_bodies) - 1)
        seq["i"] += 1
        return [s2c(CMD_INFO, info_bodies[i])]

    return _client({
        CMD_INFO: _info,
        CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")],
    })


def _forbid_cancel(_body):
    raise AssertionError(
        "cancel_work must NOT be sent by assign_idle_workshops — it only assigns "
        "to idle workshops and never disturbs a running one")


def test_assign_skips_running_workshop_untouched():
    # 6002 busy producing 8005 -> assign must NOT touch it (no choose, no cancel).
    c, fake = _assign_client([(6002, 8005)])
    try:
        out = assign_idle_workshops(c, materials={6019: 999, 6020: 999, 6021: 999})
        assert CMD_CHOOSE_FOOD not in fake.sent_cmds()
        assert CMD_CANCEL_WORK not in fake.sent_cmds()
        assigned = [w for w in out["workshops"] if w.get("action") == "assigned"]
        assert assigned == []
    finally:
        c.close()


def test_assign_idle_workshop_chooses_producible_food():
    # 6002 idle; materials make 8005 producible (59) -> choose 8005 ×59 on wire id 2.
    mats = {6019: 118, 6020: 118, 6021: 1138}
    c, fake = _assign_client([(6002, 0)], reread=[[(6002, 8005)]])
    try:
        out = assign_idle_workshops(c, materials=mats)
        chosen = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                  if cmd == CMD_CHOOSE_FOOD]
        assert len(chosen) == 1
        assert codec.walk_dict(bytes(chosen[0][1])) == {1: 8005, 2: 59}
        assert chosen[0][2] == 2   # configWorkshop.id for team 6002
        assigned = [w for w in out["workshops"] if w.get("action") == "assigned"]
        assert assigned[0]["food_id"] == 8005
        assert assigned[0]["count"] == 59
        assert assigned[0]["ok"] is True
    finally:
        c.close()


def test_assign_prefers_value_high_food_first():
    # Both 8005 and 8001 producible -> 8005 (value-high, first in prefer_order) wins.
    mats = {6017: 999, 6019: 999, 6020: 999, 6021: 999}
    c, fake = _assign_client([(6002, 0)], reread=[[(6002, 8005)]])
    try:
        out = assign_idle_workshops(c, materials=mats)
        chosen = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                  if cmd == CMD_CHOOSE_FOOD]
        assert codec.walk_dict(bytes(chosen[0][1]))[1] == 8005
        assert out["workshops"][0]["food_id"] == 8005
    finally:
        c.close()


def test_assign_falls_back_to_cheaper_food_when_high_not_producible():
    # 8005 needs 6019/6020/6021 (absent) -> not producible; 8001 needs 6017 -> use it.
    mats = {6017: 10}
    c, fake = _assign_client([(6002, 0)], reread=[[(6002, 8001)]])
    try:
        out = assign_idle_workshops(c, materials=mats)
        chosen = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                  if cmd == CMD_CHOOSE_FOOD]
        assert codec.walk_dict(bytes(chosen[0][1])) == {1: 8001, 2: 5}  # 10//2
        assert out["workshops"][0]["food_id"] == 8001
    finally:
        c.close()


def test_assign_skips_idle_workshop_when_no_food_producible():
    # idle but no materials for any recipe -> nothing sent, never a count=0 request.
    c, fake = _assign_client([(6002, 0)])
    try:
        out = assign_idle_workshops(c, materials={})
        assert CMD_CHOOSE_FOOD not in fake.sent_cmds()
        skipped = [w for w in out["workshops"] if w.get("action") == "skipped"]
        assert skipped and skipped[0]["reason"] == "no_producible_food"
    finally:
        c.close()


def test_assign_ignores_manual_workshop_6001():
    # 手動加工 6001 (even when idle) is never assigned by the team pass.
    c, fake = _assign_client([(6001, 0)])
    try:
        out = assign_idle_workshops(c, materials={6017: 999})
        assert CMD_CHOOSE_FOOD not in fake.sent_cmds()
        assert all(w["team_cfg_id"] != 6001 or w["action"] == "ignored"
                   for w in out["workshops"])
    finally:
        c.close()


def test_assign_mixed_running_and_idle():
    # 6002 busy (skip), 6003 idle (assign 8005). Only one choose, never a cancel.
    mats = {6019: 999, 6020: 999, 6021: 999}
    c, fake = _assign_client([(6002, 8005), (6003, 0)], reread=[
        [(6002, 8005), (6003, 8005)]])
    try:
        assign_idle_workshops(c, materials=mats)
        assert fake.sent_cmds().count(CMD_CHOOSE_FOOD) == 1
        assert CMD_CANCEL_WORK not in fake.sent_cmds()
        chosen = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent()
                  if cmd == CMD_CHOOSE_FOOD]
        assert chosen[0][2] == 3   # only 6003 (wire id 3) got assigned
    finally:
        c.close()


def test_assign_no_team_workshops_is_noop():
    c, fake = _assign_client([(6001, 0)])
    try:
        out = assign_idle_workshops(c, materials={6017: 999})
        assert all(cmd != CMD_CHOOSE_FOOD for _s, cmd, _b in fake.framed_sent())
        assert out["workshops"][0]["team_cfg_id"] == 6001
    finally:
        c.close()

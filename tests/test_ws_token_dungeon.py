"""Tests for ws_token.dungeon — 深淵之門 (daily, type=2) + 萬神試煉 (weekly, type=23).

One pure-WS module over a single dungeon.* protocol; the two features differ only
by ``type``. Authoritative field numbers (docs/protocol/DUNGEON_PROTO_SCHEMA.json
+ TYPE_PROTO_SCHEMA.json):
  dungeon_list_s2c          { dungeon_list#1:p_dungeon[] }
  p_dungeon                 { type#1, max_level#2, cur_level#3, day_times#4 }
  dungeon_battle_start_c2s  { type#1:uint32, level#2:uint32 }
  dungeon_battle_start_s2c  { code#1, type#2, dungeon_id#3, battle_checkout#4,
                              random_seed#5:uint64, roles#6, rank_list#8 }
  dungeon_battle_result_c2s { type#1, dungeon_id#2, result#3, manual_operators#4,
                              operators#5:p_battle_operator[], args#6:p_key_value[] }
  dungeon_battle_result_s2c { code#1:int32, type#2, dungeon_id#3, result#4,
                              reward_list#5:p_reward[], ext#6 }
  dungeon_sweep_c2s         { type#1, dungeon_id#2, sweep_num#3 }
  dungeon_sweep_s2c         { dungeon_id#1, reward_list#2:p_reward[] }   (NO code field)
  p_reward                  { gtid#1:int32, num#2:int64 }
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.dungeon import (  # noqa: E402
    CMD_BATTLE_RESULT,
    CMD_BATTLE_START,
    CMD_ERROR,
    CMD_LIST,
    CMD_SWEEP,
    SEVEN_TICKET_GTID,
    TYPE_ABYSS,
    TYPE_SEVEN_TRIAL,
    BattleResult,
    Dungeon,
    SweepResult,
    build_battle_result_body,
    build_battle_start_body,
    build_sweep_body,
    list_dungeons,
    parse_battle_result,
    parse_battle_start,
    parse_dungeon_list,
    parse_sweep_result,
    run_battle,
    run_sweep,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- constants --------------------------------------------------------------

def test_type_constants_match_recon():
    assert TYPE_ABYSS == 2          # 深淵之門 (daily Coin dungeon)
    assert TYPE_SEVEN_TRIAL == 23   # 萬神試煉 (weekly Seven dungeon)
    assert SEVEN_TICKET_GTID == 1081


def test_cmd_ids_match_schema():
    assert CMD_LIST == 3585
    assert CMD_BATTLE_START == 3591
    assert CMD_BATTLE_RESULT == 3592
    assert CMD_SWEEP == 3596
    assert CMD_ERROR == 0x0201


# --- build_battle_start_body: {type#1, level#2} -----------------------------

def test_build_battle_start_body_field_order():
    # {type#1=2, level#2=5} -> 08 02 (type) 10 05 (level)
    assert build_battle_start_body(2, 5) == bytes.fromhex("08021005")


def test_build_battle_start_body_decodes_back():
    body = build_battle_start_body(TYPE_SEVEN_TRIAL, 12)
    assert codec.walk_dict(body) == {1: 23, 2: 12}


# --- build_sweep_body: {type#1, dungeon_id#2, sweep_num#3} -------------------

def test_build_sweep_body_field_order():
    # {type#1=23, dungeon_id#2=4001, sweep_num#3=3}
    body = build_sweep_body(23, 4001, 3)
    assert body == codec.pb_uint(1, 23) + codec.pb_uint(2, 4001) + codec.pb_uint(3, 3)
    assert codec.walk_dict(body) == {1: 23, 2: 4001, 3: 3}


# --- build_battle_result_body: {type,dungeon_id,result,manual_operators,...} -

def test_build_battle_result_body_defaults_are_win_no_operators():
    # result=0 (client-reported win), manual_operators=0, no operators/args
    body = build_battle_result_body(TYPE_ABYSS, 1234)
    assert codec.walk_dict(body) == {1: 2, 2: 1234, 3: 0, 4: 0}


def test_build_battle_result_body_field_order_is_type_first():
    body = build_battle_result_body(23, 99, result=0, manual_operators=0)
    # type#1, dungeon_id#2, result#3, manual_operators#4 — in that exact order
    assert body == (codec.pb_uint(1, 23) + codec.pb_uint(2, 99)
                    + codec.pb_uint(3, 0) + codec.pb_uint(4, 0))


def test_build_battle_result_body_omits_empty_operators_and_args():
    body = build_battle_result_body(2, 5, operators=[], args=[])
    assert codec.walk_dict(body) == {1: 2, 2: 5, 3: 0, 4: 0}


# --- parse_dungeon_list: repeated p_dungeon + day_times ---------------------

def _dungeon(type_, max_level, cur_level, day_times):
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, max_level)
            + codec.pb_uint(3, cur_level) + codec.pb_uint(4, day_times))


def _list_body(entries):
    return b"".join(codec.pb_msg(1, e) for e in entries)


def test_parse_dungeon_list_decodes_repeated_entries():
    body = _list_body([
        _dungeon(TYPE_ABYSS, 50, 12, 1),
        _dungeon(TYPE_SEVEN_TRIAL, 30, 8, 2),
    ])
    dungeons = parse_dungeon_list(body)
    assert len(dungeons) == 2
    abyss = dungeons[0]
    assert isinstance(abyss, Dungeon)
    assert (abyss.type, abyss.max_level, abyss.cur_level, abyss.day_times) == (2, 50, 12, 1)
    seven = dungeons[1]
    assert (seven.type, seven.day_times) == (23, 2)


def test_parse_dungeon_list_empty():
    assert parse_dungeon_list(b"") == []


def test_parse_dungeon_list_preserves_day_times_zero():
    # day_times == 0 means "no attempts left today"
    body = _list_body([_dungeon(TYPE_SEVEN_TRIAL, 10, 3, 0)])
    assert parse_dungeon_list(body)[0].day_times == 0


# --- parse_battle_start: {code#1, type#2, dungeon_id#3, random_seed#5} -------

def test_parse_battle_start_success_extracts_id_and_seed():
    body = (codec.pb_uint(1, 0) + codec.pb_uint(2, 23) + codec.pb_uint(3, 7777)
            + codec.pb_uint(4, 1) + codec.pb_uint(5, 0xDEADBEEF))
    r = parse_battle_start(CMD_BATTLE_START, body)
    assert r.success is True
    assert r.dungeon_id == 7777
    assert r.random_seed == 0xDEADBEEF
    assert r.type == 23
    assert r.error_code is None


def test_parse_battle_start_nonzero_code_is_failure():
    # code != 0 -> e.g. day_times used up / 門票不足
    body = codec.pb_uint(1, 5) + codec.pb_uint(2, 2)
    r = parse_battle_start(CMD_BATTLE_START, body)
    assert r.success is False and r.error_code == 5


def test_parse_battle_start_error_channel():
    r = parse_battle_start(CMD_ERROR, codec.pb_uint(1, 159))
    assert r.success is False and r.error_code == 159


# --- parse_battle_result: {code#1, ..., reward_list#5} ----------------------

def _reward(gtid, num):
    return codec.pb_uint(1, gtid) + codec.pb_uint(2, num)


def test_parse_battle_result_success_collects_rewards():
    body = (codec.pb_uint(1, 0) + codec.pb_uint(2, 23) + codec.pb_uint(3, 7777)
            + codec.pb_uint(4, 0)
            + codec.pb_msg(5, _reward(1001, 500))
            + codec.pb_msg(5, _reward(1081, 1)))
    r = parse_battle_result(CMD_BATTLE_RESULT, body)
    assert isinstance(r, BattleResult)
    assert r.success is True
    assert r.rewards == {1001: 500, 1081: 1}
    assert r.dungeon_id == 7777


def test_parse_battle_result_nonzero_code_is_failure():
    # anti-cheat / checkCheat forced loss -> code != 0
    body = codec.pb_uint(1, 1) + codec.pb_uint(2, 2)
    r = parse_battle_result(CMD_BATTLE_RESULT, body)
    assert r.success is False and r.error_code == 1 and r.rewards == {}


def test_parse_battle_result_error_channel():
    r = parse_battle_result(CMD_ERROR, codec.pb_uint(1, 3))
    assert r.success is False and r.error_code == 3


# --- parse_sweep_result: {dungeon_id#1, reward_list#2} (NO code) ------------

def test_parse_sweep_result_collects_rewards():
    body = (codec.pb_uint(1, 4001)
            + codec.pb_msg(2, _reward(1001, 1500))
            + codec.pb_msg(2, _reward(2002, 30)))
    r = parse_sweep_result(CMD_SWEEP, body)
    assert isinstance(r, SweepResult)
    assert r.success is True
    assert r.dungeon_id == 4001
    assert r.rewards == {1001: 1500, 2002: 30}


def test_parse_sweep_result_error_channel_is_failure():
    # over-sweep / 門票不足 -> 0x0201 error
    r = parse_sweep_result(CMD_ERROR, codec.pb_uint(1, 160))
    assert r.success is False and r.error_code == 160 and r.rewards == {}


# --- orchestrators against the fake transport -------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_list_dungeons_roundtrip():
    body = _list_body([_dungeon(TYPE_ABYSS, 50, 12, 1)])
    c, _ = _client({CMD_LIST: lambda _b: [s2c(CMD_LIST, body)]})
    try:
        dungeons = list_dungeons(c)
        assert len(dungeons) == 1 and dungeons[0].type == TYPE_ABYSS
    finally:
        c.close()


def test_run_sweep_sends_type_dungeon_num_and_returns_rewards():
    sweep_body = (codec.pb_uint(1, 4001) + codec.pb_msg(2, _reward(1001, 900)))
    c, fake = _client({CMD_SWEEP: lambda _b: [s2c(CMD_SWEEP, sweep_body)]})
    try:
        r = run_sweep(c, type=TYPE_SEVEN_TRIAL, dungeon_id=4001, sweep_num=2)
        assert r.success is True and r.rewards == {1001: 900}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SWEEP]
        assert codec.walk_dict(sent[0]) == {1: 23, 2: 4001, 3: 2}
    finally:
        c.close()


def test_run_sweep_error_is_failure():
    c, _ = _client({CMD_SWEEP: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 160))]})
    try:
        r = run_sweep(c, type=TYPE_SEVEN_TRIAL, dungeon_id=4001, sweep_num=99)
        assert r.success is False and r.error_code == 160
    finally:
        c.close()


def test_run_battle_start_then_result_returns_rewards():
    start_body = (codec.pb_uint(1, 0) + codec.pb_uint(2, 2) + codec.pb_uint(3, 8888)
                  + codec.pb_uint(5, 0xABCDEF))
    result_body = (codec.pb_uint(1, 0) + codec.pb_uint(3, 8888)
                   + codec.pb_msg(5, _reward(1001, 250)))

    c, fake = _client({
        CMD_BATTLE_START: lambda _b: [s2c(CMD_BATTLE_START, start_body)],
        CMD_BATTLE_RESULT: lambda _b: [s2c(CMD_BATTLE_RESULT, result_body)],
    })
    try:
        outcome = run_battle(c, type=TYPE_ABYSS, level=5)
        assert outcome.success is True
        assert outcome.dungeon_id == 8888
        assert outcome.rewards == {1001: 250}
        # the c2s result body must report result=0 and reference the started dungeon_id
        sent_result = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_BATTLE_RESULT]
        rf = codec.walk_dict(sent_result[0])
        assert rf[1] == TYPE_ABYSS and rf[2] == 8888 and rf[3] == 0 and rf[4] == 0
        # and the c2s start body carried {type, level}
        sent_start = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_BATTLE_START]
        assert codec.walk_dict(sent_start[0]) == {1: TYPE_ABYSS, 2: 5}
    finally:
        c.close()


def test_run_battle_aborts_when_start_fails():
    # start replies code!=0 (門票不足 / day_times used) -> never send result
    c, fake = _client({
        CMD_BATTLE_START: lambda _b: [s2c(CMD_BATTLE_START, codec.pb_uint(1, 5))],
    })
    try:
        outcome = run_battle(c, type=TYPE_SEVEN_TRIAL, level=1)
        assert outcome.success is False and outcome.error_code == 5
        assert CMD_BATTLE_RESULT not in fake.sent_cmds()
    finally:
        c.close()


def test_run_battle_result_rejected_by_anticheat_is_failure():
    # start ok, but server rejects client-reported result (checkCheat forced loss)
    start_body = (codec.pb_uint(1, 0) + codec.pb_uint(2, 23) + codec.pb_uint(3, 5555)
                  + codec.pb_uint(5, 42))
    c, _ = _client({
        CMD_BATTLE_START: lambda _b: [s2c(CMD_BATTLE_START, start_body)],
        CMD_BATTLE_RESULT: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 7))],
    })
    try:
        outcome = run_battle(c, type=TYPE_SEVEN_TRIAL, level=8)
        assert outcome.success is False and outcome.error_code == 7
    finally:
        c.close()

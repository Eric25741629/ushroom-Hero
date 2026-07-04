"""Tests for ws_token.tycoon — 傳奇大亨 (大富翁 monopoly board) over pure WS.

Field numbers are the LIVE-recon truth (emulator-5556, 2026-06-14; `act` module
24, c2s and s2c share the same cmd id):

  act.act_monopoly_info_c2s   6312 (0x18a8) { act_type#1 }
  act.act_monopoly_info_s2c        (0x18a8) { act_type#1, circle#2, pos#3,
        dice_time#4, double_card_open#5, event#6, grid_list#7:p_monopoly_grid[],
        reward_circle_list#8:uint32[] }
  act.act_monopoly_dice_c2s   6313 (0x18a9) { act_type#1 }   <- empty roll
  act.act_monopoly_dice_s2c        (0x18a9) { act_type#1, dice_num#2, circle#3,
        pos#4, event#5, reward#6:p_monopoly_reward[] }       <- SERVER rolls
  p_monopoly_grid   { grid_id#1, cfg_id#2, type#3 }
  p_monopoly_reward { type#1, item#2:p_key_value{k#1,v#2}, ...#3 }

VERDICT: server-authoritative. roll_dice sends the empty {act_type} and reads the
point/pos/reward from the same-cmd reply; the landed reward is auto-granted (no
claim cmd). A rejected roll (out of dice) replies on the 0x0201 error channel, so
roll_dice waits for EITHER cmd, never crashes, and auto_play stops on the first
0x0201. Reads (read_board) use plain ``call``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.tycoon import (  # noqa: E402
    ACT_TYPE,
    CMD_DICE,
    CMD_DICE_TIME,
    CMD_ERROR,
    CMD_INFO,
    DiceResult,
    Grid,
    MonopolyBoard,
    auto_play,
    build_act_type_body,
    parse_board,
    parse_dice_result,
    read_board,
    roll_dice,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- wire helpers (build server bodies the parser must decode) --------------

def _grid(grid_id, cfg_id, type_=1):
    """One p_monopoly_grid {grid_id#1, cfg_id#2, type#3}."""
    return codec.pb_uint(1, grid_id) + codec.pb_uint(2, cfg_id) + codec.pb_uint(3, type_)


def _info_s2c(act_type, circle, pos, dice_time, double_card, grids,
              reward_circle=()):
    """act_monopoly_info_s2c {act_type#1, circle#2, pos#3, dice_time#4,
    double_card_open#5, grid_list#7:[], reward_circle_list#8:[]}."""
    out = (codec.pb_uint(1, act_type) + codec.pb_uint(2, circle)
           + codec.pb_uint(3, pos) + codec.pb_uint(4, dice_time)
           + codec.pb_uint(5, double_card))
    for gid, cfg, t in grids:
        out += codec.pb_msg(7, _grid(gid, cfg, t))
    for v in reward_circle:
        out += codec.pb_uint(8, v)
    return out


def _reward(type_, item_id, count):
    """One p_monopoly_reward {type#1, item#2:p_key_value{k#1,v#2}}."""
    kv = codec.pb_uint(1, item_id) + codec.pb_uint(2, count)
    return codec.pb_uint(1, type_) + codec.pb_msg(2, kv)


def _dice_s2c(act_type, dice_num, circle, pos, rewards=()):
    """act_monopoly_dice_s2c {act_type#1, dice_num#2, circle#3, pos#4,
    reward#6:p_monopoly_reward[]}."""
    out = (codec.pb_uint(1, act_type) + codec.pb_uint(2, dice_num)
           + codec.pb_uint(3, circle) + codec.pb_uint(4, pos))
    for t, iid, cnt in rewards:
        out += codec.pb_msg(6, _reward(t, iid, cnt))
    return out


def _error_s2c(code):
    """error_info_s2c {error_code#1}."""
    return codec.pb_uint(1, code)


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


# --- cmd / act_type constants -----------------------------------------------

def test_cmd_constants_match_captured_values():
    # act module 24 cmds captured live 2026-06-14 (cmd = module*256 + N).
    assert CMD_INFO == 0x18A8 == 6312
    assert CMD_DICE == 0x18A9 == 6313
    assert CMD_DICE_TIME == 0x18AA == 6314
    assert CMD_ERROR == 0x0201
    assert CMD_INFO // 256 == 24  # `act` module


def test_act_type_is_4003():
    # banner icon_4003 + c2s body f1 == 4003 (LIVE-verified).
    assert ACT_TYPE == 4003


# --- build body: both info_c2s and dice_c2s are {act_type#1} ----------------

def test_build_act_type_body_default():
    body = build_act_type_body()
    assert body == codec.pb_uint(1, 4003)
    assert codec.walk_dict(body)[1] == 4003


def test_build_act_type_body_explicit():
    assert codec.walk_dict(build_act_type_body(9999))[1] == 9999


# --- parse_board ------------------------------------------------------------

def test_parse_board_reads_pos_circle_and_grids():
    body = _info_s2c(4003, circle=2, pos=7, dice_time=1781389562, double_card=1,
                     grids=[(1, 101, 1), (2, 102, 1), (3, 103, 1)],
                     reward_circle=[5, 10])
    board = parse_board(body)
    assert board.act_type == 4003
    assert board.circle == 2
    assert board.pos == 7
    assert board.dice_time == 1781389562
    assert board.double_card_open == 1
    assert board.grids == [
        Grid(grid_id=1, cfg_id=101, type=1),
        Grid(grid_id=2, cfg_id=102, type=1),
        Grid(grid_id=3, cfg_id=103, type=1),
    ]
    assert board.reward_circle_list == [5, 10]


def test_parse_board_live_capture_20_grids():
    # The real board on 5556 had 20 tiles {grid_id, cfg_id=grid_id, type=1},
    # pos=1, circle=0.
    grids = [(i, i, 1) for i in range(1, 21)]
    body = _info_s2c(4003, circle=0, pos=1, dice_time=0, double_card=0, grids=grids)
    board = parse_board(body)
    assert len(board.grids) == 20
    assert board.pos == 1
    assert board.circle == 0


def test_parse_board_empty_body_is_zeroed():
    board = parse_board(b"")
    assert board.pos == 0
    assert board.grids == []


# --- parse_dice_result ------------------------------------------------------

def test_parse_dice_result_reads_point_pos_and_reward():
    # LIVE roll #1: dice_num=6, pos=7, reward item 1401 x20.
    body = _dice_s2c(4003, dice_num=6, circle=0, pos=7, rewards=[(4, 1401, 20)])
    r = parse_dice_result(CMD_DICE, body)
    assert r.ok is True
    assert r.dice_num == 6
    assert r.pos == 7
    assert r.circle == 0
    assert len(r.rewards) == 1
    assert r.rewards[0].item_id == 1401
    assert r.rewards[0].count == 20
    assert r.rewards[0].type == 4


def test_parse_dice_result_error_channel_is_not_ok():
    r = parse_dice_result(CMD_ERROR, _error_s2c(173))
    assert r.ok is False
    assert r.error_code == 173
    assert r.dice_num == 0


# --- read_board: plain call, sends {act_type} -------------------------------

def test_read_board_sends_act_type_and_parses():
    grids = [(i, i, 1) for i in range(1, 21)]
    c, fake = _client({CMD_INFO: lambda _b: [
        s2c(CMD_INFO, _info_s2c(4003, 0, 1, 0, 0, grids))]})
    try:
        board = read_board(c)
        assert isinstance(board, MonopolyBoard)
        assert board.pos == 1
        assert len(board.grids) == 20
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert sent == [codec.pb_uint(1, 4003)]
    finally:
        c.close()


# --- roll_dice: success + 0x0201 rejection ----------------------------------

def test_roll_dice_success_returns_point_and_reward():
    c, fake = _client({CMD_DICE: lambda _b: [
        s2c(CMD_DICE, _dice_s2c(4003, dice_num=3, circle=0, pos=10,
                                rewards=[(4, 1401, 20)]))]})
    try:
        r = roll_dice(c)
        assert r.ok is True
        assert r.dice_num == 3
        assert r.pos == 10
        assert r.rewards[0].item_id == 1401
        # the c2s body is just {act_type}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_DICE]
        assert sent == [codec.pb_uint(1, 4003)]
    finally:
        c.close()


def test_roll_dice_rejection_on_0x0201_is_not_ok():
    c, _ = _client({CMD_DICE: lambda _b: [s2c(CMD_ERROR, _error_s2c(159))]})
    try:
        r = roll_dice(c)
        assert r.ok is False
        assert r.error_code == 159
        assert r.response_cmd == CMD_ERROR
    finally:
        c.close()


# --- auto_play: bounded loop, terminates on 0x0201 and on max_rolls ---------

def _counting_dice_responder(success_rolls, error_code=173):
    """Replies dice_s2c for the first ``success_rolls`` rolls (point cycles
    1..6, reward 1401 x20 each), then 0x0201 ``error_code`` forever after."""
    state = {"n": 0}

    def _r(_b):
        state["n"] += 1
        if state["n"] <= success_rolls:
            pt = ((state["n"] - 1) % 6) + 1
            return [s2c(CMD_DICE, _dice_s2c(4003, dice_num=pt, circle=0,
                                            pos=state["n"], rewards=[(4, 1401, 20)]))]
        return [s2c(CMD_ERROR, _error_s2c(error_code))]
    return _r


def test_auto_play_stops_on_server_rejection():
    c, fake = _client({CMD_DICE: _counting_dice_responder(success_rolls=3)})
    try:
        out = auto_play(c, max_rolls=50, spacing=0)
        assert out["rolls"] == 3
        assert out["stopped_reason"] == "error_code=173"
        # 3 successful rolls each granted 1401 x20 -> 60 total.
        assert out["total_rewards"] == {1401: 60}
        assert out["last_pos"] == 3
        # 4 dice sends total: 3 ok + 1 that got rejected.
        dice_sends = [c for c in fake.sent_cmds() if c == CMD_DICE]
        assert len(dice_sends) == 4
    finally:
        c.close()


def test_auto_play_respects_max_rolls_cap():
    # Server never rejects; the cap must stop the loop.
    c, fake = _client({CMD_DICE: _counting_dice_responder(success_rolls=999)})
    try:
        out = auto_play(c, max_rolls=5, spacing=0)
        assert out["rolls"] == 5
        assert out["stopped_reason"] == "max_rolls"
        dice_sends = [c for c in fake.sent_cmds() if c == CMD_DICE]
        assert len(dice_sends) == 5  # exactly max_rolls, no extra probe
    finally:
        c.close()


def test_auto_play_zero_rolls_when_first_is_rejected():
    c, _ = _client({CMD_DICE: lambda _b: [s2c(CMD_ERROR, _error_s2c(173))]})
    try:
        out = auto_play(c, max_rolls=10, spacing=0)
        assert out["rolls"] == 0
        assert out["total_rewards"] == {}
        assert out["stopped_reason"] == "error_code=173"
    finally:
        c.close()

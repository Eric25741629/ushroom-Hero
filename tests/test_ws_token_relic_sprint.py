"""Tests for ws_token.relic_sprint + relic.spend_to_target — 遺物碎片衝刺.

Schemas are the recon truth (docs/protocol/RELIC_SPRINT_RECON.md):

  act2 module 25 (0x19); cmd = module*256 + sub:
    act_cross_limited_rank_info        0x19AC  c2s {act_type#1}
        -> s2c {act_type#1, group_id#2, task_list#3: p_cross_limited_rank_task[]}
    act_cross_limited_rank_task_reward 0x19AF  c2s {act_type#1, small_group_id#2}
        -> s2c (success) OR 0x0201 error
  p_cross_limited_rank_task {task_id#1, status#2, count#3:uint64}
     status: 0 Normal / 1 CanGet / 2 HadGet

  relic module 17 (0x11): relic_up 0x1103 {relic_uid#1} -> {p_relic#1} | 0x0201

The fragment-spend gate in spend_to_target reads the live count from the
InventoryTracker (0x0402 push); the fakes below mutate a tiny tracker's ``counts``
on each relic_up to emulate the server's consume push deterministically.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token import relic  # noqa: E402
from ws_token import relic_sprint  # noqa: E402
from ws_token.relic_sprint import (  # noqa: E402
    ACT_TYPES,
    CMD_ERROR,
    CMD_SPRINT_INFO,
    CMD_SPRINT_REWARD,
    SPRINT_TOTAL,
    Sprint,
    SprintTask,
    build_info_body,
    build_reward_body,
    claim_round,
    find_active_act_type,
    parse_claim,
    parse_sprint,
    read_sprint,
    run_relic_sprint,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)

CMD_RELIC_INFO = relic.CMD_RELIC_INFO
CMD_RELIC_UP = relic.CMD_RELIC_UP
FRAG = relic.RELIC_FRAGMENT_ITEM


# --- wire body builders -----------------------------------------------------


def _p_relic(uid, cfg_id, quality, location, lv):
    return (codec.pb_uint(1, uid) + codec.pb_uint(2, cfg_id)
            + codec.pb_uint(3, quality) + codec.pb_uint(4, location)
            + codec.pb_uint(5, lv))


def _relic_info_body(relics):
    return b"".join(codec.pb_msg(1, _p_relic(*r)) for r in relics)


def _relic_up_body(uid, cfg_id, quality, location, lv):
    return codec.pb_msg(1, _p_relic(uid, cfg_id, quality, location, lv))


def _p_task(task_id, status, count):
    return (codec.pb_uint(1, task_id) + codec.pb_uint(2, status)
            + codec.pb_uint(3, count))


def _sprint_body(act_type, group_id, tasks):
    out = codec.pb_uint(1, act_type) + codec.pb_uint(2, group_id)
    for t in tasks:
        out += codec.pb_msg(3, _p_task(*t))
    return out


# --- fakes ------------------------------------------------------------------


class _Tracker:
    """Minimal InventoryTracker stand-in: just the ``counts`` dict spend reads."""

    def __init__(self, counts=None):
        self.counts = dict(counts or {})


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


# ===========================================================================
# parse_sprint
# ===========================================================================


def test_parse_sprint_decodes_repeated_tasks():
    # Arrange — 4 rounds, round1 CanGet, accrued count 300000
    body = _sprint_body(269, 7, [
        (1001, 1, 300_000),
        (1002, 0, 300_000),
        (1003, 0, 0),
        (1004, 0, 0),
    ])
    # Act
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    # Assert
    assert isinstance(sprint, Sprint) and sprint.open
    assert sprint.act_type == 269 and sprint.group_id == 7
    assert sprint.tasks[0] == SprintTask(task_id=1001, status=1, count=300_000)
    assert sprint.accrued == 300_000
    assert sprint.claimable_rounds == (1,)


def test_parse_sprint_empty_task_list_is_closed():
    # Arrange — act_type echoed but NO task_list (wrong rotation)
    body = codec.pb_uint(1, 13)
    # Act
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    # Assert
    assert sprint.open is False and sprint.tasks == ()


def test_parse_sprint_error_frame_is_closed():
    # Arrange
    body = codec.pb_uint(1, 173)
    # Act
    sprint = parse_sprint(CMD_ERROR, body)
    # Assert
    assert sprint.open is False and sprint.error_code == 173


def test_parse_sprint_claimable_rounds_multiple():
    # Arrange — rounds 1 & 3 CanGet, round 2 already HadGet
    body = _sprint_body(269, 7, [
        (1001, 1, 700_000),
        (1002, 2, 700_000),
        (1003, 1, 700_000),
        (1004, 0, 700_000),
    ])
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    assert sprint.claimable_rounds == (1, 3)
    assert sprint.accrued == 700_000


# ===========================================================================
# read_sprint
# ===========================================================================


def test_read_sprint_sends_act_type_and_parses():
    body = _sprint_body(269, 7, [(1001, 1, 225_000), (1002, 0, 225_000)])
    c, fake = _client({CMD_SPRINT_INFO: lambda _b: [s2c(CMD_SPRINT_INFO, body)]})
    try:
        out = read_sprint(c, 269)
        assert out["open"] and out["act_type"] == 269
        assert out["accrued"] == 225_000 and out["claimable_rounds"] == [1]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_INFO]
        assert codec.walk_dict(sent[0]) == {1: 269}
    finally:
        c.close()


def test_read_sprint_closed_when_no_tasks():
    body = codec.pb_uint(1, 13)  # echoed type, no task_list
    c, _ = _client({CMD_SPRINT_INFO: lambda _b: [s2c(CMD_SPRINT_INFO, body)]})
    try:
        out = read_sprint(c, 13)
        assert out["open"] is False and out["act_type"] == 13
    finally:
        c.close()


# ===========================================================================
# find_active_act_type — 13 closed, 269 open -> 269
# ===========================================================================


def test_find_active_act_type_picks_open_rotation():
    open_body = _sprint_body(269, 7, [(1001, 0, 0)])

    def _info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type == 269:
            return [s2c(CMD_SPRINT_INFO, open_body)]
        # 13 is closed: echo type only, no task_list
        return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]

    c, fake = _client({CMD_SPRINT_INFO: _info})
    try:
        assert find_active_act_type(c) == 269
        # probed 13 first (closed) then 269 (open) — both queried
        probed = [codec.walk_dict(b).get(1)
                  for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_INFO]
        assert probed == list(ACT_TYPES)
    finally:
        c.close()


def test_find_active_act_type_none_when_all_closed():
    c, _ = _client(
        {CMD_SPRINT_INFO: lambda b: [s2c(CMD_SPRINT_INFO,
                                         codec.pb_uint(1, codec.walk_dict(b).get(1)))]})
    try:
        assert find_active_act_type(c) is None
    finally:
        c.close()


def test_find_active_act_type_treats_timeout_as_closed(monkeypatch):
    """一個 act_type 探測逾時不該中斷整個 sweep — 視為關閉,繼續探下一個。"""
    from ws_token.client import WSTimeoutError

    probed = []

    def _fake_read(_client, act_type, **_kw):
        probed.append(act_type)
        if act_type == ACT_TYPES[0]:
            raise WSTimeoutError("no reply for closed act_type")
        return {"open": True, "act_type": act_type}

    monkeypatch.setattr(relic_sprint, "read_sprint", _fake_read)
    c, _ = _client({})
    try:
        assert find_active_act_type(c) == ACT_TYPES[1]
        # 第一個逾時被吞掉,仍探了第二個 act_type。
        assert probed == list(ACT_TYPES)
    finally:
        c.close()


def test_find_active_act_type_wserror_does_not_abort_sweep(monkeypatch):
    """非逾時的 WSError 也視為該 act_type 關閉,不上拋中斷探測;全錯則回 None。"""
    from ws_token.client import WSError

    probed = []

    def _fake_read(_client, act_type, **_kw):
        probed.append(act_type)
        raise WSError(f"act_type {act_type} closed")

    monkeypatch.setattr(relic_sprint, "read_sprint", _fake_read)
    c, _ = _client({})
    try:
        assert find_active_act_type(c) is None
        assert probed == list(ACT_TYPES)  # 兩個都探過,沒有中途中斷
    finally:
        c.close()


# ===========================================================================
# claim_round — success / 0x0201
# ===========================================================================


def test_claim_round_success():
    c, fake = _client(
        {CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")]})
    try:
        res = claim_round(c, 269, 2)
        assert res.success and res.small_group_id == 2
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 269, 2: 2}
    finally:
        c.close()


def test_claim_round_error_is_failure():
    c, _ = _client(
        {CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]})
    try:
        res = claim_round(c, 269, 1)
        assert res.success is False and res.error_code == 173 and res.rejected
    finally:
        c.close()


def test_build_reward_body_two_fields():
    assert codec.walk_dict(build_reward_body(13, 4)) == {1: 13, 2: 4}


def test_parse_claim_unexpected_cmd_is_failure():
    res = parse_claim(0x9999, b"", 3)
    assert res.success is False and res.small_group_id == 3


# ===========================================================================
# relic.spend_to_target
# ===========================================================================


def _spend_client(tracker, *, cost_per_step, relics, up_error_after=None):
    """A client whose relic_up consumes ``cost_per_step`` fragments per step.

    The responder mutates ``tracker.counts[FRAG]`` to emulate the 0x0402 consume
    push, and echoes the upgraded p_relic (lv+1). ``up_error_after`` makes the
    Nth (1-based) upgrade reply 0x0201 (e.g. out of fragments).
    """
    state = {"levels": {uid: lv for uid, _cfg, _q, _loc, lv in relics},
             "ups": 0}
    info_body = _relic_info_body(relics)
    cfg_of = {uid: (cfg, q, loc) for uid, cfg, q, loc, _lv in relics}

    def _info(_b):
        return [s2c(CMD_RELIC_INFO, info_body)]

    def _up(body):
        uid = codec.walk_dict(body).get(1)
        state["ups"] += 1
        if up_error_after is not None and state["ups"] >= up_error_after:
            return [s2c(CMD_ERROR, codec.pb_uint(1, 25))]
        state["levels"][uid] += 1
        cfg, q, loc = cfg_of[uid]
        tracker.counts[FRAG] = tracker.counts.get(FRAG, 0) - cost_per_step
        return [s2c(CMD_RELIC_UP,
                    _relic_up_body(uid, cfg, q, loc, state["levels"][uid]))]

    c, fake = _client({CMD_RELIC_INFO: _info, CMD_RELIC_UP: _up})
    return c, fake, state


def test_spend_to_target_stops_at_target():
    # Arrange — 100/step, target 250 -> needs 3 steps (overshoots to 300)
    tracker = _Tracker({FRAG: 1_000})
    c, fake, _ = _spend_client(
        tracker, cost_per_step=100,
        relics=[(1, 4001, 1, 1, 10), (2, 4002, 1, 2, 12)])
    try:
        out = relic.spend_to_target(c, tracker, target_spend=250)
        assert out["stopped"] == "target_reached"
        assert out["upgraded"] == 3 and out["spent"] == 300
        assert out["fragments_remaining"] == 700
        assert out["frag_unknown"] is False
    finally:
        c.close()


def test_spend_to_target_picks_lowest_level_first():
    # Arrange — uid1 starts lower; balanced strategy = lowest level each step
    tracker = _Tracker({FRAG: 10_000})
    c, fake, _ = _spend_client(
        tracker, cost_per_step=100,
        relics=[(1, 4001, 1, 1, 10), (2, 4002, 1, 2, 12)])
    try:
        relic.spend_to_target(c, tracker, target_spend=400)  # 4 steps
        ups = [codec.walk_dict(b).get(1)
               for _sid, cmd, b in fake.framed_sent() if cmd == CMD_RELIC_UP]
        # 10,12 -> up1(11) -> up1(12) -> tie, order picks 1 (13) -> tie picks 1? no
        #   after up1 twice: (12,12) tie -> uid1 (read order) -> (13,12) -> uid2
        assert ups == [1, 1, 1, 2]
    finally:
        c.close()


def test_spend_to_target_stops_on_server_error():
    # Arrange — 2nd upgrade is rejected (insufficient fragments)
    tracker = _Tracker({FRAG: 1_000})
    c, _, _ = _spend_client(
        tracker, cost_per_step=100,
        relics=[(1, 4001, 1, 1, 10)], up_error_after=2)
    try:
        out = relic.spend_to_target(c, tracker, target_spend=10_000)
        assert out["stopped"] == "error_code=25"
        assert out["upgraded"] == 1 and out["spent"] == 100
    finally:
        c.close()


def test_spend_to_target_frag_unknown_falls_back_to_max_steps():
    # Arrange — tracker has NO fragment count (login snapshot lacked 100022)
    tracker = _Tracker({})  # no FRAG key
    c, _, _ = _spend_client(
        tracker, cost_per_step=100,
        relics=[(1, 4001, 1, 1, 10)])
    try:
        out = relic.spend_to_target(
            c, tracker, target_spend=999_999, max_steps=3)
        assert out["frag_unknown"] is True
        assert out["stopped"] == "max_steps"
        assert out["upgraded"] == 3
        # spend cannot be measured -> best-effort 0
        assert out["spent"] == 0
    finally:
        c.close()


def test_spend_to_target_zero_target_is_noop():
    tracker = _Tracker({FRAG: 500})
    c, fake, _ = _spend_client(
        tracker, cost_per_step=100, relics=[(1, 4001, 1, 1, 10)])
    try:
        out = relic.spend_to_target(c, tracker, target_spend=0)
        assert out["stopped"] == "target<=0" and out["upgraded"] == 0
        assert not [c2 for _s, c2, _b in fake.framed_sent() if c2 == CMD_RELIC_UP]
    finally:
        c.close()


def test_spend_to_target_no_equipped_relic():
    # Arrange — only unequipped relics (location 0)
    tracker = _Tracker({FRAG: 500})
    c, _, _ = _spend_client(
        tracker, cost_per_step=100, relics=[(1, 4001, 1, 0, 10)])
    try:
        out = relic.spend_to_target(c, tracker, target_spend=200)
        assert out["stopped"] == "no equipped relic" and out["upgraded"] == 0
    finally:
        c.close()


# ===========================================================================
# run_relic_sprint
# ===========================================================================


def test_run_relic_sprint_disabled_skips():
    tracker = _Tracker({FRAG: 1_000})
    c, fake = _client({})
    try:
        out = run_relic_sprint(c, tracker, enabled=False)
        assert out == {"skipped": "disabled"}
        assert fake.sent_cmds() == [257]  # only login, no sprint packets
    finally:
        c.close()


def test_run_relic_sprint_no_active_skips():
    tracker = _Tracker({FRAG: 1_000})
    # both act_types closed
    c, _ = _client(
        {CMD_SPRINT_INFO: lambda b: [s2c(CMD_SPRINT_INFO,
                                         codec.pb_uint(1, codec.walk_dict(b).get(1)))]})
    try:
        out = run_relic_sprint(c, tracker, enabled=True)
        assert out == {"skipped": "no active sprint"}
    finally:
        c.close()


def test_run_relic_sprint_already_met_only_claims():
    # Arrange — accrued already at SPRINT_TOTAL; round1 CanGet, others HadGet.
    #   remaining target = 0 -> NO relic upgrades, just claim the CanGet round.
    tracker = _Tracker({FRAG: 1_000})
    full = _sprint_body(269, 7, [
        (1001, 1, SPRINT_TOTAL),  # CanGet
        (1002, 2, SPRINT_TOTAL),  # HadGet
        (1003, 2, SPRINT_TOTAL),
        (1004, 2, SPRINT_TOTAL),
    ])

    def _info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type == 269:
            return [s2c(CMD_SPRINT_INFO, full)]
        return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]

    c, fake = _client({
        CMD_SPRINT_INFO: _info,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True)
        assert out["act_type"] == 269
        assert out["claimed_rounds"] == [1]
        # crucially: NO relic_up sent (target already met)
        assert CMD_RELIC_UP not in fake.sent_cmds()
        assert out["spent"] == 0
    finally:
        c.close()


def test_run_relic_sprint_full_flow_spend_then_claim():
    # Arrange — start accrued 0; after spending 200 fragments, round1 (225K? no,
    #   we use a small target via SPRINT default? keep target tiny via accrued
    #   accounting). Simplest deterministic flow: target 200, 100/step (2 ups),
    #   then re-read shows round1 CanGet -> claim it.
    tracker = _Tracker({FRAG: 1_000})
    relics = [(1, 4001, 1, 1, 10), (2, 4002, 1, 2, 10)]
    info_body = _relic_info_body(relics)
    levels = {1: 10, 2: 10}
    phase = {"reads": 0, "ups": 0}

    before_body = _sprint_body(269, 7, [
        (1001, 0, 0), (1002, 0, 0), (1003, 0, 0), (1004, 0, 0)])
    after_body = _sprint_body(269, 7, [
        (1001, 1, 200), (1002, 0, 200), (1003, 0, 200), (1004, 0, 200)])

    def _sprint_info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type != 269:
            return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]
        phase["reads"] += 1
        # read#1 = find_active probe, read#2 = sprint_before, read#3 = sprint_after
        if phase["reads"] >= 3:
            return [s2c(CMD_SPRINT_INFO, after_body)]
        return [s2c(CMD_SPRINT_INFO, before_body)]

    def _relic_info(_b):
        return [s2c(CMD_RELIC_INFO, info_body)]

    def _relic_up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        phase["ups"] += 1
        tracker.counts[FRAG] -= 100
        return [s2c(CMD_RELIC_UP, _relic_up_body(uid, 4001, 1, 1, levels[uid]))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: _relic_info,
        CMD_RELIC_UP: _relic_up,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True, target_spend=200)
        assert out["act_type"] == 269
        assert out["spent"] == 200          # 2 upgrades * 100
        assert out["claimed_rounds"] == [1]  # round1 became CanGet
        assert phase["ups"] == 2
        # reward c2s carried {act_type, small_group_id}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 269, 2: 1}
    finally:
        c.close()

"""Tests for ws_token.relic_sprint + relic.spend_to_target — 遺物碎片衝刺.

Model is the LIVE recon truth (docs/protocol/RELIC_SPRINT_RECON.md), captured
2026-06-19 on 5556 (CDP 9223, act_type=269, group_id=2698):

  act2 module 25 (0x19); cmd = module*256 + sub:
    act_cross_limited_rank_info        0x19AC  c2s {act_type#1}
        -> s2c {act_type#1, group_id#2, task_list#3: p_cross_limited_rank_task[]}
    act_cross_limited_rank_task_reward 0x19AF  c2s {act_type#1, small_group_id#2}
        -> s2c (success) OR 0x0201 error
  p_cross_limited_rank_task {task_id#1, status#2, count#3:uint64}
     status: 0 Normal / 1 CanGet / 2 HadGet

  32 tasks = 28 non-stage milestones (is_stage=0; count = cumulative 遺物碎片 spent,
  shared across all 28) + 4 STAGE round tasks (is_stage=1; count = sub-tasks done
  0..7, claimable when count==7). The 4 stage tasks are the highest task_ids,
  small_group_id = ascending ordinal 1..4. accrued = the cumulative spent (max
  non-stage count); claimable_rounds = CanGet stage small_group_ids.

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
    CMD_SPRINT_CALENDAR,
    CMD_SPRINT_INFO,
    CMD_SPRINT_REWARD,
    CalWindow,
    active_window,
    parse_calendar,
    MAX_SPRINT_UPGRADES,
    NUM_ROUNDS,
    ROUND_THRESHOLDS,
    SPRINT_TOTAL,
    SUBTASKS_PER_ROUND,
    Sprint,
    SprintRound,
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

# ---------------------------------------------------------------------------
# LIVE capture (5556, 2026-06-19): 6572 reply for act_type=269 after two relic
# level-ups (cumulative spent 265870). Round 1 (small_group_id=1) is CanGet
# (stage task 269129 count==7); round 2 at 2/7; rounds 3/4 untouched.
# Used to prove parse_sprint decodes the real 32-task wire bytes correctly.
# ---------------------------------------------------------------------------
# f1=act 269, f2=group 2698, then 28 milestone tasks (count 265870, early ones
# CanGet) and 4 stage tasks (269129 status1 count7; 269130 c2; 269131/2 c0).
LIVE_6572_HEX = "088d02108a151a0a08adb610100118ead2071a0a08aeb610100118ead2071a0a08afb610100118ead2071a0a08b0b610100118ead2071a0a08b1b6101001188e9d101a0a08b2b6101001188e9d101a0a08b3b6101001188e9d101a0a08b4b6101001188e9d101a0a08b5b6101001188e9d101a0a08b6b6101000188e9d101a0a08b7b6101000188e9d101a0a08b8b6101000188e9d101a0a08b9b6101000188e9d101a0a08bab6101000188e9d101a0a08bbb6101000188e9d101a0a08bcb6101000188e9d101a0a08bdb6101000188e9d101a0a08beb6101000188e9d101a0a08bfb6101000188e9d101a0a08c0b6101000188e9d101a0a08c1b6101000188e9d101a0a08c2b6101000188e9d101a0a08c3b6101000188e9d101a0a08c4b6101000188e9d101a0a08c5b6101000188e9d101a0a08c6b6101000188e9d101a0a08c7b6101000188e9d101a0a08c8b6101000188e9d101a0808c9b610100118071a0808cab610100018021a0808cbb610100018001a0808ccb61010001800"  # noqa: E501
LIVE_6572_BODY = bytes.fromhex(LIVE_6572_HEX)


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


def _full_sprint_tasks(cumulative, *, round_status):
    """Build the canonical 32-task list (28 milestones + 4 stage rounds).

    ``cumulative`` = shared fragments-spent count on every milestone; each
    milestone's status is CanGet iff cumulative >= its threshold. ``round_status``
    = list of 4 status ints for the stage tasks; their count = sub-tasks done.
    Returns ``[(task_id, status, count), ...]`` in wire order.
    """
    base = 269101
    # per-round cumulative thresholds for the 7 sub-tasks (matches LIVE config)
    sub_thresholds = [
        [15_000, 30_000, 60_000, 90_000, 135_000, 180_000, 225_000],
        [240_000, 255_000, 285_000, 315_000, 360_000, 405_000, 450_000],
        [465_000, 480_000, 510_000, 540_000, 585_000, 630_000, 675_000],
        [690_000, 705_000, 735_000, 765_000, 810_000, 855_000, 900_000],
    ]
    tasks = []
    tid = base
    for rnd in range(NUM_ROUNDS):
        for thr in sub_thresholds[rnd]:
            st = 1 if cumulative >= thr else 0
            tasks.append((tid, st, cumulative))
            tid += 1
    # 4 stage tasks; count = how many of the round's sub-tasks are crossed
    for rnd in range(NUM_ROUNDS):
        done = sum(1 for thr in sub_thresholds[rnd] if cumulative >= thr)
        tasks.append((base + 28 + rnd, round_status[rnd], done))
    return tasks


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
# parse_sprint — against the REAL live bytes
# ===========================================================================


def test_parse_sprint_decodes_live_32_task_capture():
    # Act — parse the exact 6572 reply captured live (act 269, 2 upgrades).
    sprint = parse_sprint(CMD_SPRINT_INFO, LIVE_6572_BODY)
    # Assert structure
    assert sprint.open
    assert sprint.act_type == 269 and sprint.group_id == 2698
    assert len(sprint.tasks) == 32
    # accrued = cumulative 遺物碎片 spent (NOT max-of-all; stage counts excluded)
    assert sprint.accrued == 265_870
    # 4 stage rounds derived, small_group_id 1..4 by ascending task_id
    assert len(sprint.rounds) == NUM_ROUNDS
    assert [r.task_id for r in sprint.rounds] == [269129, 269130, 269131, 269132]
    assert [r.small_group_id for r in sprint.rounds] == [1, 2, 3, 4]
    # round1 fully done (7/7) -> CanGet; round2 2/7; rounds 3/4 untouched
    assert sprint.rounds[0] == SprintRound(
        small_group_id=1, task_id=269129, status=1, done_subtasks=7)
    assert sprint.rounds[1].done_subtasks == 2 and sprint.rounds[1].status == 0
    assert sprint.rounds[2].done_subtasks == 0
    assert sprint.claimable_rounds == (1,)


def test_parse_sprint_milestone_count_frozen_at_crossing():
    # LIVE quirk: a milestone's count FREEZES at the cumulative-spend value when
    # it crossed (status->CanGet); still-Normal milestones keep the LIVE cumulative.
    #   269101 crossed in upgrade#1 -> frozen at 125290.
    #   269110 still Normal         -> live cumulative 265870.
    # So accrued (= max non-stage count) still equals the true live spend 265870.
    sprint = parse_sprint(CMD_SPRINT_INFO, LIVE_6572_BODY)
    t0 = next(t for t in sprint.tasks if t.task_id == 269101)
    assert t0.status == 1 and t0.count == 125_290  # frozen at crossing
    t_live = next(t for t in sprint.tasks if t.task_id == 269110)
    assert t_live.status == 0 and t_live.count == 265_870  # live cumulative
    assert sprint.accrued == 265_870
    # round cumulative thresholds (last sub-task per round) are the documented set
    assert ROUND_THRESHOLDS == (225_000, 450_000, 675_000, 900_000)
    assert SPRINT_TOTAL == ROUND_THRESHOLDS[-1]


def test_parse_sprint_zero_progress_synthetic():
    # Fresh account: cumulative 0, no rounds claimable.
    body = _sprint_body(269, 2698, _full_sprint_tasks(0, round_status=[0, 0, 0, 0]))
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    assert sprint.open and len(sprint.tasks) == 32
    assert sprint.accrued == 0
    assert sprint.claimable_rounds == ()
    assert all(r.done_subtasks == 0 for r in sprint.rounds)


def test_parse_sprint_multiple_claimable_rounds():
    # Synthetic: cumulative crosses round 1 & 2; both stage tasks CanGet, one HadGet.
    body = _sprint_body(269, 2698,
                        _full_sprint_tasks(700_000, round_status=[2, 1, 1, 0]))
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    # round1 HadGet (2) -> not claimable; rounds 2 & 3 CanGet (1)
    assert sprint.claimable_rounds == (2, 3)
    assert sprint.accrued == 700_000


def test_parse_sprint_empty_task_list_is_closed():
    body = codec.pb_uint(1, 13)  # echoed type, no task_list
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    assert sprint.open is False and sprint.tasks == () and sprint.rounds == ()


def test_parse_sprint_error_frame_is_closed():
    sprint = parse_sprint(CMD_ERROR, codec.pb_uint(1, 173))
    assert sprint.open is False and sprint.error_code == 173


def test_derive_rounds_returns_empty_on_unexpected_layout():
    # Wrong number of tasks (framework change) -> no rounds guessed.
    body = _sprint_body(269, 2698, [(269101, 0, 0), (269102, 0, 0)])
    sprint = parse_sprint(CMD_SPRINT_INFO, body)
    assert sprint.rounds == ()
    # accrued falls back to max count across all tasks when rounds unknown
    assert sprint.accrued == 0


# ===========================================================================
# read_sprint
# ===========================================================================


def test_read_sprint_sends_act_type_and_parses_live_bytes():
    c, fake = _client(
        {CMD_SPRINT_INFO: lambda _b: [s2c(CMD_SPRINT_INFO, LIVE_6572_BODY)]})
    try:
        out = read_sprint(c, 269)
        assert out["open"] and out["act_type"] == 269 and out["group_id"] == 2698
        assert out["accrued"] == 265_870
        assert out["claimable_rounds"] == [1]
        assert len(out["rounds"]) == NUM_ROUNDS
        assert out["rounds"][0]["small_group_id"] == 1
        assert out["rounds"][0]["done_subtasks"] == 7
        assert len(out["tasks"]) == 32
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_INFO]
        assert codec.walk_dict(sent[0]) == {1: 269}
    finally:
        c.close()


def test_read_sprint_closed_when_no_tasks():
    body = codec.pb_uint(1, 13)
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
    open_body = _sprint_body(269, 2698, _full_sprint_tasks(0, round_status=[0] * 4))

    def _info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type == 269:
            return [s2c(CMD_SPRINT_INFO, open_body)]
        return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]

    c, fake = _client({CMD_SPRINT_INFO: _info})
    try:
        assert find_active_act_type(c) == 269
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
        assert probed == list(ACT_TYPES)
    finally:
        c.close()


# ===========================================================================
# claim_round — success / 0x0201 ; small_group_id derived, never hard-coded
# ===========================================================================


def test_claim_round_success_uses_small_group_id():
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
    """A client whose relic_up consumes ``cost_per_step`` fragments per step."""
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
    tracker = _Tracker({FRAG: 10_000})
    c, fake, _ = _spend_client(
        tracker, cost_per_step=100,
        relics=[(1, 4001, 1, 1, 10), (2, 4002, 1, 2, 12)])
    try:
        relic.spend_to_target(c, tracker, target_spend=400)
        ups = [codec.walk_dict(b).get(1)
               for _sid, cmd, b in fake.framed_sent() if cmd == CMD_RELIC_UP]
        assert ups == [1, 1, 1, 2]
    finally:
        c.close()


def test_spend_to_target_stops_on_server_error():
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
    c, _ = _client(
        {CMD_SPRINT_INFO: lambda b: [s2c(CMD_SPRINT_INFO,
                                         codec.pb_uint(1, codec.walk_dict(b).get(1)))]})
    try:
        out = run_relic_sprint(c, tracker, enabled=True)
        assert out == {"skipped": "no active sprint"}
    finally:
        c.close()


def test_run_relic_sprint_already_met_only_claims():
    # Arrange — cumulative already at SPRINT_TOTAL; round1 CanGet, others HadGet.
    #   remaining target = 0 -> NO relic upgrades, just claim the CanGet round.
    tracker = _Tracker({FRAG: 1_000})
    full = _sprint_body(269, 2698,
                        _full_sprint_tasks(SPRINT_TOTAL, round_status=[1, 2, 2, 2]))

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
        # claim c2s carried the round's small_group_id (=1), not hard-coded index
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 269, 2: 1}
    finally:
        c.close()


def test_run_relic_sprint_full_flow_spend_then_claim():
    # Arrange — start cumulative 0; spend 225000 (=> round 1 threshold), 75000/step
    #   (3 ups), then re-read shows round1 (small_group_id=1) CanGet -> claim it.
    # The sprint reply is driven by the SERVER-side accrued counter (mirrors the
    # real game folding each relic_up consumption into the sprint count), so the
    # accrued-driven should_stop in run_relic_sprint stops at exactly the target.
    tracker = _Tracker({FRAG: 1_000_000})
    relics = [(1, 4001, 1, 1, 10), (2, 4002, 1, 2, 10)]
    info_body = _relic_info_body(relics)
    levels = {1: 10, 2: 10}
    server = {"accrued": 0}

    def _sprint_info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type != 269:
            return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]
        # round1 CanGet once cumulative crosses its 225K threshold
        rs = [1 if server["accrued"] >= ROUND_THRESHOLDS[0] else 0, 0, 0, 0]
        body_out = _sprint_body(269, 2698,
                                _full_sprint_tasks(server["accrued"], round_status=rs))
        return [s2c(CMD_SPRINT_INFO, body_out)]

    def _relic_info(_b):
        return [s2c(CMD_RELIC_INFO, info_body)]

    def _relic_up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        tracker.counts[FRAG] -= 75_000
        server["accrued"] += 75_000  # server folds the spend into the sprint count
        return [s2c(CMD_RELIC_UP, _relic_up_body(uid, 4001, 1, 1, levels[uid]))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: _relic_info,
        CMD_RELIC_UP: _relic_up,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True, target_spend=225_000)
        assert out["act_type"] == 269
        assert out["spent"] == 225_000          # 3 upgrades * 75000
        assert out["claimed_rounds"] == [1]      # round1 became CanGet
        ups = [c2 for _s, c2, _b in fake.framed_sent() if c2 == CMD_RELIC_UP]
        assert len(ups) == 3                     # stopped exactly at target, no overshoot
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPRINT_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 269, 2: 1}
    finally:
        c.close()


def test_run_relic_sprint_resumes_from_partial_accrued():
    # Arrange — already spent 200000 (below round1 225K). target=225000 so only
    #   25000 remaining; one 25000-step crosses round1 -> CanGet -> claim.
    # Server-side accrued counter starts at 200000 and the relic_up folds the spend
    # in, so the accrued-driven should_stop halts after exactly one step.
    tracker = _Tracker({FRAG: 1_000_000})
    relics = [(1, 4001, 1, 1, 10)]
    info_body = _relic_info_body(relics)
    levels = {1: 10}
    server = {"accrued": 200_000}

    def _sprint_info(body):
        if codec.walk_dict(body).get(1) != 269:
            return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]
        rs = [1 if server["accrued"] >= ROUND_THRESHOLDS[0] else 0, 0, 0, 0]
        return [s2c(CMD_SPRINT_INFO,
                    _sprint_body(269, 2698,
                                 _full_sprint_tasks(server["accrued"], round_status=rs)))]

    def _relic_up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        tracker.counts[FRAG] -= 25_000
        server["accrued"] += 25_000
        return [s2c(CMD_RELIC_UP, _relic_up_body(uid, 4001, 1, 1, levels[uid]))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: lambda _b: [s2c(CMD_RELIC_INFO, info_body)],
        CMD_RELIC_UP: _relic_up,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True, target_spend=225_000)
        # only the 25000 deficit was spent (resumed from accrued=200000)
        assert out["spent"] == 25_000
        ups = [c2 for _s, c2, _b in fake.framed_sent() if c2 == CMD_RELIC_UP]
        assert len(ups) == 1
        assert out["claimed_rounds"] == [1]
    finally:
        c.close()


def test_run_relic_sprint_frag_unknown_stops_at_accrued_no_overspend():
    """CRITICAL: 碎片現量未知（tracker 沒 100022）時，仍須用 server accrued 停在
    target，不可超量耗光碎片。

    這是 codex 找到的 CRITICAL：0x0402 升級回覆常不帶 100022 -> tracker delta 永遠
    測不到 -> 舊版 spent 卡 0 -> 一路升到 max_steps(100000) 耗光碎片。新版以 server
    accrued 驅動 should_stop：accrued 隨每次升級遞增，到 >= target 立即停。
    """
    tracker = _Tracker({})  # NO FRAG key -> frag_unknown (the dangerous case)
    relics = [(1, 4001, 1, 1, 10)]
    info_body = _relic_info_body(relics)
    levels = {1: 10}
    server = {"accrued": 0}
    step = 50_000  # 4 steps reach 200K target exactly

    def _sprint_info(body):
        if codec.walk_dict(body).get(1) != 269:
            return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]
        return [s2c(CMD_SPRINT_INFO,
                    _sprint_body(269, 2698,
                                 _full_sprint_tasks(server["accrued"],
                                                    round_status=[0, 0, 0, 0])))]

    def _relic_up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        server["accrued"] += step  # server folds spend in; tracker still blind
        return [s2c(CMD_RELIC_UP, _relic_up_body(uid, 4001, 1, 1, levels[uid]))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: lambda _b: [s2c(CMD_RELIC_INFO, info_body)],
        CMD_RELIC_UP: _relic_up,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True, target_spend=200_000)
        assert out["frag_unknown"] is True
        ups = [c2 for _s, c2, _b in fake.framed_sent() if c2 == CMD_RELIC_UP]
        # 4 steps reach exactly 200K then stop — NOT runaway to max_steps.
        assert len(ups) == 4
        assert len(ups) <= MAX_SPRINT_UPGRADES        # bounded even when blind
        assert out["spend_stopped"] == "target_reached"
    finally:
        c.close()


def test_run_relic_sprint_hard_cap_bounds_runaway_when_accrued_stuck():
    """CRITICAL backstop: 即使 server accrued 讀取異常（永遠卡 0，should_stop 永不
    為真）且 frag_unknown，升級次數也絕不超過 MAX_SPRINT_UPGRADES。"""
    tracker = _Tracker({})  # frag_unknown
    relics = [(1, 4001, 1, 1, 10)]
    info_body = _relic_info_body(relics)
    levels = {1: 10}

    def _sprint_info(body):
        if codec.walk_dict(body).get(1) != 269:
            return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]
        # accrued NEVER advances (simulates a broken/stale read) -> should_stop
        # can never fire; only the hard cap can stop the loop.
        return [s2c(CMD_SPRINT_INFO,
                    _sprint_body(269, 2698,
                                 _full_sprint_tasks(0, round_status=[0, 0, 0, 0])))]

    def _relic_up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        return [s2c(CMD_RELIC_UP, _relic_up_body(uid, 4001, 1, 1, levels[uid]))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: lambda _b: [s2c(CMD_RELIC_INFO, info_body)],
        CMD_RELIC_UP: _relic_up,
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True, target_spend=SPRINT_TOTAL)
        ups = [c2 for _s, c2, _b in fake.framed_sent() if c2 == CMD_RELIC_UP]
        # The fail-safe: never more than the hard cap, even with a broken accrued.
        assert len(ups) == MAX_SPRINT_UPGRADES
        assert out["spend_stopped"] == "max_steps"
    finally:
        c.close()


def test_run_relic_sprint_protocol_mismatch_does_not_spend():
    """HIGH: sprint open 但 rounds 結構不符（_derive_rounds 回空）-> 不送任何
    relic_up，直接回 protocol_mismatch（花了也領不到輪獎）。"""
    tracker = _Tracker({FRAG: 1_000_000})
    # 2-task list -> wrong size -> _derive_rounds returns () -> rounds empty
    bad_body = _sprint_body(269, 2698, [(269101, 0, 0), (269102, 0, 0)])

    def _sprint_info(body):
        act_type = codec.walk_dict(body).get(1)
        if act_type == 269:
            return [s2c(CMD_SPRINT_INFO, bad_body)]
        return [s2c(CMD_SPRINT_INFO, codec.pb_uint(1, 13))]

    c, fake = _client({
        CMD_SPRINT_INFO: _sprint_info,
        CMD_RELIC_INFO: lambda _b: [s2c(CMD_RELIC_INFO, b"")],
        CMD_RELIC_UP: lambda _b: [s2c(CMD_RELIC_UP, b"")],
        CMD_SPRINT_REWARD: lambda _b: [s2c(CMD_SPRINT_REWARD, b"")],
    })
    try:
        out = run_relic_sprint(c, tracker, enabled=True)
        assert out["skipped"] == "protocol_mismatch"
        assert out["act_type"] == 269
        # crucially: NO relic_up and NO claim sent — we never spend a fragment.
        assert CMD_RELIC_UP not in fake.sent_cmds()
        assert CMD_SPRINT_REWARD not in fake.sent_cmds()
    finally:
        c.close()


def test_constants_match_live_recon():
    assert SUBTASKS_PER_ROUND == 7
    assert NUM_ROUNDS == 4
    assert ROUND_THRESHOLDS == (225_000, 450_000, 675_000, 900_000)
    # hard backstop on the number of level-ups (full 900K sprint is ~7 LIVE)
    assert MAX_SPRINT_UPGRADES == 30


# ---------------------------------------------------------------------------
# calendar (6576 act_cross_limit_rank_calendar) — end-date for dashboard.
# LIVE capture 2026-06-19 (小寶 7fe98fc6, CDP 9226): the empty-body reply lists
# every act_type's scheduled windows as repeated #1 {act#1, begin#2, end#3}
# (Unix seconds, clean UTC+8 midnight boundaries). act 269 appears twice — the
# CURRENT window (06-15→06-22) plus a future one-day rerun (07-13→07-14).
# ---------------------------------------------------------------------------
LIVE_CAL = [
    (269, 1783872000, 1783958400), (267, 1783267200, 1783872000),
    (278, 1783440000, 1783699200), (268, 1783353600, 1783526400),
    (265, 1782662400, 1783267200), (266, 1782748800, 1782921600),
    (263, 1782057600, 1782662400), (264, 1782057600, 1782662400),
    (276, 1782403200, 1782662400), (277, 1782230400, 1782489600),
    (269, 1781452800, 1782057600), (275, 1781625600, 1781884800),
    (270, 1781539200, 1781712000),
]
NOW_2026_06_19 = 1781880588  # within the current 269 window per the capture


def _cal_body(triples):
    return b"".join(
        codec.pb_msg(1, codec.pb_uint(1, a) + codec.pb_uint(2, b) + codec.pb_uint(3, e))
        for a, b, e in triples
    )


def test_parse_calendar_decodes_live_windows():
    wins = parse_calendar(CMD_SPRINT_CALENDAR, _cal_body(LIVE_CAL))
    assert len(wins) == len(LIVE_CAL)
    assert wins[0] == CalWindow(269, 1783872000, 1783958400)
    # both 269 windows are present
    assert sum(1 for w in wins if w.act_type == 269) == 2


def test_parse_calendar_non_calendar_cmd_is_empty():
    assert parse_calendar(CMD_ERROR, _cal_body(LIVE_CAL)) == ()


def test_active_window_picks_window_covering_now():
    wins = parse_calendar(CMD_SPRINT_CALENDAR, _cal_body(LIVE_CAL))
    win = active_window(wins, 269, NOW_2026_06_19)
    assert win is not None
    # the CURRENT 269 window, not the future 07-13 rerun
    assert (win.begin, win.end) == (1781452800, 1782057600)


def test_active_window_none_when_no_window_covers_now():
    wins = parse_calendar(CMD_SPRINT_CALENDAR, _cal_body(LIVE_CAL))
    assert active_window(wins, 269, now=1) is None          # before any window
    assert active_window(wins, 999, NOW_2026_06_19) is None  # act not scheduled


def test_active_window_earliest_end_when_multiple_cover():
    # two windows of the same act both covering now -> pick the earliest-ending
    wins = (CalWindow(269, 100, 900), CalWindow(269, 100, 500))
    assert active_window(wins, 269, now=200).end == 500

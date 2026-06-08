"""Tests for ws_token.main_tasks — 主畫面自動領任務 over pure WS (TASK module 10).

Schemas are the live-exported truth (docs/protocol/TASK_PROTO_SCHEMA.json +
TYPE_PROTO_SCHEMA.json p_task / p_key_value):
  task_all_s2c        { task_list#1:p_task[] }
  p_task              { task_id#1:uint64, state#2:int32, count#3:int64, type#4:int32 }
  task_commit_c2s     { type#1:int32, task_id#2:uint64 }
  task_daily_point_s2c{ daily_point#1:int32, box_list#2:p_key_value[] }
  p_key_value         { k#1:int64, v#2:int64 }
  task_weekly_box_s2c { status#1:uint32, last_list#2, cur_list#3, week_day#4 }
  task_achievement_s2c{ get_id#1, now_id#2, progress#3 }

Read is PUSH-based: task_all / task_daily_point / task_weekly_box arrive as
unsolicited server frames right after login. The collector is mounted as the
client's push_handler BEFORE connect so it captures those login pushes; tests
script them by returning extra frames alongside login_ok().
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.main_tasks import (  # noqa: E402
    CMD_ACHIEVEMENT,
    CMD_ACHIEVEMENT_REWARD,
    CMD_ALL,
    CMD_COMMIT,
    CMD_DAILY_POINT,
    CMD_ERROR,
    CMD_REQ_DAILY_BOX,
    CMD_REQ_WEEKLY_BOX,
    CMD_WEEKLY_BOX,
    TYPE_DAILY,
    Task,
    TaskCollector,
    TaskState,
    build_commit_body,
    claim_achievement,
    claim_daily_box,
    claim_daily_tasks,
    claim_weekly_box,
    collect_state,
    parse_achievement,
    parse_daily_point,
    parse_task_all,
    parse_weekly_box,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_ok,
    s2c,
)


# --- wire builders for the push bodies --------------------------------------

def _p_task(task_id, state, count, type_):
    return (codec.pb_uint(1, task_id) + codec.pb_uint(2, state)
            + codec.pb_uint(3, count) + codec.pb_uint(4, type_))


def _task_all_body(tasks):
    return b"".join(codec.pb_msg(1, _p_task(*t)) for t in tasks)


def _kv(k, v):
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _daily_point_body(daily_point, boxes):
    return (codec.pb_uint(1, daily_point)
            + b"".join(codec.pb_msg(2, _kv(k, v)) for k, v in boxes))


def _weekly_body(status, week_day=3, last_list=(), cur_list=()):
    out = codec.pb_uint(1, status)
    for v in last_list:
        out += codec.pb_uint(2, v)
    for v in cur_list:
        out += codec.pb_uint(3, v)
    return out + codec.pb_uint(4, week_day)


def _ach_body(get_id, now_id, progress):
    return codec.pb_uint(1, get_id) + codec.pb_uint(2, now_id) + codec.pb_uint(3, progress)


# --- build_commit_body: {type#1, task_id#2} byte structure ------------------

def test_build_commit_body_is_type_then_task_id():
    # task_commit_c2s {type#1:int32, task_id#2:uint64}
    # build_commit_body(type_=2, task_id=1) -> 08 02 (type) 10 01 (task_id)
    assert build_commit_body(2, 1) == bytes.fromhex("08021001")


def test_build_commit_body_real_wire():
    assert build_commit_body(TYPE_DAILY, 89616640123660) == (
        codec.pb_uint(1, TYPE_DAILY) + codec.pb_uint(2, 89616640123660))


# --- parse_task_all: p_task decode ------------------------------------------

def test_parse_task_all_decodes_each_field():
    body = _task_all_body([(1001, 2, 5, TYPE_DAILY)])
    tasks = parse_task_all(body)
    assert len(tasks) == 1
    t = tasks[0]
    assert isinstance(t, Task)
    assert (t.task_id, t.state, t.count, t.type) == (1001, 2, 5, TYPE_DAILY)


def test_parse_task_all_multiple():
    body = _task_all_body([(1, 1, 0, 1), (2, 2, 3, 2), (3, 3, 9, 2)])
    tasks = parse_task_all(body)
    assert [t.task_id for t in tasks] == [1, 2, 3]


def test_parse_task_all_empty():
    assert parse_task_all(b"") == []


# --- parse_daily_point: daily_point + box_list ------------------------------

def test_parse_daily_point_decodes_point_and_boxes():
    body = _daily_point_body(120, [(1, 2), (2, 1)])  # box1 claimed, box2 claimable
    point, boxes = parse_daily_point(body)
    assert point == 120
    assert boxes == [(1, 2), (2, 1)]


def test_parse_daily_point_no_boxes():
    point, boxes = parse_daily_point(_daily_point_body(0, []))
    assert point == 0 and boxes == []


# --- parse_weekly_box: status -----------------------------------------------

def test_parse_weekly_box_status_claimable():
    status, week_day = parse_weekly_box(_weekly_body(1, week_day=5))
    assert status == 1 and week_day == 5


def test_parse_weekly_box_status_not_ready():
    status, _ = parse_weekly_box(_weekly_body(0))
    assert status == 0


# --- parse_achievement: get_id / now_id / progress --------------------------

def test_parse_achievement_fields():
    a = parse_achievement(_ach_body(get_id=4, now_id=5, progress=999))
    assert (a.get_id, a.now_id, a.progress) == (4, 5, 999)


# --- TaskCollector: accumulates the latest push bodies ----------------------

def test_collector_keeps_latest_of_each_cmd():
    col = TaskCollector()
    col(CMD_ALL, _task_all_body([(1, 2, 0, TYPE_DAILY)]))
    col(CMD_ALL, _task_all_body([(9, 1, 0, 1)]))  # newer task_all wins
    col(CMD_DAILY_POINT, _daily_point_body(50, [(7, 1)]))
    col(CMD_WEEKLY_BOX, _weekly_body(1))
    state = col.build()
    assert isinstance(state, TaskState)
    assert [t.task_id for t in state.tasks] == [9]
    assert state.daily_point == 50
    assert state.daily_boxes == [(7, 1)]
    assert state.weekly_status == 1


def test_collector_ignores_unrelated_cmd():
    col = TaskCollector()
    col(0x9999, b"\x08\x01")  # not a task push
    state = col.build()
    assert state.tasks == [] and state.daily_point == 0
    assert state.daily_boxes == [] and state.weekly_status == 0


# --- collect_state: collector mounted as push_handler before connect --------

def _client(extra=None, login_frames=None):
    """A connected client whose push_handler is a fresh TaskCollector.

    ``login_frames`` are appended after login_ok() so the collector captures the
    login-time task pushes (mounted before connect, exactly like the smoke).
    """
    col = TaskCollector()

    def responder(cmd, body):
        if cmd == 257:  # CMD_LOGIN
            return [login_ok(), *(login_frames or [])]
        if extra and cmd in extra:
            return extra[cmd](body)
        return []

    fake = FakeTransport(responder)
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False, push_handler=col)
    c.connect()
    return c, fake, col


def test_collect_state_captures_login_pushes():
    login_frames = [
        s2c(CMD_ALL, _task_all_body([(11, 2, 1, TYPE_DAILY), (22, 1, 0, 1)])),
        s2c(CMD_DAILY_POINT, _daily_point_body(80, [(1, 1)])),
        s2c(CMD_WEEKLY_BOX, _weekly_body(1, week_day=2)),
    ]
    c, _, col = _client(login_frames=login_frames)
    try:
        state = collect_state(c, col, settle=0.5)
        assert [t.task_id for t in state.tasks] == [11, 22]
        assert state.daily_point == 80
        assert state.daily_boxes == [(1, 1)]
        assert state.weekly_status == 1
    finally:
        c.close()


# --- claim_daily_tasks: only type==2 and state==2; stop on 0x0201 -----------

def test_claim_daily_tasks_only_commits_claimable_daily():
    state = TaskState(
        tasks=[
            Task(101, 2, 0, TYPE_DAILY),  # claimable daily -> commit
            Task(102, 1, 0, TYPE_DAILY),  # daily but not claimable (state 1)
            Task(103, 2, 0, 1),           # claimable but Main (type 1), not daily
            Task(104, 3, 0, TYPE_DAILY),  # daily already claimed (state 3)
        ],
        daily_point=0, daily_boxes=[], weekly_status=0,
    )
    commits = []

    def commit_resp(body):
        commits.append(codec.walk_dict(body))
        return [s2c(CMD_COMMIT, body)]  # echo back as success

    c, fake, _ = _client({CMD_COMMIT: commit_resp})
    try:
        summary = claim_daily_tasks(c, state)
        assert summary["attempted"] == 1
        assert summary["claimed"] == 1
        # exactly one commit, for task 101 with type=TYPE_DAILY
        assert commits == [{1: TYPE_DAILY, 2: 101}]
    finally:
        c.close()


def test_claim_daily_tasks_stops_counting_on_error():
    state = TaskState(
        tasks=[Task(201, 2, 0, TYPE_DAILY), Task(202, 2, 0, TYPE_DAILY)],
        daily_point=0, daily_boxes=[], weekly_status=0,
    )

    def commit_resp(body):
        # task 201 ok, task 202 returns 0x0201 error
        if codec.walk_dict(body).get(2) == 201:
            return [s2c(CMD_COMMIT, body)]
        return [s2c(CMD_ERROR, codec.pb_uint(1, 2))]

    c, _, _ = _client({CMD_COMMIT: commit_resp})
    try:
        summary = claim_daily_tasks(c, state)
        assert summary["attempted"] == 2
        assert summary["claimed"] == 1  # only 201 counts; 202 errored
    finally:
        c.close()


def test_claim_daily_tasks_none_claimable():
    state = TaskState(tasks=[Task(1, 1, 0, TYPE_DAILY)], daily_point=0,
                      daily_boxes=[], weekly_status=0)
    c, fake, _ = _client()
    try:
        summary = claim_daily_tasks(c, state)
        assert summary["attempted"] == 0 and summary["claimed"] == 0
        assert CMD_COMMIT not in fake.sent_cmds()
    finally:
        c.close()


# --- claim_daily_box: only when a box state==1 exists -----------------------

def test_claim_daily_box_sends_when_claimable():
    state = TaskState(tasks=[], daily_point=100, daily_boxes=[(1, 2), (2, 1)],
                      weekly_status=0)
    c, fake, _ = _client(
        {CMD_REQ_DAILY_BOX: lambda _b: [s2c(CMD_REQ_DAILY_BOX, b"")]})
    try:
        claimed = claim_daily_box(c, state)
        assert claimed is True
        assert CMD_REQ_DAILY_BOX in fake.sent_cmds()
    finally:
        c.close()


def test_claim_daily_box_skips_when_no_claimable_box():
    state = TaskState(tasks=[], daily_point=100, daily_boxes=[(1, 2), (2, 2)],
                      weekly_status=0)
    c, fake, _ = _client()
    try:
        claimed = claim_daily_box(c, state)
        assert claimed is False
        assert CMD_REQ_DAILY_BOX not in fake.sent_cmds()
    finally:
        c.close()


# --- claim_weekly_box: only when status==1 ----------------------------------

def test_claim_weekly_box_sends_when_status_one():
    state = TaskState(tasks=[], daily_point=0, daily_boxes=[], weekly_status=1)
    c, fake, _ = _client(
        {CMD_REQ_WEEKLY_BOX: lambda _b: [s2c(CMD_REQ_WEEKLY_BOX, codec.pb_uint(1, 1))]})
    try:
        claimed = claim_weekly_box(c, state)
        assert claimed is True
        assert CMD_REQ_WEEKLY_BOX in fake.sent_cmds()
    finally:
        c.close()


def test_claim_weekly_box_skips_when_status_zero():
    state = TaskState(tasks=[], daily_point=0, daily_boxes=[], weekly_status=0)
    c, fake, _ = _client()
    try:
        assert claim_weekly_box(c, state) is False
        assert CMD_REQ_WEEKLY_BOX not in fake.sent_cmds()
    finally:
        c.close()


# --- claim_achievement: loop until get_id==now_id or error ------------------

def test_claim_achievement_loops_until_caught_up():
    # First read: get_id=3, now_id=5 (2 milestones unclaimed). Each reward bumps
    # get_id by 1 and re-reads; stop when get_id==now_id.
    reads = iter([
        _ach_body(get_id=3, now_id=5, progress=999),  # initial achievement read
        _ach_body(get_id=4, now_id=5, progress=999),  # after 1st reward
        _ach_body(get_id=5, now_id=5, progress=999),  # after 2nd reward -> caught up
    ])
    reward_calls = {"n": 0}

    def ach_resp(_b):
        return [s2c(CMD_ACHIEVEMENT, next(reads))]

    def reward_resp(_b):
        reward_calls["n"] += 1
        return [s2c(CMD_ACHIEVEMENT_REWARD, b"")]

    c, _, _ = _client({CMD_ACHIEVEMENT: ach_resp,
                       CMD_ACHIEVEMENT_REWARD: reward_resp})
    try:
        summary = claim_achievement(c)
        assert reward_calls["n"] == 2
        assert summary["claimed"] == 2
    finally:
        c.close()


def test_claim_achievement_stops_when_already_caught_up():
    c, fake, _ = _client(
        {CMD_ACHIEVEMENT: lambda _b: [s2c(CMD_ACHIEVEMENT, _ach_body(5, 5, 0))]})
    try:
        summary = claim_achievement(c)
        assert summary["claimed"] == 0
        assert CMD_ACHIEVEMENT_REWARD not in fake.sent_cmds()
    finally:
        c.close()


def test_claim_achievement_stops_on_error():
    # get_id<now_id but the reward replies with 0x0201 -> stop, do not loop forever
    def ach_resp(_b):
        return [s2c(CMD_ACHIEVEMENT, _ach_body(3, 5, 999))]

    def reward_resp(_b):
        return [s2c(CMD_ERROR, codec.pb_uint(1, 2))]

    c, _, _ = _client({CMD_ACHIEVEMENT: ach_resp,
                       CMD_ACHIEVEMENT_REWARD: reward_resp})
    try:
        summary = claim_achievement(c)
        assert summary["claimed"] == 0
        assert summary.get("error") is not None
    finally:
        c.close()

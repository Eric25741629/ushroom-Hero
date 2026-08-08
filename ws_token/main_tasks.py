"""Main-screen 自動領任務 over a logged-in WSGameClient (TASK module 10).

Pure WS. The read side is PUSH-based: right after role_login the server pushes
the task snapshot unsolicited (no c2s request), so this module supplies a
``TaskCollector`` you mount as the client's ``push_handler`` *before*
``connect()`` to capture those login frames. The claim orchestrators are plain
request/response on top of WSGameClient.

Schemas (docs/protocol/TASK_PROTO_SCHEMA.json + TYPE_PROTO_SCHEMA.json):
  task_all_s2c         { task_list#1:p_task[] }                          PUSH
  p_task               { task_id#1:uint64, state#2, count#3:int64, type#4 }
  task_daily_point_s2c { daily_point#1, box_list#2:p_key_value[] }       PUSH
  p_key_value          { k#1:int64, v#2:int64 }   (k=box_id, v=state)
  task_weekly_box_s2c  { status#1, last_list#2, cur_list#3, week_day#4 } PUSH
  task_commit_c2s      { type#1:int32, task_id#2:uint64 }                claim one
  task_req_daily_box_c2s {}                                              claim activity boxes
  task_req_weekly_box_c2s {}                                             claim weekly box
  task_achievement_s2c { get_id#1, now_id#2, progress#3 }
  task_achievement_reward_c2s {}                                         claim a milestone

state: 1=未達成 / 2=可領 / 3=已領.  type (TaskType enum): Main=1, Daily=2,
  AdventureTitle=3, GameRoom=5, Marry=6, RebetTunble=7, KungfuRace=8,
  Achievement=9, FlyPet=10.  Marry(6) = 婚姻/默契考驗 好感週任務 (claimed like any
  task via task_commit{type:6, task_id}; see claim_marry_tasks).
daily box v: 1=可領未領 / 2=已領.  weekly status==1 => 可領.
Errors arrive on error.error_info_s2c (0x0201) {error_code#1}.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (TASK module 10; c2s and s2c share the id) ---------------------
CMD_ALL = 2561              # 0x0A01 task_all_s2c          (PUSH)
CMD_COMMIT = 2562           # 0x0A02 task_commit
CMD_DAILY_POINT = 2565      # 0x0A05 task_daily_point_s2c  (PUSH)
CMD_REQ_DAILY_BOX = 2566    # 0x0A06 task_req_daily_box
CMD_WEEKLY_BOX = 2567       # 0x0A07 task_weekly_box_s2c   (PUSH)
CMD_REQ_WEEKLY_BOX = 2568   # 0x0A08 task_req_weekly_box
CMD_ACHIEVEMENT = 2569      # 0x0A09 task_achievement
CMD_ACHIEVEMENT_REWARD = 2570  # 0x0A0A task_achievement_reward
CMD_ERROR = 0x0201          # error.error_info_s2c {error_code#1}

# p_task.state values
STATE_PENDING = 1     # 未達成
STATE_CLAIMABLE = 2   # 可領
STATE_CLAIMED = 3     # 已領

# p_task.type values (TaskType enum, client index.966f5.js)
TYPE_MAIN = 1
TYPE_DAILY = 2
TYPE_ADVENTURE_TITLE = 3
TYPE_GAMEROOM = 5
TYPE_MARRY = 6          # 婚姻/默契考驗 好感週任務 (MarryFavorWeekTaskView)
TYPE_REBET_TUNBLE = 7
TYPE_KUNGFU_RACE = 8
TYPE_ACHIEVEMENT = 9
TYPE_FLYPET = 10

# daily box state (p_key_value.v)
BOX_CLAIMABLE = 1     # 可領未領
BOX_CLAIMED = 2       # 已領

# 對外用「15 個每日任務 + 1 個總獎勵」呈現；伺服器實際筆數仍保留在摘要中。
EXPECTED_DAILY_TASK_COUNT = 15
EXPECTED_DAILY_BOX_COUNT = 6

# weekly status
WEEKLY_CLAIMABLE = 1

# guard so a malformed/never-converging achievement read can't loop forever
_ACHIEVEMENT_MAX_ITERS = 100


# --- data ------------------------------------------------------------------

@dataclass(frozen=True)
class Task:
    """One entry from task_all_s2c.task_list (p_task)."""

    task_id: int   # task_id #1
    state: int     # #2: 1 未達成 / 2 可領 / 3 已領
    count: int     # #3: progress count
    type: int      # #4: 1 Main / 2 Daily / 9 Achievement / 10 FlyPet


@dataclass(frozen=True)
class Achievement:
    """task_achievement_s2c {get_id#1, now_id#2, progress#3}."""

    get_id: int    # last claimed milestone id
    now_id: int    # current reached milestone id
    progress: int  # progress toward the next milestone


@dataclass(frozen=True)
class TaskState:
    """Snapshot assembled from the login-time PUSH frames."""

    tasks: list = field(default_factory=list)              # list[Task]
    daily_point: int = 0                                   # 活躍度
    daily_boxes: list = field(default_factory=list)        # list[(box_id, state)]
    weekly_status: int = 0                                 # 1 => claimable


def summarize_daily_progress(
    state: TaskState,
    *,
    newly_claimed: int = 0,
    expected_tasks: int = EXPECTED_DAILY_TASK_COUNT,
) -> dict:
    """將每日任務伺服器快照壓成只含數量的 15+1 摘要。"""
    daily_tasks = [task for task in state.tasks if task.type == TYPE_DAILY]
    server_total = len(daily_tasks)
    completed = sum(
        task.state in (STATE_CLAIMABLE, STATE_CLAIMED) for task in daily_tasks
    )
    rewards_claimed = sum(task.state == STATE_CLAIMED for task in daily_tasks)
    boxes_total = len(state.daily_boxes)
    boxes_claimed = sum(
        box_state == BOX_CLAIMED for _box_id, box_state in state.daily_boxes
    )
    activity_reward = int(
        completed >= expected_tasks and boxes_claimed >= EXPECTED_DAILY_BOX_COUNT
    )
    display_total = server_total or expected_tasks
    mismatch = (
        f"（期望 {expected_tasks}）"
        if server_total and server_total != expected_tasks
        else ""
    )
    detail = (
        f"每日任務 {completed}/{display_total}{mismatch}，"
        f"總獎勵 {activity_reward}/1，本輪新領 {int(newly_claimed or 0)}"
    )
    return {
        "expected_tasks": expected_tasks,
        "server_total": server_total,
        "completed": completed,
        "rewards_claimed": rewards_claimed,
        "pending": max(0, server_total - completed),
        "activity_reward": activity_reward,
        "activity_boxes_claimed": boxes_claimed,
        "activity_boxes_total": boxes_total,
        "newly_claimed": int(newly_claimed or 0),
        "detail": detail,
    }


# --- parsers ----------------------------------------------------------------

def parse_task_all(body: bytes) -> list[Task]:
    """task_all_s2c: task_list is repeated p_task at field 1."""
    out: list[Task] = []
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            out.append(Task(
                task_id=_as_int(d.get(1)),
                state=_as_int(d.get(2)),
                count=_as_int(d.get(3)),
                type=_as_int(d.get(4)),
            ))
    return out


def parse_daily_point(body: bytes) -> tuple[int, list[tuple[int, int]]]:
    """task_daily_point_s2c -> (daily_point, [(box_id, state), ...])."""
    daily_point = 0
    boxes: list[tuple[int, int]] = []
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, int):
            daily_point = v
        elif fnum == 2 and isinstance(v, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(v))
            boxes.append((_as_int(kv.get(1)), _as_int(kv.get(2))))
    return daily_point, boxes


def parse_weekly_box(body: bytes) -> tuple[int, int]:
    """task_weekly_box_s2c -> (status, week_day). status==1 => claimable."""
    d = codec.walk_dict(body)
    return _as_int(d.get(1)), _as_int(d.get(4))


def parse_achievement(body: bytes) -> Achievement:
    """task_achievement_s2c {get_id#1, now_id#2, progress#3}."""
    d = codec.walk_dict(body)
    return Achievement(
        get_id=_as_int(d.get(1)),
        now_id=_as_int(d.get(2)),
        progress=_as_int(d.get(3)),
    )


# --- push collector ---------------------------------------------------------

class TaskCollector:
    """Accumulates the latest task PUSH bodies, then builds a TaskState.

    Mount as ``WSGameClient(creds, push_handler=collector)`` BEFORE connect so
    the login-time pushes (task_all / daily_point / weekly_box) land here.
    Keeps only the most recent body per cmd (a later task_all replaces earlier).
    """

    __slots__ = ("_latest",)

    def __init__(self) -> None:
        self._latest: dict[int, bytes] = {}

    def __call__(self, cmd: int, body: bytes) -> None:
        if cmd in (CMD_ALL, CMD_DAILY_POINT, CMD_WEEKLY_BOX):
            self._latest[cmd] = bytes(body)

    def build(self) -> TaskState:
        tasks: list[Task] = []
        daily_point = 0
        daily_boxes: list[tuple[int, int]] = []
        weekly_status = 0
        if CMD_ALL in self._latest:
            tasks = parse_task_all(self._latest[CMD_ALL])
        if CMD_DAILY_POINT in self._latest:
            daily_point, daily_boxes = parse_daily_point(self._latest[CMD_DAILY_POINT])
        if CMD_WEEKLY_BOX in self._latest:
            weekly_status, _ = parse_weekly_box(self._latest[CMD_WEEKLY_BOX])
        return TaskState(
            tasks=tasks,
            daily_point=daily_point,
            daily_boxes=daily_boxes,
            weekly_status=weekly_status,
        )


def collect_state(
    client: WSGameClient,
    collector: TaskCollector,
    *,
    settle: float = 1.0,
) -> TaskState:
    """Wait ``settle`` seconds for login pushes to arrive, then snapshot.

    The collector must already be mounted as the client's push_handler before
    ``connect()``; this just gives the reader thread a moment to drain the
    login-time frames before building the state.
    """
    if settle > 0:
        time.sleep(settle)
    state = collector.build()
    logger.info(
        "ws_token tasks: %d task(s), daily_point=%d, %d daily box(es), weekly_status=%d",
        len(state.tasks), state.daily_point, len(state.daily_boxes), state.weekly_status,
    )
    return state


# --- builders ---------------------------------------------------------------

def build_commit_body(type_: int, task_id: int) -> bytes:
    """task_commit_c2s {type#1:int32, task_id#2:uint64} — type FIRST, then id."""
    return codec.pb_uint(1, type_) + codec.pb_uint(2, task_id)


# --- claim orchestrators ----------------------------------------------------

def _is_error(cmd: int) -> bool:
    return cmd == CMD_ERROR


def _claim_tasks_of_type(
    client: WSGameClient,
    state: TaskState,
    type_: int,
    *,
    spacing: float = 0.0,
    timeout: float | None = None,
) -> dict:
    """Commit every claimable task of ``type_`` (state==Claimable).

    Each is sent as task_commit{type:type_, task_id}; a 0x0201 reply counts as a
    skip/failure (the task simply isn't counted). Returns
    {attempted, claimed, results:[(task_id, ok)]}.
    """
    claimable = [t for t in state.tasks
                 if t.type == type_ and t.state == STATE_CLAIMABLE]
    results: list[tuple[int, bool]] = []
    claimed = 0
    for t in claimable:
        cmd, _body = client.call_for(
            CMD_COMMIT, build_commit_body(type_, t.task_id),
            expect_cmds=(CMD_COMMIT, CMD_ERROR), timeout=timeout)
        ok = not _is_error(cmd)
        results.append((t.task_id, ok))
        if ok:
            claimed += 1
        else:
            logger.info("ws_token tasks: type=%d task %s commit rejected (0x0201)",
                        type_, t.task_id)
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token tasks: type=%d commit attempted=%d claimed=%d",
                type_, len(claimable), claimed)
    return {"attempted": len(claimable), "claimed": claimed, "results": results}


def claim_daily_tasks(
    client: WSGameClient,
    state: TaskState,
    *,
    spacing: float = 0.0,
    timeout: float | None = None,
) -> dict:
    """Commit every claimable daily task (type==Daily and state==Claimable).

    Each is sent as task_commit{type:2, task_id}; a 0x0201 reply counts as a
    skip/failure (the task simply isn't counted). Returns
    {attempted, claimed, results:[(task_id, ok)]}.
    """
    return _claim_tasks_of_type(client, state, TYPE_DAILY,
                                spacing=spacing, timeout=timeout)


def claim_marry_tasks(
    client: WSGameClient,
    state: TaskState,
    *,
    spacing: float = 0.0,
    timeout: float | None = None,
) -> dict:
    """Commit every claimable 婚姻/默契考驗 weekly task (type==Marry=6).

    The 默契考驗 (好感週任務, in-game MarryFavorWeekTaskView) rewards are plain
    TaskType.Marry tasks in the global task push — claimed via
    task_commit{type:6, task_id}, exactly like any other task. This is the real
    married-couple weekly claim, NOT couple.favor_reward_fetch (15142, which is
    the UNMARRIED friend-favor milestone path used by MarryIntimacyView and which
    returns 0x0201 code 2 for married accounts).

    Live-verified 2026-06-10 on 小寶 (3 claimable) + emulator-5554 (2 claimable):
    every task_commit{type:6} returned OK and a re-read showed 0 remaining.
    Returns {attempted, claimed, results:[(task_id, ok)]}.
    """
    return _claim_tasks_of_type(client, state, TYPE_MARRY,
                                spacing=spacing, timeout=timeout)


def claim_daily_box(
    client: WSGameClient,
    state: TaskState,
    *,
    timeout: float | None = None,
) -> bool:
    """Claim the daily-activity boxes if any box is claimable (state==1).

    The server grants all reached boxes from a single task_req_daily_box{}.
    Returns True iff the request was sent (i.e. there was something to claim).
    """
    if not any(st == BOX_CLAIMABLE for _bid, st in state.daily_boxes):
        return False
    cmd, _body = client.call_for(
        CMD_REQ_DAILY_BOX, b"",
        expect_cmds=(CMD_REQ_DAILY_BOX, CMD_ERROR), timeout=timeout)
    if _is_error(cmd):
        logger.info("ws_token tasks: daily box request rejected (0x0201)")
        return False
    logger.info("ws_token tasks: daily activity box(es) claimed")
    return True


def claim_weekly_box(
    client: WSGameClient,
    state: TaskState,
    *,
    timeout: float | None = None,
) -> bool:
    """Claim the weekly box if weekly_status==1. Returns True iff requested."""
    if state.weekly_status != WEEKLY_CLAIMABLE:
        return False
    cmd, _body = client.call_for(
        CMD_REQ_WEEKLY_BOX, b"",
        expect_cmds=(CMD_REQ_WEEKLY_BOX, CMD_ERROR), timeout=timeout)
    if _is_error(cmd):
        logger.info("ws_token tasks: weekly box request rejected (0x0201)")
        return False
    logger.info("ws_token tasks: weekly box claimed")
    return True


def read_achievement(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> Achievement:
    """Read the current achievement milestone state (task_achievement{})."""
    body = client.call(CMD_ACHIEVEMENT, b"", timeout=timeout)
    return parse_achievement(body)


def claim_achievement(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> dict:
    """Claim every reached-but-unclaimed achievement milestone.

    Loop: read task_achievement{}; while ``get_id != now_id`` send
    task_achievement_reward{} and re-read. Stop when caught up (get_id==now_id),
    on a 0x0201 error, or after a hard iteration cap. The per-milestone reward
    threshold lives in client config (not on the wire), so "get_id != now_id"
    is the claimable signal; a non-advancing read is treated as stuck and stops.

    Returns {claimed, get_id, now_id, error}.
    """
    ach = read_achievement(client, timeout=timeout)
    claimed = 0
    error: str | None = None
    for _ in range(_ACHIEVEMENT_MAX_ITERS):
        if ach.get_id >= ach.now_id:
            break
        prev_get_id = ach.get_id
        cmd, _body = client.call_for(
            CMD_ACHIEVEMENT_REWARD, b"",
            expect_cmds=(CMD_ACHIEVEMENT_REWARD, CMD_ERROR), timeout=timeout)
        if _is_error(cmd):
            error = "achievement reward rejected (0x0201)"
            logger.info("ws_token tasks: %s at get_id=%d now_id=%d",
                        error, ach.get_id, ach.now_id)
            break
        claimed += 1
        ach = read_achievement(client, timeout=timeout)
        if ach.get_id <= prev_get_id:
            error = "achievement get_id did not advance; stopping"
            logger.warning("ws_token tasks: %s (get_id=%d now_id=%d)",
                           error, ach.get_id, ach.now_id)
            break
    logger.info("ws_token tasks: achievement claimed=%d get_id=%d now_id=%d",
                claimed, ach.get_id, ach.now_id)
    return {"claimed": claimed, "get_id": ach.get_id, "now_id": ach.now_id,
            "error": error}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

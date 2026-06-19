"""遺物碎片衝刺 (Relic Sprint / 衝刺榜) — pure-WS read + claim over act2 (25 / 0x19).

The "遺物碎片衝刺" is one rotation of the generic cross-server limited rank
("衝刺榜 / RankRush / cross_limited_rank") activity framework, gated to the relic
養成 system: spending 遺物碎片 (item 100022) accrues server-side toward 4 cumulative
rounds (``small_group_id`` 1..4). Reaching a round's threshold flips that task to
CanGet; the per-round reward is then claimed with one packet.

🔑 The spend itself is NOT submitted here. relic ``relic_level_up`` (0x1103)
deducts the fragments and the server folds that consumption straight into the
sprint ``count`` — so the flow is: SPEND fragments by levelling relics
(:func:`ws_token.relic.spend_to_target`) → re-read the sprint → claim every
CanGet round. See docs/protocol/RELIC_SPRINT_RECON.md (唯讀 recon, 2026-06-19).

Cmd map (cmd = module(25)*256 + sub; c2s/s2c share the id; failures -> 0x0201)::

  act_cross_limited_rank_info        6572 (0x19AC)  c2s {act_type#1}
        -> s2c {act_type#1, group_id#2, task_list#3: p_cross_limited_rank_task[]}
  act_cross_limited_rank_task_reward 6575 (0x19AF)  c2s {act_type#1, small_group_id#2}
        -> s2c (status -> HadGet, reward via 0x0402 push) OR 0x0201 error
  act_cross_limit_rank_calendar      6576 (0x19B0)  c2s {}  (optional; which type Open)

  p_cross_limited_rank_task {task_id#1, status#2, count#3:uint64}
     status: 0=Normal, 1=CanGet, 2=HadGet

act_type is **13 (RankRush_8) or 269 (RankRush_New_7)**, monthly-rotated — never
hard-code which is current; :func:`find_active_act_type` probes both and picks the
one that answers with a task_list.

Discipline mirrors ws_token/tycoon.py (act packet shape) + ws_token/ad_reward.py
(read once → act only on the deficit → never re-claim a HadGet round).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec, relic
from ws_token.client import WSError, WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# --- cmd ids (act2 module 25 / 0x19); c2s and s2c share the same id ----------
CMD_SPRINT_INFO = 0x19AC      # 6572 act_cross_limited_rank_info
CMD_SPRINT_REWARD = 0x19AF    # 6575 act_cross_limited_rank_task_reward
CMD_SPRINT_CALENDAR = 0x19B0  # 6576 act_cross_limit_rank_calendar
CMD_ERROR = 0x0201            # error.error_info_s2c {error_code#1}

# 遺物碎片衝刺 candidate act_types (monthly-rotated — probe, don't hard-code).
ACT_TYPES: tuple[int, ...] = (13, 269)  # RankRush_8 / RankRush_New_7

# 4 cumulative rounds; small_group_id 1..4. Thresholds (cumulative 遺物碎片 spent)
# are the推斷 values pending live confirm (RELIC_SPRINT_RECON.md 待 live #1).
ROUND_THRESHOLDS: tuple[int, ...] = (225_000, 450_000, 675_000, 900_000)
SPRINT_TOTAL = 900_000  # = ROUND_THRESHOLDS[-1]; full-sprint target spend

# p_cross_limited_rank_task.status (ActivityTaskState)
STATUS_NORMAL = 0
STATUS_CAN_GET = 1
STATUS_HAD_GET = 2


# --- results ----------------------------------------------------------------


@dataclass(frozen=True)
class SprintTask:
    """One p_cross_limited_rank_task: {task_id#1, status#2, count#3}."""

    task_id: int       # #1
    status: int        # #2 — 0 Normal / 1 CanGet / 2 HadGet
    count: int         # #3 — server-accrued progress (cumulative fragments spent)

    @property
    def can_get(self) -> bool:
        return self.status == STATUS_CAN_GET

    @property
    def had_get(self) -> bool:
        return self.status == STATUS_HAD_GET


@dataclass(frozen=True)
class Sprint:
    """A parsed act_cross_limited_rank_info_s2c snapshot."""

    open: bool                          # True iff a task_list came back
    act_type: int = 0                   # #1
    group_id: int = 0                   # #2
    tasks: tuple[SprintTask, ...] = ()  # #3
    response_cmd: int = 0
    error_code: Optional[int] = None
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def accrued(self) -> int:
        """Max task ``count`` = cumulative progress already spent this sprint.

        All 4 rounds share one cumulative counter, so the largest reported count
        is the current progress (rounds not yet reached may report 0).
        """
        return max((t.count for t in self.tasks), default=0)

    @property
    def claimable_rounds(self) -> tuple[int, ...]:
        """small_group_ids (1..4) whose task is CanGet (claim, not yet taken)."""
        out = []
        for idx, task in enumerate(self.tasks, start=1):
            if task.can_get:
                out.append(idx)
        return tuple(out)


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of one 6575 round-reward claim."""

    success: bool
    small_group_id: int = 0
    response_cmd: int = 0
    error_code: Optional[int] = None

    @property
    def rejected(self) -> bool:
        return self.response_cmd == CMD_ERROR


# --- helpers ----------------------------------------------------------------


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _parse_task(entry: bytes) -> SprintTask:
    d = codec.walk_dict(entry)
    return SprintTask(task_id=_as_int(d.get(1)), status=_as_int(d.get(2)),
                      count=_as_int(d.get(3)))


# --- body builders ----------------------------------------------------------


def build_info_body(act_type: int) -> bytes:
    """act_cross_limited_rank_info_c2s {act_type#1}."""
    return codec.pb_uint(1, act_type)


def build_reward_body(act_type: int, small_group_id: int) -> bytes:
    """act_cross_limited_rank_task_reward_c2s {act_type#1, small_group_id#2}."""
    return codec.pb_uint(1, act_type) + codec.pb_uint(2, small_group_id)


# --- parsers ----------------------------------------------------------------


def parse_sprint(cmd: int, body: bytes) -> Sprint:
    """act_cross_limited_rank_info_s2c -> Sprint (or 0x0201 / unexpected = closed).

    A reply with NO task_list (field 3) — including a 0x0201 error — is treated as
    ``open=False`` (this act_type is not the current rotation / not yet open).
    """
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return Sprint(open=False, response_cmd=cmd,
                      error_code=int(ec) if isinstance(ec, int) else None,
                      raw=codec.walk_dict(body))
    if cmd != CMD_SPRINT_INFO:
        return Sprint(open=False, response_cmd=cmd)
    d = codec.walk_dict(body)
    tasks = tuple(
        _parse_task(bytes(v)) for fnum, v in codec.walk(body)
        if fnum == 3 and isinstance(v, (bytes, bytearray))
    )
    return Sprint(
        open=bool(tasks),
        act_type=_as_int(d.get(1)),
        group_id=_as_int(d.get(2)),
        tasks=tasks,
        response_cmd=cmd,
        raw=d,
    )


def parse_claim(cmd: int, body: bytes, small_group_id: int) -> ClaimResult:
    """act_cross_limited_rank_task_reward_s2c (CMD_SPRINT_REWARD) or 0x0201."""
    if cmd == CMD_SPRINT_REWARD:
        return ClaimResult(success=True, small_group_id=small_group_id,
                           response_cmd=cmd)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return ClaimResult(success=False, small_group_id=small_group_id,
                           response_cmd=cmd,
                           error_code=int(ec) if isinstance(ec, int) else None)
    return ClaimResult(success=False, small_group_id=small_group_id,
                       response_cmd=cmd)


# --- single calls -----------------------------------------------------------


def read_sprint(client: WSGameClient, act_type: int, *,
                timeout: Optional[float] = None) -> dict:
    """Read one act_type's sprint progress (6572). Returns a plain dict.

    ``{open, act_type, group_id, accrued, claimable_rounds, tasks}`` where ``tasks``
    is a list of ``{task_id, status, count}``. A closed / wrong-rotation act_type
    (no task_list, or a 0x0201) returns ``{"open": False, "act_type": act_type}``.
    """
    cmd, reply = client.call_for(
        CMD_SPRINT_INFO, build_info_body(act_type),
        expect_cmds=(CMD_SPRINT_INFO, CMD_ERROR), timeout=timeout)
    sprint = parse_sprint(cmd, reply)
    if not sprint.open:
        return {"open": False, "act_type": act_type,
                "error_code": sprint.error_code}
    return {
        "open": True,
        "act_type": sprint.act_type or act_type,
        "group_id": sprint.group_id,
        "accrued": sprint.accrued,
        "claimable_rounds": list(sprint.claimable_rounds),
        "tasks": [{"task_id": t.task_id, "status": t.status, "count": t.count}
                  for t in sprint.tasks],
    }


def find_active_act_type(client: WSGameClient, *,
                         timeout: Optional[float] = None) -> Optional[int]:
    """Probe ACT_TYPES; return the first one whose sprint is open (current rotation).

    The 遺物碎片衝刺 act_type rotates monthly between 13 and 269, so we ask each in
    turn and pick the one the server answers with a task_list. Returns ``None`` when
    neither is open (no active relic sprint right now).

    A not-currently-open act_type may simply time out or error instead of replying
    with an echo / 0x0201 — so each probe's :func:`read_sprint` is wrapped: a
    :class:`WSTimeoutError` / :class:`WSError` is treated as that act_type being
    closed (continue probing the next one) rather than aborting the whole sweep.
    """
    for act_type in ACT_TYPES:
        try:
            sprint = read_sprint(client, act_type, timeout=timeout)
        except (WSTimeoutError, WSError) as exc:
            logger.info("ws_token relic_sprint: act_type=%s 探測逾時/錯誤,視為關閉 (%s)",
                        act_type, exc)
            continue
        if sprint.get("open"):
            logger.info("ws_token relic_sprint: active act_type=%s", act_type)
            return act_type
    logger.info("ws_token relic_sprint: no active sprint (probed %s)",
                list(ACT_TYPES))
    return None


def claim_round(client: WSGameClient, act_type: int, small_group_id: int, *,
                timeout: Optional[float] = None) -> ClaimResult:
    """Claim one round's reward (6575 {act_type, small_group_id}); 6575 or 0x0201."""
    cmd, reply = client.call_for(
        CMD_SPRINT_REWARD, build_reward_body(act_type, small_group_id),
        expect_cmds=(CMD_SPRINT_REWARD, CMD_ERROR), timeout=timeout)
    result = parse_claim(cmd, reply, small_group_id)
    if result.success:
        logger.info("ws_token relic_sprint: claimed round %s (act_type=%s)",
                    small_group_id, act_type)
    else:
        logger.info("ws_token relic_sprint: claim round %s rejected 0x%04x code=%s",
                    small_group_id, cmd, result.error_code)
    return result


# --- orchestrator -----------------------------------------------------------


def run_relic_sprint(
    client: WSGameClient,
    tracker,
    *,
    target_spend: int = SPRINT_TOTAL,
    enabled: bool = False,
    timeout: Optional[float] = None,
) -> dict:
    """Drive 遺物碎片衝刺: SPEND fragments to ``target_spend`` then claim CanGet rounds.

    OFF by default (``enabled=False``) because it SPENDS 遺物碎片. When enabled:

      1. :func:`find_active_act_type` — bail with ``{"skipped": "no active sprint"}``
         if neither 13 nor 269 is the current rotation.
      2. Read the sprint; subtract progress already accrued so we only spend the
         remaining deficit (``remaining = max(0, target_spend - accrued)``). When
         ``remaining <= 0`` we DON'T level any relic — just claim CanGet rounds.
      3. :func:`ws_token.relic.spend_to_target` to consume ``remaining`` fragments
         (the server folds the spend into the sprint count automatically).
      4. Re-read the sprint; :func:`claim_round` for every CanGet round.

    Returns ``{act_type, spent, claimed_rounds, sprint_before, sprint_after}`` (or
    ``{"skipped": ...}`` when disabled / no active sprint).
    """
    if not enabled:
        return {"skipped": "disabled"}

    act_type = find_active_act_type(client, timeout=timeout)
    if act_type is None:
        return {"skipped": "no active sprint"}

    sprint_before = read_sprint(client, act_type, timeout=timeout)
    accrued = int(sprint_before.get("accrued", 0) or 0)
    remaining = max(0, int(target_spend) - accrued)

    spend_result: dict = {"upgraded": 0, "spent": 0, "stopped": "no_spend_needed"}
    if remaining > 0:
        spend_result = relic.spend_to_target(
            client, tracker, target_spend=remaining, timeout=timeout)
    else:
        logger.info("ws_token relic_sprint: accrued=%s >= target=%s — claim only",
                    accrued, target_spend)

    # Re-read so freshly-crossed round thresholds show CanGet, then claim them.
    sprint_after = read_sprint(client, act_type, timeout=timeout)
    claimed: list[int] = []
    if sprint_after.get("open"):
        for sgid in sprint_after.get("claimable_rounds", []):
            res = claim_round(client, act_type, sgid, timeout=timeout)
            if res.success:
                claimed.append(sgid)

    logger.info(
        "ws_token relic_sprint: act_type=%s spent=%s claimed=%s (accrued %s->%s)",
        act_type, spend_result.get("spent"), claimed,
        accrued, sprint_after.get("accrued"),
    )
    return {
        "act_type": act_type,
        "spent": spend_result.get("spent", 0),
        "spend_stopped": spend_result.get("stopped"),
        "frag_unknown": spend_result.get("frag_unknown", False),
        "claimed_rounds": claimed,
        "sprint_before": sprint_before,
        "sprint_after": sprint_after,
    }

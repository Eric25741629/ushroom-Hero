"""遺物碎片衝刺 (Relic Sprint / 衝刺榜) — pure-WS read + claim over act2 (25 / 0x19).

The "遺物碎片衝刺" is one rotation of the generic cross-server limited rank
("衝刺榜 / RankRush / cross_limited_rank") activity framework, gated to the relic
養成 system: spending 遺物碎片 (item 100022, currency 7) accrues server-side, and
reaching round thresholds lets you claim per-round rewards.

🔑 LIVE-verified 2026-06-19 on 5556 (CDP 9223, act_type=269, group_id=2698). The
earlier "4 tasks / accrued=max(count)" recon was WRONG; the real structure is:

  * **32 tasks**, two kinds (distinguished by config ``is_stage``):
    - **28 non-stage milestone tasks** (is_stage=0), 7 per round × 4 rounds.
      Their ``count`` = the **cumulative 遺物碎片 spent** (one shared server counter,
      mirrored identically onto all 28). status flips to CanGet (1) once
      ``count >= condition[2]`` (that milestone's cumulative threshold). Reward is
      a token [gold 2:100, item 1002:5] — usually auto / not the prize.
    - **4 STAGE round tasks** (is_stage=1), one per ``small_group_id`` 1..4,
      ``condition=[]``. Their ``count`` = **how many of that round's 7 sub-tasks are
      done** (0..7); status flips to CanGet when count==7 (round fully cleared).
      THESE are the claimable rounds (big reward [1017:2000, 2:400, 1002:20]),
      claimed with ``send_25_144(act_type, small_group_id)`` = 6575 {act_type, sgid}.

  * Round cumulative thresholds (= last sub-task per round): 225K / 450K / 675K /
    900K (== ROUND_THRESHOLDS). Per-relic level-up cost at lv~88 is ~125K-140K
    fragments LIVE, so the full 900K sprint is only ~7 level-ups.

🔑 The spend itself is NOT submitted here. relic ``relic_level_up`` (0x1103)
deducts the fragments and the server folds that consumption straight into the
sprint ``count`` (LIVE: one level-up 88→89 cost 125290 → all 28 milestone counts
jumped 0→125290) — so the flow is: SPEND fragments by levelling relics
(:func:`ws_token.relic.spend_to_target`) → re-read the sprint → claim every
CanGet stage round. See docs/protocol/RELIC_SPRINT_RECON.md (LIVE recon).

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

# Structure (LIVE-verified for act 269; framework-identical across rotations).
SUBTASKS_PER_ROUND = 7   # non-stage milestone tasks per round (config is_stage=0)
NUM_ROUNDS = 4           # stage round tasks (config is_stage=1), small_group_id 1..4

# Round cumulative-spend thresholds (= the last sub-task's condition[2] per round),
# LIVE-read from configCross_limited_rank_task.condition. A round becomes
# claimable once cumulative spent >= its threshold (all 7 sub-tasks crossed).
ROUND_THRESHOLDS: tuple[int, ...] = (225_000, 450_000, 675_000, 900_000)
SPRINT_TOTAL = 900_000  # = ROUND_THRESHOLDS[-1]; full-sprint target spend

# Hard backstop on the number of relic level-ups one sprint run may perform.
# The full 900K sprint is only ~7 level-ups LIVE (per-level cost ~125K-140K at
# lv~88), so 30 is a generous ceiling. This protects against runaway spend even
# if the server ``accrued`` read goes wrong: spend_to_target is driven by the
# authoritative ``accrued`` gate, but max_steps is the fail-safe second bound —
# crucial because the 0x0402 fragment delta is unmeasurable when an upgrade reply
# omits item 100022 (frag_unknown), which is the common case.
MAX_SPRINT_UPGRADES = 30

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
    count: int         # #3 — non-stage: cumulative 遺物碎片 spent;
    #                          stage: number of that round's sub-tasks done (0..7)

    @property
    def can_get(self) -> bool:
        return self.status == STATUS_CAN_GET

    @property
    def had_get(self) -> bool:
        return self.status == STATUS_HAD_GET


@dataclass(frozen=True)
class SprintRound:
    """A claimable stage round (config is_stage=1)."""

    small_group_id: int   # 1..4 — the claim key (send_25_144 arg 2)
    task_id: int          # the stage task's id
    status: int           # 0 Normal / 1 CanGet / 2 HadGet
    done_subtasks: int    # stage task count == sub-tasks cleared in this round

    @property
    def can_get(self) -> bool:
        return self.status == STATUS_CAN_GET

    @property
    def had_get(self) -> bool:
        return self.status == STATUS_HAD_GET


@dataclass(frozen=True)
class Sprint:
    """A parsed act_cross_limited_rank_info_s2c snapshot.

    ``tasks`` keeps every raw task (in wire order); ``rounds`` is the derived list
    of the NUM_ROUNDS stage tasks (the claimable rounds), ordered by small_group_id.
    """

    open: bool                          # True iff a task_list came back
    act_type: int = 0                   # #1
    group_id: int = 0                   # #2
    tasks: tuple[SprintTask, ...] = ()  # #3 — all 32 raw tasks
    rounds: tuple[SprintRound, ...] = ()  # derived: the 4 stage rounds
    response_cmd: int = 0
    error_code: Optional[int] = None
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def accrued(self) -> int:
        """Cumulative 遺物碎片 already spent this sprint.

        This is the **non-stage** milestone count (one shared server counter
        mirrored onto all 28 sub-tasks). It is read as the max count among
        non-stage tasks; stage-task counts (tiny 0..7) are excluded, so this is
        robust even before the stage rounds are identified.
        """
        non_stage = [t.count for t in self.tasks
                     if t.count > SUBTASKS_PER_ROUND or not self._is_stage(t)]
        return max(non_stage, default=0)

    @property
    def claimable_rounds(self) -> tuple[int, ...]:
        """small_group_ids (1..4) of stage rounds that are CanGet (claim now)."""
        return tuple(r.small_group_id for r in self.rounds if r.can_get)

    def _is_stage(self, task: SprintTask) -> bool:
        return any(r.task_id == task.task_id for r in self.rounds)


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


@dataclass(frozen=True)
class CalWindow:
    """One act_cross_limit_rank_calendar entry: {act_type#1, begin#2, end#3}.

    begin/end are Unix seconds (LIVE 2026-06-19 小寶: clean UTC+8 midnight
    boundaries). The calendar lists every act_type's scheduled windows (past,
    current and upcoming), so a given act_type can appear more than once.
    """

    act_type: int  # #1
    begin: int     # #2 — window open  (Unix s)
    end: int       # #3 — window close (Unix s)

    def covers(self, now: int) -> bool:
        return self.begin <= now <= self.end


# --- helpers ----------------------------------------------------------------


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _parse_task(entry: bytes) -> SprintTask:
    d = codec.walk_dict(entry)
    return SprintTask(task_id=_as_int(d.get(1)), status=_as_int(d.get(2)),
                      count=_as_int(d.get(3)))


def _derive_rounds(tasks: tuple[SprintTask, ...]) -> tuple[SprintRound, ...]:
    """Identify the NUM_ROUNDS stage (round) tasks from the raw task list.

    Pure-WS has no config table, so stage tasks are identified structurally from
    the LIVE-verified layout: the task list is ``NUM_ROUNDS * SUBTASKS_PER_ROUND``
    non-stage milestones followed by ``NUM_ROUNDS`` stage tasks (ascending
    task_id; the 4 stage tasks are the highest task_ids). small_group_id is then
    their ordinal 1..NUM_ROUNDS by ascending task_id — matching config
    (LIVE: 269129→sgid1 .. 269132→sgid4).

    Returns () when the list doesn't match the expected size (wrong rotation /
    framework change) so callers can treat it as "no rounds" rather than guess.
    """
    expected = NUM_ROUNDS * SUBTASKS_PER_ROUND + NUM_ROUNDS
    if len(tasks) != expected:
        return ()
    stage = sorted(tasks, key=lambda t: t.task_id)[-NUM_ROUNDS:]
    return tuple(
        SprintRound(small_group_id=i, task_id=t.task_id, status=t.status,
                    done_subtasks=t.count)
        for i, t in enumerate(stage, start=1)
    )


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
        rounds=_derive_rounds(tasks),
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

    ``{open, act_type, group_id, accrued, claimable_rounds, rounds, tasks}`` where
    ``accrued`` is the cumulative 遺物碎片 spent, ``claimable_rounds`` is the list of
    CanGet stage small_group_ids, ``rounds`` describes all 4 stage rounds, and
    ``tasks`` is the raw list of ``{task_id, status, count}``. A closed /
    wrong-rotation act_type (no task_list, or a 0x0201) returns
    ``{"open": False, "act_type": act_type}``.
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
        "rounds": [{"small_group_id": r.small_group_id, "task_id": r.task_id,
                    "status": r.status, "done_subtasks": r.done_subtasks}
                   for r in sprint.rounds],
        "tasks": [{"task_id": t.task_id, "status": t.status, "count": t.count}
                  for t in sprint.tasks],
    }


def parse_calendar(cmd: int, body: bytes) -> tuple[CalWindow, ...]:
    """act_cross_limit_rank_calendar_s2c -> windows (repeated #1 {act#1,begin#2,end#3}).

    A 0x0201 / unexpected cmd yields () (treat as "no calendar"). LIVE-verified
    2026-06-19 (小寶): each entry is a 15-byte sub-message; begin/end are Unix s.
    """
    if cmd != CMD_SPRINT_CALENDAR:
        return ()
    out: list[CalWindow] = []
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            out.append(CalWindow(_as_int(d.get(1)), _as_int(d.get(2)),
                                  _as_int(d.get(3))))
    return tuple(out)


def read_calendar(client: WSGameClient, *,
                  timeout: Optional[float] = None) -> tuple[CalWindow, ...]:
    """Read the cross-limited-rank calendar (6576, empty body): all act windows."""
    cmd, reply = client.call_for(
        CMD_SPRINT_CALENDAR, b"",
        expect_cmds=(CMD_SPRINT_CALENDAR, CMD_ERROR), timeout=timeout)
    return parse_calendar(cmd, reply)


def active_window(windows: tuple[CalWindow, ...], act_type: int,
                  now: int) -> Optional[CalWindow]:
    """The window of ``act_type`` currently open (begin<=now<=end).

    The calendar can list the same act_type more than once (current + a future
    rotation); we keep only windows that actually cover ``now`` and, if several
    do, return the earliest-ending one (the live window, not a later rerun).
    Returns None when none is open right now.
    """
    cands = [w for w in windows if w.act_type == act_type and w.covers(now)]
    return min(cands, key=lambda w: w.end) if cands else None


def _current_accrued(client: WSGameClient, act_type: int, *,
                     timeout: Optional[float] = None) -> int:
    """Re-read the sprint and return the server-authoritative cumulative spend.

    Used as the ``should_stop`` driver for :func:`ws_token.relic.spend_to_target`:
    ``accrued`` is the 28-milestone shared counter the server folds each relic_up
    consumption into, so it is the ground truth for "how much have we spent this
    sprint" — reliable even when the 0x0402 inventory tracker never saw item
    100022. A failed / closed read returns 0 (don't stop on a transient hiccup;
    ``MAX_SPRINT_UPGRADES`` remains the hard backstop).
    """
    try:
        snapshot = read_sprint(client, act_type, timeout=timeout)
    except (WSTimeoutError, WSError):
        return 0
    return int(snapshot.get("accrued", 0) or 0)


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
      2. Read the sprint. If it is open but the stage ``rounds`` couldn't be derived
         (wrong rotation / framework change — ``_derive_rounds`` returned ()), bail
         with ``{"skipped": "protocol_mismatch"}`` WITHOUT spending: we can't tell
         which round to claim, so any fragments spent would be unrecoverable.
         Otherwise subtract progress already accrued so we only spend the remaining
         deficit (``remaining = max(0, target_spend - accrued)``). When
         ``remaining <= 0`` we DON'T level any relic — just claim CanGet rounds.
      3. :func:`ws_token.relic.spend_to_target` to consume ``remaining`` fragments.
         **Stop is driven by the server-authoritative sprint ``accrued``** (re-read
         after each relic_up via ``should_stop``), NOT the 0x0402 tracker delta —
         which is unmeasurable when an upgrade reply omits item 100022. A hard
         ``max_steps=MAX_SPRINT_UPGRADES`` is the fail-safe second bound so the run
         can never run away even if ``accrued`` reads go wrong.
      4. Re-read the sprint; :func:`claim_round` for every CanGet stage round
         (using each round's config-derived ``small_group_id``).

    Returns ``{act_type, spent, claimed_rounds, sprint_before, sprint_after}`` (or
    ``{"skipped": ...}`` when disabled / no active sprint / protocol_mismatch).
    """
    if not enabled:
        return {"skipped": "disabled"}

    act_type = find_active_act_type(client, timeout=timeout)
    if act_type is None:
        return {"skipped": "no active sprint"}

    sprint_before = read_sprint(client, act_type, timeout=timeout)
    # Guard: open but no derivable rounds → don't spend (can't claim → wasted).
    if sprint_before.get("open") and not sprint_before.get("rounds"):
        logger.warning(
            "ws_token relic_sprint: act_type=%s open 但 rounds 結構不符 — 不花碎片 "
            "(protocol_mismatch)", act_type)
        return {"skipped": "protocol_mismatch", "act_type": act_type,
                "sprint_before": sprint_before}

    accrued = int(sprint_before.get("accrued", 0) or 0)
    target = int(target_spend)
    remaining = max(0, target - accrued)

    spend_result: dict = {"upgraded": 0, "spent": 0, "stopped": "no_spend_needed"}
    if remaining > 0:
        # Authoritative stop: re-read the sprint accrued after each level-up and
        # stop the moment it reaches the target. Combined with MAX_SPRINT_UPGRADES
        # this bounds the spend even when the fragment count is unknown.
        def _reached_target() -> bool:
            return _current_accrued(client, act_type, timeout=timeout) >= target

        spend_result = relic.spend_to_target(
            client, tracker, target_spend=remaining,
            max_steps=MAX_SPRINT_UPGRADES,
            should_stop=_reached_target, timeout=timeout)
    else:
        logger.info("ws_token relic_sprint: accrued=%s >= target=%s — claim only",
                    accrued, target)

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

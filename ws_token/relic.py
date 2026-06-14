"""遺物 (relic) — pure-WS read + level-up (點數分配/強化) over the relic module.

遺物 is its OWN protocol module ``relic`` = **module 17 (0x11)**, NOT part of the
萬神試煉Beta roguelike (module 76). "Point allocation" for relics means **強化
(leveling)** each relic by spending 遺物碎片 (item 100022, currency id 7 — a
non-premium grind currency), which the server applies authoritatively.

LIVE-captured 2026-06-14 (小寶 7fe98fc6, CDP 9226); full recon in
docs/protocol/RELIC_ALLOC_RECON.md. Cross-checked vs docs/protocol/
TYPE_PROTO_SCHEMA.json (p_relic / p_relic_tab_info).

Cmd map (cmd = module*256 + sub)::

  relic_info     0x1101 (4353)  c2s {}                 -> s2c { relic_list#1: p_relic[] }
  relic_up       0x1103 (4355)  c2s { relic_uid#1:u64 } -> s2c { p_relic#1 }   (level+1)
  relic_tab_info 0x1105 (4357)  c2s {}                 -> s2c { tab#1, tab_list#2: p_relic_tab_info[] }

  p_relic { id#1:u64, cfg_id#2, type#3(quality), location#4(slot,0=unequipped), lv#5 }

**Verdict: pure-WS, server-authoritative.** ``relic_up`` c2s carries only the relic
uid; the server alone increments the level, deducts the cost, and replies with the
updated p_relic (LIVE: Lv.99->100, fragments -442080, exact cost). A forged WS
``relic_up`` is accepted — no client computation / seed / battle coupling.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# relic module 17 (0x11)
CMD_RELIC_INFO = 0x1101      # 4353  c2s {} -> s2c { relic_list }
CMD_RELIC_UP = 0x1103        # 4355  c2s { relic_uid } -> s2c { p_relic }
CMD_RELIC_TAB_INFO = 0x1105  # 4357  c2s {} -> s2c { tab, tab_list }
CMD_ERROR = 0x0201           # error.error_info_s2c { error_code#1 }

RELIC_LEVEL_CAP = 150        # configRelic max level per relic
RELIC_FRAGMENT_ITEM = 100022  # 遺物碎片 (currency id 7)


@dataclass(frozen=True)
class Relic:
    """One owned relic instance (p_relic)."""

    uid: int                 # f1 unique instance id
    cfg_id: int              # f2 catalog id (configRelic, 4001..)
    quality: int             # f3 type/quality 1..5
    location: int            # f4 equipped slot in active plan; 0 = unequipped
    level: int               # f5 current level 1..150

    @property
    def is_equipped(self) -> bool:
        return self.location > 0


@dataclass(frozen=True)
class RelicPlan:
    """One named loadout plan (p_relic_tab_info)."""

    tab: int                            # f1 plan index
    name: str                           # f2 plan name (e.g. '法師')
    positions: dict[int, int] = field(default_factory=dict)  # pos -> relic cfg_id


@dataclass(frozen=True)
class RelicInfo:
    """Parsed relic_info_s2c."""

    relics: tuple[Relic, ...] = ()
    response_cmd: int = 0
    error_code: int | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.response_cmd == CMD_RELIC_INFO

    @property
    def equipped(self) -> tuple[Relic, ...]:
        return tuple(r for r in self.relics if r.is_equipped)


@dataclass(frozen=True)
class RelicTabInfo:
    """Parsed relic_tab_info_s2c."""

    active_tab: int = 0
    plans: tuple[RelicPlan, ...] = ()
    response_cmd: int = 0


@dataclass(frozen=True)
class UpgradeResult:
    """Outcome of one relic_up (強化) step."""

    success: bool
    relic: Relic | None = None          # updated p_relic on success
    response_cmd: int = 0
    error_code: int | None = None
    error: str | None = None


# --- parsers ----------------------------------------------------------------


def _parse_relic(body: bytes) -> Relic | None:
    """Decode a p_relic { id#1, cfg_id#2, type#3, location#4, lv#5 }."""
    d = codec.walk_dict(body)
    uid = d.get(1)
    if not isinstance(uid, int):
        return None
    return Relic(
        uid=uid,
        cfg_id=int(d.get(2) or 0),
        quality=int(d.get(3) or 0),
        location=int(d.get(4) or 0),
        level=int(d.get(5) or 0),
    )


def parse_relic_info(cmd: int, body: bytes) -> RelicInfo:
    """relic_info_s2c { relic_list#1: p_relic[] }."""
    if cmd == CMD_RELIC_INFO:
        relics = []
        for fnum, v in codec.walk(body):
            if fnum == 1 and isinstance(v, (bytes, bytearray)):
                r = _parse_relic(bytes(v))
                if r is not None:
                    relics.append(r)
        return RelicInfo(relics=tuple(relics), response_cmd=cmd)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return RelicInfo(response_cmd=cmd, error_code=ec,
                         error=f"server error code={ec}")
    return RelicInfo(response_cmd=cmd, error=f"unexpected cmd 0x{cmd:04x}")


def parse_relic_tab_info(cmd: int, body: bytes) -> RelicTabInfo:
    """relic_tab_info_s2c { tab#1, tab_list#2: p_relic_tab_info[] }."""
    if cmd != CMD_RELIC_TAB_INFO:
        return RelicTabInfo(response_cmd=cmd)
    active_tab = 0
    plans: list[RelicPlan] = []
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, int):
            active_tab = v
        elif fnum == 2 and isinstance(v, (bytes, bytearray)):
            sub = codec.walk(bytes(v))
            tab = 0
            name = ""
            positions: dict[int, int] = {}
            for sfn, sv in sub:
                if sfn == 1 and isinstance(sv, int):
                    tab = sv
                elif sfn == 2 and isinstance(sv, (bytes, bytearray)):
                    name = bytes(sv).decode("utf-8", "replace")
                elif sfn == 3 and isinstance(sv, (bytes, bytearray)):
                    kv = codec.walk_dict(bytes(sv))
                    pos = kv.get(1)
                    cfg = kv.get(2)
                    if isinstance(pos, int) and isinstance(cfg, int):
                        positions[pos] = cfg
            plans.append(RelicPlan(tab=tab, name=name, positions=positions))
    return RelicTabInfo(active_tab=active_tab, plans=tuple(plans),
                        response_cmd=cmd)


def parse_upgrade(cmd: int, body: bytes) -> UpgradeResult:
    """relic_up_s2c { p_relic#1 } — the upgraded relic, or a 0x0201 error."""
    if cmd == CMD_RELIC_UP:
        relic = None
        for fnum, v in codec.walk(body):
            if fnum == 1 and isinstance(v, (bytes, bytearray)):
                relic = _parse_relic(bytes(v))
                break
        return UpgradeResult(success=True, relic=relic, response_cmd=cmd)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return UpgradeResult(success=False, response_cmd=cmd, error_code=ec,
                             error=f"server error code={ec}")
    return UpgradeResult(success=False, response_cmd=cmd,
                         error=f"unexpected cmd 0x{cmd:04x}")


# --- request body builders --------------------------------------------------


def build_relic_up_body(relic_uid: int) -> bytes:
    """relic_up_c2s { relic_uid#1:uint64 } — the ONLY field (server bumps lv+1)."""
    return codec.pb_uint(1, relic_uid)


# --- live calls -------------------------------------------------------------


def read_relics(client: WSGameClient, *,
                timeout: float | None = None) -> RelicInfo:
    """Pure-WS read of the relic inventory (relic_info 0x1101, empty body)."""
    cmd, body = client.call_for(
        CMD_RELIC_INFO, b"",
        expect_cmds=(CMD_RELIC_INFO, CMD_ERROR), timeout=timeout)
    return parse_relic_info(cmd, body)


def read_plans(client: WSGameClient, *,
               timeout: float | None = None) -> RelicTabInfo:
    """Pure-WS read of relic loadout plans (relic_tab_info 0x1105, empty body)."""
    cmd, body = client.call_for(
        CMD_RELIC_TAB_INFO, b"",
        expect_cmds=(CMD_RELIC_TAB_INFO, CMD_ERROR), timeout=timeout)
    return parse_relic_tab_info(cmd, body)


def upgrade_relic(client: WSGameClient, relic_uid: int, *,
                  timeout: float | None = None) -> UpgradeResult:
    """強化 one relic by +1 level (relic_up 0x1103). Server-authoritative.

    Replies on 0x1103 (updated p_relic) or 0x0201 (error, e.g. insufficient
    fragments / max level). Performs exactly ONE level-up step.
    """
    cmd, body = client.call_for(
        CMD_RELIC_UP, build_relic_up_body(relic_uid),
        expect_cmds=(CMD_RELIC_UP, CMD_ERROR), timeout=timeout)
    result = parse_upgrade(cmd, body)
    if result.success and result.relic is not None:
        logger.info("ws_token relic: upgraded uid=%s cfg=%s -> lv%s",
                     result.relic.uid, result.relic.cfg_id, result.relic.level)
    else:
        logger.warning("ws_token relic: upgrade uid=%s failed cmd=0x%04x code=%s",
                       relic_uid, cmd, result.error_code)
    return result


# --- "平均 / balanced" allocation strategy (pure) ---------------------------


def plan_balanced_upgrades(
    relics: list[tuple[int, int]],
    fragments: int,
    *,
    cost_at: Callable[[int], int],
    level_cap: int = RELIC_LEVEL_CAP,
    max_steps: Optional[int] = None,
) -> list[int]:
    """Plan an even ("平均") relic level allocation as a list of +1 steps.

    Strategy: repeatedly upgrade the **lowest-level** relic that is still below
    ``level_cap`` and affordable, deducting its step cost from ``fragments``.
    This minimises level variance across the equipped relics — the balanced /
    average distribution — and is fragment-efficient because per-level stat gain
    is ~constant while per-level cost is convex.

    Args:
        relics:    ``[(uid, current_level), ...]`` for the relics to balance
                   (typically the active plan's 7 equipped relics).
        fragments: available 遺物碎片 budget (currency 7).
        cost_at:   ``cost_at(level) -> int`` = fragments to upgrade FROM ``level``
                   to ``level+1`` (from configRelic for that relic).
        level_cap: per-relic max level (default 150).
        max_steps: optional hard cap on the number of +1 steps planned.

    Returns:
        An ordered list of relic uids; each occurrence is ONE +1 step. Feed it
        to :func:`upgrade_relic` one at a time (re-read between steps if desired,
        stop any time). Empty when nothing is affordable / all at cap.
    """
    # mutable working levels keyed by uid (preserve input order for tie-break)
    order = [uid for uid, _ in relics]
    level: dict[int, int] = {}
    for uid, lv in relics:
        level[uid] = lv  # last wins if duplicate uid given
    budget = int(fragments)
    steps: list[int] = []

    while True:
        if max_steps is not None and len(steps) >= max_steps:
            break
        # candidate = lowest-level, below cap, affordable; tie -> input order
        best_uid: Optional[int] = None
        best_level: Optional[int] = None
        best_cost = 0
        for uid in order:
            lv = level[uid]
            if lv >= level_cap:
                continue
            step_cost = int(cost_at(lv))
            if step_cost > budget:
                continue
            if best_level is None or lv < best_level:
                best_uid, best_level, best_cost = uid, lv, step_cost
        if best_uid is None:
            break
        steps.append(best_uid)
        level[best_uid] += 1
        budget -= best_cost

    return steps

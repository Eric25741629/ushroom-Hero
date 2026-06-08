"""League-solo chests over a logged-in WSGameClient — 烈焰山洞 + 魔法劇場.

One pure-WS task module covering two in-game features that share a single claim
cmd, differing only by ``type`` (live-verified mechanism):

  - 烈焰山洞 (Lava Cave / lieyanshandong) — daily:   type 1 (normal) / 2 (rare)
  - 魔法劇場 (Magic Theatre / 幻劇)        — weekly:  type 3 (normal) / 4 (rare)

Authoritative field numbers / cmd ids (verify against the exported schemas):
  docs/protocol/DUNGEON_PROTO_SCHEMA.json
    dungeon_league_solo_info       3598 0x0E0E  c2s {} -> s2c {box_list#1:p_league_solo_box[], record_list#2, reward_status#3}
    dungeon_league_solo_get_reward 3599 0x0E0F  c2s {type#1:uint32}  (the claim)
    dungeon_league_solo_update_box 3600 0x0E10  s2c {box_info#1:p_league_solo_box[]}  (success ack)
    dungeon_league_hard_info       3608 0x0E18  s2c {buff_id#1, state#2, ...}  -> NO box_list, so not needed here
  docs/protocol/TYPE_PROTO_SCHEMA.json
    p_league_solo_box {type#1, count#2, got_count#3, rare_offer_name#4}

All four box types live in solo_info.box_list (hard_info carries only buff/rank,
not boxes), so a single solo_info read drives every claim. ``claimable`` =
count > got_count; the server holds the real per-config chest_limit and answers
an over-claim with error.error_info_s2c (0x0201) error_code 159 — treated here
as "already claimed", skipped, never an abort. See docs/protocol/LIEYAN_CAVE.md.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_SOLO_INFO = 0x0E0E      # 3598 dungeon.dungeon_league_solo_info_c2s/s2c (empty body)
CMD_GET_REWARD = 0x0E0F     # 3599 dungeon.dungeon_league_solo_get_reward_c2s
CMD_UPDATE_BOX = 0x0E10     # 3600 dungeon.dungeon_league_solo_update_box_s2c (success ack)
CMD_ERROR = 0x0201          # error.error_info_s2c {error_code#1}
ERR_ALREADY_CLAIMED = 159   # got_count >= chest_limit (already claimed / maxed)

# type groups (lava = daily 1/2, theatre = weekly 3/4).
DAILY_TYPES: tuple[int, ...] = (1, 2)
WEEKLY_TYPES: tuple[int, ...] = (3, 4)
ALL_TYPES: tuple[int, ...] = (1, 2, 3, 4)

# Replies that mean the claim landed (server picks one of these per build).
_SUCCESS_CMDS: tuple[int, ...] = (CMD_UPDATE_BOX, CMD_GET_REWARD)


@dataclass(frozen=True)
class LeagueSoloBox:
    """One p_league_solo_box {type#1, count#2, got_count#3, rare_offer_name#4}."""

    type: int               # 1/2 daily (lava), 3/4 weekly (theatre)
    count: int              # boxes earned / available
    got_count: int          # boxes already claimed
    rare_offer_name: str = ""
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def claimable(self) -> bool:
        """Earned more than claimed. The server still enforces chest_limit."""
        return self.count > self.got_count


@dataclass(frozen=True)
class ClaimResult:
    type: int
    success: bool                  # True iff a get_reward/update_box reply arrived
    response_cmd: int
    fields: dict = field(compare=False, default_factory=dict)
    already_claimed: bool = False  # 0x0201 error_code == 159
    error_code: int | None = None
    error: str | None = None


# --- build c2s --------------------------------------------------------------

def build_get_reward_body(type_: int) -> bytes:
    """dungeon_league_solo_get_reward_c2s {type#1:uint32}."""
    return codec.pb_uint(1, type_)


# --- parse s2c --------------------------------------------------------------

def _parse_box(entry: bytes) -> LeagueSoloBox:
    d = codec.walk_dict(entry)
    return LeagueSoloBox(
        type=_as_int(d.get(1)),
        count=_as_int(d.get(2)),
        got_count=_as_int(d.get(3)),
        rare_offer_name=_as_str(d.get(4)),
        raw=d,
    )


def parse_solo_info(body: bytes) -> list[LeagueSoloBox]:
    """dungeon_league_solo_info_s2c: boxes are box_list (field 1, repeated)."""
    return [_parse_box(bytes(v)) for fnum, v in codec.walk(body)
            if fnum == 1 and isinstance(v, (bytes, bytearray))]


def parse_claim_result(cmd: int, body: bytes, *, type_: int) -> ClaimResult:
    """A get_reward reply is success (update_box 0x0E10 or the request cmd) or a
    0x0201 error; error_code 159 == already claimed (skip, not a failure)."""
    f = codec.walk_dict(body)
    if cmd in _SUCCESS_CMDS:
        return ClaimResult(type_, True, cmd, fields=f)
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        already = ec == ERR_ALREADY_CLAIMED
        return ClaimResult(type_, False, cmd, fields=f, already_claimed=already,
                           error_code=ec, error=f"server error code={ec}")
    return ClaimResult(type_, False, cmd, fields=f,
                       error=f"unexpected response cmd 0x{cmd:04x}")


# --- single-action orchestrators --------------------------------------------

def read_boxes(client: WSGameClient, *, timeout: float | None = None) -> list[LeagueSoloBox]:
    """Read every claimable box (types 1-4). All come from solo_info.box_list;
    hard_info carries no boxes, so a single solo_info request is enough."""
    return parse_solo_info(client.call(CMD_SOLO_INFO, b"", timeout=timeout))


def claim_box(client: WSGameClient, type_: int, *,
              timeout: float | None = None) -> ClaimResult:
    """Claim one box ``type``. Reply is update_box (0x0E10) / get_reward, or
    0x0201 (error; 159 == already claimed)."""
    cmd, body = client.call_for(
        CMD_GET_REWARD, build_get_reward_body(type_),
        expect_cmds=(CMD_UPDATE_BOX, CMD_GET_REWARD, CMD_ERROR), timeout=timeout)
    return parse_claim_result(cmd, body, type_=type_)


# --- batch orchestrator -----------------------------------------------------

def claim_available(client: WSGameClient, *, types: tuple[int, ...] | None = None,
                    spacing: float = 0.2) -> dict:
    """Read boxes and claim each claimable one (count > got_count).

    ``types`` limits which box types to consider (default all 1-4; pass
    ``DAILY_TYPES`` / ``WEEKLY_TYPES`` to do only lava or only theatre). Boxes
    that are not claimable are counted in ``skipped_maxed`` and never sent. A
    159 ("already claimed") reply is counted in ``already`` and does NOT abort
    the batch — every selected type is attempted.

    Returns ``{attempted, claimed, already, skipped_maxed, results}``.
    """
    wanted = set(ALL_TYPES if types is None else types)
    boxes = read_boxes(client)
    results: list[ClaimResult] = []
    attempted = claimed = already = skipped_maxed = 0
    for box in boxes:
        if box.type not in wanted:
            continue
        if not box.claimable:
            skipped_maxed += 1
            continue
        attempted += 1
        result = claim_box(client, box.type)
        results.append(result)
        if result.success:
            claimed += 1
        elif result.already_claimed:
            already += 1
        else:
            logger.warning("ws_token league_solo: claim type=%d failed code=%s",
                           box.type, result.error_code)
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token league_solo: attempted=%d claimed=%d already=%d skipped_maxed=%d",
                attempted, claimed, already, skipped_maxed)
    return {"attempted": attempted, "claimed": claimed, "already": already,
            "skipped_maxed": skipped_maxed, "results": results}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_opt_int(v) -> int | None:
    return int(v) if isinstance(v, int) else None


def _as_str(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(v).decode("utf-8", "replace")
    return "" if v is None else str(v)

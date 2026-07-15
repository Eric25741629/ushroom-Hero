"""抽卡 (gacha) — pure-WS skill/companion draw over the draw module.

Live-decoded 2026-06-15 (小寶 7fe98fc6, CDP 9226); see memory reference-gacha-
draw-protocol + tools/probe_gacha_live.py. Server-authoritative, no client compute.

Cmd map::

  draw   0x0902 (2306)  c2s { type#1, count#2 }
                        -> s2c { header#1, items#2[] }   (drawn rewards)
                        -> 0x0201 { error_code#1 } on reject (e.g. insufficient)

  type:  1 = 技能(skill), 2 = 同伴(companion)
  count: draws per request. The in-game buttons expose 15 / 35 / 999 bundles with
         FIXED ticket costs (15→15, 35→30, 999→800; 999 gets the bulk discount).
  ticket item_id (currency spent): 技能 = 1012, 同伴 = 1013. LIVE side-channel:
         each draw pushes 0x0402 carrying the ticket's NEW remaining qty.

This module owns the shared brain (ladder + cost + result parse) used by BOTH the
dashboard (web_h5 page) and this headless ws_token path. The headless ``run_gacha``
drains/fixed-draws via :class:`WSGameClient`, stepping the bundle ladder and
stopping on the server's immediate 0x0201 reject (no timeout-probing).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_INVENTORY_QUERY = 0x0401  # 1025  full bag snapshot
CMD_DRAW_INFO = 0x0901 # 2305  draw pool level/count snapshot
CMD_DRAW = 0x0902      # 2306  c2s {type,count} -> s2c {items} | 0x0201
CMD_FREE_DRAW = 0x1602 # 5634  c2s {slot#1, flag#3=1} -> server push 0x0902 | 0x0201
CMD_ERROR = 0x0201     # error.error_info_s2c { error_code#1 }

DRAW_TYPE_SKILL = 1
DRAW_TYPE_COMPANION = 2
DRAW_TYPE_NAME = {DRAW_TYPE_SKILL: "技能", DRAW_TYPE_COMPANION: "同伴"}

# Free ad-summon slot ids (0x1602 field#1).
SLOT_SKILL = 8       # 技能召喚
SLOT_COMPANION = 7   # 同伴召喚
# slot -> human name (mirrors DRAW_TYPE_NAME for logging)
SLOT_NAME = {SLOT_SKILL: "技能", SLOT_COMPANION: "同伴"}

# Server error code when the daily free-draw limit is exhausted.
_ERROR_FREE_LIMIT = 89

# In-game UI cap: 3 free 35-draws per slot per day.
_FREE_DRAW_DAILY_LIMIT = 3

# Draw-ticket currency item_id per type (LIVE 2026-06-15 via 0x0402 diff).
TICKET_ITEM = {DRAW_TYPE_SKILL: 1012, DRAW_TYPE_COMPANION: 1013}

# Bundle count -> ticket cost. 15/35 live-verified; 999 from the in-game button.
BUNDLE_COST = {15: 15, 35: 30, 999: 800}
# Largest-coin-efficient first (999 = 0.80 券/抽, 35 = 0.857, 15 = 1.0).
BUNDLE_LADDER: tuple[int, ...] = (999, 35, 15)
_MIN_COST = 15

# Runaway guards for 一鍵抽完.
_MAX_BUNDLES = 5000          # 5000 × 999 draws is far past any real balance
_DEFAULT_MAX_TOTAL = 1_000_000


def largest_affordable(remaining: int) -> Optional[int]:
    """Largest bundle count whose cost <= ``remaining`` (None if < 15)."""
    for cnt in BUNDLE_LADDER:
        if remaining >= BUNDLE_COST[cnt]:
            return cnt
    return None


@dataclass(frozen=True)
class GachaProbe:
    """抽卡前後的權威狀態，用來判斷 timeout 請求是否實際落地。"""

    ticket_count: int
    pool_count: int


def _parse_probe_value(body: bytes, key: int) -> int | None:
    """解析 repeated field#1 中指定 id 的 field#3 數值。"""
    for fnum, val in codec.walk(body):
        if fnum != 1 or not isinstance(val, (bytes, bytearray)):
            continue
        data = codec.walk_dict(bytes(val))
        if data.get(1) == key and isinstance(data.get(3), int):
            return int(data[3])
    return None


def read_probe(client: WSGameClient, draw_type: int, *,
               timeout: float | None = None) -> GachaProbe | None:
    """讀取抽券現量與對應抽池累計 count；任一缺失即回 ``None``。"""
    ticket_id = TICKET_ITEM.get(draw_type)
    if ticket_id is None:
        return None
    bag_cmd, bag = client.call_for(
        CMD_INVENTORY_QUERY, b"",
        expect_cmds=(CMD_INVENTORY_QUERY,), timeout=timeout)
    info_cmd, info = client.call_for(
        CMD_DRAW_INFO, b"",
        expect_cmds=(CMD_DRAW_INFO,), timeout=timeout)
    if bag_cmd != CMD_INVENTORY_QUERY or info_cmd != CMD_DRAW_INFO:
        return None
    tickets = _parse_probe_value(bag, ticket_id)
    pool_count = _parse_probe_value(info, draw_type)
    if tickets is None or pool_count is None:
        return None
    return GachaProbe(ticket_count=tickets, pool_count=pool_count)


def compare_probe(before: GachaProbe, after: GachaProbe, count: int) -> str:
    """比較 mutation 前後狀態：``landed`` / ``not_landed`` / ``conflict``。"""
    ticket_delta = before.ticket_count - after.ticket_count
    pool_delta = after.pool_count - before.pool_count
    if ticket_delta == BUNDLE_COST[count] and pool_delta == count:
        return "landed"
    if ticket_delta == 0 and pool_delta == 0:
        return "not_landed"
    return "conflict"


def build_draw_body(draw_type: int, count: int) -> bytes:
    """draw_c2s { type#1, count#2 } — two varints."""
    return codec.pb_uint(1, draw_type) + codec.pb_uint(2, count)


@dataclass(frozen=True)
class DrawResult:
    """Outcome of one 0x0902 draw call."""

    success: bool
    drawn: int = 0                 # #(top-level field-2 groups) = items drawn
    response_cmd: int = 0
    error_code: int | None = None

    @property
    def rejected(self) -> bool:
        return self.response_cmd == CMD_ERROR


def parse_draw_result(cmd: int, body: bytes) -> DrawResult:
    """draw_s2c { header#1, items#2[] } -> drawn = #items; or 0x0201 error."""
    if cmd == CMD_DRAW:
        n = 0
        for fnum, v in codec.walk(body):
            if fnum == 2 and isinstance(v, (bytes, bytearray)):
                n += 1
        return DrawResult(success=True, drawn=n, response_cmd=cmd)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return DrawResult(success=False, response_cmd=cmd,
                          error_code=int(ec) if isinstance(ec, int) else None)
    return DrawResult(success=False, response_cmd=cmd)


def draw_once(client: WSGameClient, draw_type: int, count: int, *,
              timeout: float | None = None) -> DrawResult:
    """Send one 0x0902 {type,count}; reply on 0x0902 (result) or 0x0201 (reject)."""
    cmd, body = client.call_for(
        CMD_DRAW, build_draw_body(draw_type, count),
        expect_cmds=(CMD_DRAW, CMD_ERROR), timeout=timeout)
    return parse_draw_result(cmd, body)


@dataclass(frozen=True)
class GachaReport:
    """Outcome of one run_gacha pass."""

    draw_type: int
    type_name: str
    mode: str
    total_drawn: int = 0
    bundles: int = 0
    skipped: bool = False
    stopped_reason: str | None = None


def run_gacha(
    client: WSGameClient,
    inventory_tracker=None,
    *,
    enabled: bool = False,
    draw_type: int = DRAW_TYPE_SKILL,
    mode: str = "drain",
    count: int = 999,
    batches: int = 1,
    max_total: int = _DEFAULT_MAX_TOTAL,
    timeout: float | None = None,
) -> GachaReport:
    """Headless pure-WS gacha.

    mode ``drain``  : 一鍵抽完 — repeatedly draw the largest affordable bundle until
                      the server rejects the smallest (15) bundle. Seeds the ticket
                      budget from ``inventory_tracker`` (login 0x0402 snapshot) when
                      available to skip doomed leading attempts, and corrects against
                      the server's immediate 0x0201 reject.
    mode ``fixed``  : draw ``count`` (15/35/999) × ``batches`` times.

    Stops cleanly on 0x0201 (insufficient / invalid) — no timeout-probing.
    """
    name = DRAW_TYPE_NAME.get(draw_type, str(draw_type))
    if not enabled:
        return GachaReport(draw_type, name, mode, skipped=True,
                           stopped_reason="disabled")
    if draw_type not in DRAW_TYPE_NAME:
        return GachaReport(draw_type, name, mode, stopped_reason=f"bad_type:{draw_type}")

    total = 0
    bundles = 0
    stopped: str | None = None

    if mode == "fixed":
        if count not in BUNDLE_COST:
            return GachaReport(draw_type, name, mode,
                               stopped_reason=f"bad_count:{count}")
        for _ in range(max(1, int(batches))):
            if total >= max_total:
                stopped = "max_total"
                break
            r = draw_once(client, draw_type, count, timeout=timeout)
            if not r.success or r.drawn <= 0:
                stopped = (f"reject:code={r.error_code}" if r.rejected
                           else f"empty:cmd=0x{r.response_cmd:04x}")
                break
            total += r.drawn
            bundles += 1
        logger.info("ws_token gacha[%s] fixed %s×%s -> drawn=%s (%s)",
                    name, count, batches, total, stopped or "done")
        return GachaReport(draw_type, name, mode, total, bundles,
                           stopped_reason=stopped)

    # --- drain (一鍵抽完) ---
    ticket_id = TICKET_ITEM.get(draw_type)
    remaining: Optional[int] = None
    if (inventory_tracker is not None and ticket_id is not None
            and inventory_tracker.has_item(ticket_id)):
        remaining = int(inventory_tracker.counts.get(ticket_id, 0))
    ladder_idx = 0  # used only when `remaining` is unknown (reject-driven)

    while True:
        if bundles >= _MAX_BUNDLES or total >= max_total:
            stopped = "max_total"
            break
        if remaining is not None:
            cnt = largest_affordable(remaining)
            if cnt is None:
                stopped = "exhausted"
                break
        else:
            if ladder_idx >= len(BUNDLE_LADDER):
                stopped = "exhausted"
                break
            cnt = BUNDLE_LADDER[ladder_idx]
        r = draw_once(client, draw_type, cnt, timeout=timeout)
        if not r.success or r.drawn <= 0:
            # Can't draw this bundle. Trust the server: step down a rung.
            if remaining is not None:
                remaining = BUNDLE_COST[cnt] - 1  # correct a stale estimate
            else:
                ladder_idx += 1
            continue
        total += r.drawn
        bundles += 1
        if remaining is not None:
            remaining -= BUNDLE_COST[cnt]
        # else: stay at the same (largest) rung until the server rejects it.

    logger.info("ws_token gacha[%s] drain -> drawn=%s bundles=%s (%s)",
                name, total, bundles, stopped or "done")
    return GachaReport(draw_type, name, mode, total, bundles,
                       stopped_reason=stopped)


# ---------------------------------------------------------------------------
# Free ad-summon (0x1602) — 3×35 free draws per slot per day, no real ad.
# ---------------------------------------------------------------------------

def free_draw_once(
    client: WSGameClient, slot: int, *, timeout: float | None = None
) -> DrawResult:
    """Send 0x1602 {slot#1, flag#3=1}; server responds with 0x0902 (draw) or 0x0201.

    Live-verified 2026-06-15 (emulator-5556): error code 89 = daily limit hit.
    """
    body = codec.pb_uint(1, slot) + codec.pb_uint(3, 1)
    cmd, reply = client.call_for(
        CMD_FREE_DRAW, body,
        expect_cmds=(CMD_DRAW, CMD_ERROR),
        timeout=timeout,
    )
    return parse_draw_result(cmd, reply)


def free_draw_slot(
    client: WSGameClient,
    slot: int,
    *,
    max_times: int = _FREE_DRAW_DAILY_LIMIT,
    timeout: float | None = None,
) -> dict:
    """Claim all available free draws for one slot.

    Loops until daily limit (error 89) or other error. Returns a summary dict.
    """
    name = SLOT_NAME.get(slot, str(slot))
    total = 0
    attempts = 0
    stopped = None
    for _ in range(max(1, max_times)):
        r = free_draw_once(client, slot, timeout=timeout)
        attempts += 1
        if r.success and r.drawn > 0:
            total += r.drawn
        else:
            if r.error_code == _ERROR_FREE_LIMIT:
                stopped = "daily_limit"
            else:
                stopped = f"error:{r.error_code}"
            break
    logger.info("ws_token gacha free[%s] slot=%s -> drawn=%s attempts=%s (%s)",
                name, slot, total, attempts, stopped or "done")
    return {"slot": slot, "name": name, "drawn": total, "stopped": stopped}


def free_draw_all(
    client: WSGameClient,
    *,
    slots: tuple[int, ...] = (SLOT_SKILL, SLOT_COMPANION),
    timeout: float | None = None,
) -> dict:
    """Claim all daily free draws for both skill and companion slots.

    Returns a dict keyed by slot name ("技能", "同伴") with per-slot summaries.
    """
    results: dict = {}
    for slot in slots:
        name = SLOT_NAME.get(slot, str(slot))
        results[name] = free_draw_slot(client, slot, timeout=timeout)
    total = sum(v["drawn"] for v in results.values())
    logger.info("ws_token gacha free_draw_all -> total=%s results=%s", total, results)
    return results

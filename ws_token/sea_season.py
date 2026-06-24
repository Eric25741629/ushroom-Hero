"""Pure-WS 賽季/航海 (sea season) daily tasks.

Protocol decoded live 2026-06-24 (CDP capture on 小寶/閃電).
See docs/protocol/SEA_SEASON_WS_RECON.md for field-level detail.

Sub-tasks (execution order — safe-first, dispatch last):
  1. claim_map_income    — 0x3c3b empty body, idempotent
  2. claim_season_tasks  — 0x180e list + 0x180f per claimable
  3. build_repair_station — 0x3c46 {wood}, dump all wood
  4. garrison_resource   — 0x3906 {1:1, 2:{gx,gy}}
  5. attack_relic        — 0x3906 {1:2, 2:{gx,gy}}

Dispatch (4-5) is night-gated (00:00-10:00 → server error 4030).
All others work 24h.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ws_token import codec
from ws_token import state as ws_state
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# --- CMD constants ---------------------------------------------------------

# module 57 (0x39) — 賽季地圖
CMD_SCAN = 0x3903
CMD_SCAN_REPLY = 0x3904
CMD_MARCH_RECORD = 0x3905
CMD_DISPATCH = 0x3906

# module 60 (0x3c) — 碼頭/收益/戰術/維修
CMD_MAP_INCOME_CLAIM = 0x3c3b
CMD_REPAIR_BUILD = 0x3c46
CMD_TACTIC_UPGRADE = 0x3c5b

# module 24 (0x18) — 任務
CMD_TASK_LIST = 0x180e
CMD_TASK_CLAIM = 0x180f

CMD_ERROR = 0x0201
CMD_INVENTORY = 0x0402

SEASON_TASK_CATEGORY = 109

# building types (0x3904 cell.field4.field2)
BT_BASE = 5
BT_RESOURCE = 7
BT_EMPIRE = 20
BT_REMAIN = 27
BT_TOTEM = 28

ACTION_ATTACK = 1
ACTION_GARRISON = 2

ERR_NIGHT = 4030
ERR_INVALID = 295

# 木材 item ID
ITEM_WOOD = 270005

# 小地圖固定遺跡座標(S4,每周不變)
RELIC_BOTTOM = (16, 25)
RELIC_TOP = (16, 4)
# 四角大本營座標
CORNERS = {1: (2, 3), 2: (30, 3), 3: (2, 26), 4: (30, 26)}

# --- dataclasses -----------------------------------------------------------


@dataclass(frozen=True)
class MapCell:
    cell_id: int
    building_type: int  # 7=resource, 27=relic, 5=base ...


@dataclass(frozen=True)
class MarchRecord:
    from_x: int
    from_y: int
    to_x: int
    to_y: int


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    error_code: int = 0
    night_blocked: bool = False
    march: Optional[MarchRecord] = None


# --- body builders --------------------------------------------------------


def _grid_body(gx: int, gy: int) -> bytes:
    return codec.pb_uint(1, gx) + codec.pb_uint(2, gy)


def build_scan(gx: int, gy: int) -> bytes:
    return codec.pb_msg(1, _grid_body(gx, gy)) + codec.pb_uint(2, 1)


def build_dispatch(action: int, gx: int, gy: int) -> bytes:
    body = codec.pb_uint(1, action) + codec.pb_msg(2, _grid_body(gx, gy))
    if action == ACTION_ATTACK:
        body += codec.pb_uint(3, 1) + codec.pb_uint(4, 1)
    else:
        body += codec.pb_uint(4, 0)
    return body


def build_task_list(category: int) -> bytes:
    return codec.pb_uint(1, category)


def build_task_claim(category: int, task_id: int, group_id: int) -> bytes:
    return (codec.pb_uint(1, category)
            + codec.pb_uint(2, task_id)
            + codec.pb_uint(3, group_id))


def build_repair(wood: int) -> bytes:
    return codec.pb_uint(1, wood)


def build_tactic(node_id: int) -> bytes:
    return codec.pb_uint(1, node_id)


# --- parsers --------------------------------------------------------------

def _int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def parse_scan_cells(body: bytes) -> list[MapCell]:
    """Parse 0x3904: repeated field1 = cell submessage."""
    cells: list[MapCell] = []
    for fn, val in codec.walk(body):
        if fn == 1 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            cid = _int(d.get(1))
            occ = d.get(4)
            bt = 0
            if isinstance(occ, (bytes, bytearray)):
                bt = _int(codec.walk_dict(bytes(occ)).get(2))
            cells.append(MapCell(cell_id=cid, building_type=bt))
    return cells


def parse_march(body: bytes) -> MarchRecord:
    """Parse 0x3905: {6:{1:fromX,2:fromY}, 7:{1:toX,2:toY}}."""
    d = codec.walk_dict(body)
    fg = codec.walk_dict(bytes(d.get(6, b""))) if isinstance(d.get(6), (bytes, bytearray)) else {}
    tg = codec.walk_dict(bytes(d.get(7, b""))) if isinstance(d.get(7), (bytes, bytearray)) else {}
    return MarchRecord(
        from_x=_int(fg.get(1)), from_y=_int(fg.get(2)),
        to_x=_int(tg.get(1)), to_y=_int(tg.get(2)),
    )


def parse_task_list(body: bytes) -> list[tuple[int, int, int]]:
    """Parse 0x180e s2c → list of (taskId, groupId, claimFlag).

    claimFlag: 1=claimable, 2=claimed, 0=not ready.
    """
    out: list[tuple[int, int, int]] = []
    for fn, val in codec.walk(body):
        if fn == 2 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            out.append((_int(d.get(1)), _int(d.get(2)), _int(d.get(4))))
    return out


# --- orchestrators --------------------------------------------------------

def scan_map(client: WSGameClient, gx: int, gy: int) -> list[MapCell]:
    """Send 0x3903 scan, return cells from 0x3904 reply."""
    try:
        rc, rb = client.call_for(
            CMD_SCAN, build_scan(gx, gy),
            expect_cmds=(CMD_SCAN_REPLY, CMD_ERROR), timeout=8)
    except WSTimeoutError:
        return []
    if rc == CMD_ERROR:
        return []
    return parse_scan_cells(rb)


def _try_dispatch(client: WSGameClient, action: int,
                  gx: int, gy: int) -> DispatchResult:
    act_label = "attack" if action == ACTION_ATTACK else "garrison"
    logger.info("[sea_ws] dispatch %s to (%d,%d) body=%s",
                act_label, gx, gy, build_dispatch(action, gx, gy).hex())
    rc, rb = client.call_for(
        CMD_DISPATCH, build_dispatch(action, gx, gy),
        expect_cmds=(CMD_MARCH_RECORD, CMD_ERROR), timeout=10)
    if rc == CMD_ERROR:
        code = _int(codec.walk_dict(rb).get(1))
        logger.info("[sea_ws] dispatch %s (%d,%d) -> error %d%s",
                    act_label, gx, gy, code,
                    " (night)" if code == ERR_NIGHT else "")
        return DispatchResult(ok=False, error_code=code,
                              night_blocked=(code == ERR_NIGHT))
    march = parse_march(rb)
    logger.info("[sea_ws] dispatch %s (%d,%d) -> OK, march from=(%d,%d) to=(%d,%d)",
                act_label, gx, gy, march.from_x, march.from_y, march.to_x, march.to_y)
    return DispatchResult(ok=True, march=march)


def nearest_relic(home_y: int) -> tuple[int, int]:
    """Pick the nearest relic based on home Y position."""
    return RELIC_BOTTOM if home_y >= 14 else RELIC_TOP


def find_garrison_target(client: WSGameClient, home_x: int, home_y: int,
                         *, radius: int = 6) -> Optional[DispatchResult]:
    """Scan near home, find a resource, try garrison dispatch.

    Scans in a cross pattern, then tries dispatch (action=2) on each scan
    center that contains a resource. The server validates adjacency.
    """
    scan_pts = [(home_x, home_y)]
    for d in range(-radius, radius + 1, 3):
        if d:
            scan_pts.append((home_x + d, home_y))
            scan_pts.append((home_x, home_y + d))

    for sx, sy in scan_pts:
        cells = scan_map(client, sx, sy)
        if not any(c.building_type == BT_RESOURCE for c in cells):
            time.sleep(0.12)
            continue
        # try dispatch at scan center + neighbors
        for dx, dy in [(0, 0), (0, 1), (0, -1), (1, 0), (-1, 0),
                       (2, 0), (-2, 0), (0, 2), (0, -2)]:
            r = _try_dispatch(client, ACTION_GARRISON, sx + dx, sy + dy)
            if r.ok or r.night_blocked:
                return r
            time.sleep(0.08)
        time.sleep(0.12)
    return None


# --- high-level tasks -----------------------------------------------------

def claim_map_income(client: WSGameClient) -> dict:
    """0x3c3b empty body → claim all banked map income."""
    try:
        rc, _ = client.call_for(
            CMD_MAP_INCOME_CLAIM, b"",
            expect_cmds=(CMD_MAP_INCOME_CLAIM, CMD_ERROR, CMD_INVENTORY),
            timeout=5)
        return {"ok": rc != CMD_ERROR}
    except WSTimeoutError:
        return {"ok": True}


def claim_season_tasks(client: WSGameClient,
                       category: int = SEASON_TASK_CATEGORY) -> dict:
    """List + claim all claimable season tasks."""
    try:
        body = client.call(CMD_TASK_LIST, build_task_list(category), timeout=6)
    except WSTimeoutError:
        return {"ok": False, "error": "list timeout"}
    tasks = parse_task_list(body)
    claimable = [(tid, gid) for tid, gid, flag in tasks if flag == 1]
    claimed = 0
    for tid, gid in claimable:
        try:
            rc, _ = client.call_for(
                CMD_TASK_CLAIM, build_task_claim(category, tid, gid),
                expect_cmds=(CMD_TASK_CLAIM, CMD_ERROR), timeout=5)
            if rc == CMD_TASK_CLAIM:
                claimed += 1
        except WSTimeoutError:
            pass
        time.sleep(0.15)
    return {"ok": True, "claimed": claimed,
            "claimable": len(claimable), "total": len(tasks)}


def build_repair_station(client: WSGameClient, wood: int) -> dict:
    """0x3c46 {1:wood} → dump wood into repair station."""
    if wood <= 0:
        return {"skipped": "no wood"}
    try:
        rc, rb = client.call_for(
            CMD_REPAIR_BUILD, build_repair(wood),
            expect_cmds=(CMD_REPAIR_BUILD, CMD_ERROR), timeout=6)
    except WSTimeoutError:
        return {"ok": False, "error": "timeout"}
    if rc == CMD_ERROR:
        return {"ok": False, "error_code": _int(codec.walk_dict(rb).get(1))}
    d = codec.walk_dict(rb)
    return {"ok": True, "new_level": _int(d.get(1)),
            "new_progress": _int(d.get(2)), "wood_spent": wood}


def upgrade_tactic(client: WSGameClient, node_id: int) -> dict:
    """0x3c5b {1:nodeId} → upgrade one tactic node (costs 風暴幣)."""
    try:
        rc, rb = client.call_for(
            CMD_TACTIC_UPGRADE, build_tactic(node_id),
            expect_cmds=(CMD_TACTIC_UPGRADE, CMD_ERROR), timeout=6)
    except WSTimeoutError:
        return {"ok": False, "node_id": node_id, "error": "timeout"}
    if rc == CMD_ERROR:
        return {"ok": False, "node_id": node_id,
                "error_code": _int(codec.walk_dict(rb).get(1))}
    d = codec.walk_dict(rb)
    inner = d.get(1)
    if isinstance(inner, (bytes, bytearray)):
        nd = codec.walk_dict(bytes(inner))
        return {"ok": True, "node_id": _int(nd.get(1)),
                "new_level": _int(nd.get(2))}
    return {"ok": True, "node_id": node_id}


def _load_home(device: str) -> Optional[tuple[int, int]]:
    st = ws_state.load_state(device)
    sea = st.get("sea_season") or {}
    hx, hy = sea.get("home_x"), sea.get("home_y")
    if hx is not None and hy is not None:
        return (int(hx), int(hy))
    return None


def _save_home(device: str, hx: int, hy: int) -> None:
    st = ws_state.load_state(device)
    sea = st.setdefault("sea_season", {})
    sea["home_x"] = hx
    sea["home_y"] = hy
    ws_state.save_state(device, st)


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _load_attack_count(device: str) -> int:
    st = ws_state.load_state(device)
    sea = st.get("sea_season") or {}
    if sea.get("attack_date") != _today():
        return 0
    return int(sea.get("attack_count", 0))


def _save_attack_count(device: str, count: int) -> None:
    st = ws_state.load_state(device)
    sea = st.setdefault("sea_season", {})
    sea["attack_date"] = _today()
    sea["attack_count"] = count
    ws_state.save_state(device, st)


# --- main entry -----------------------------------------------------------

def run_sea_season(
    client: WSGameClient,
    *,
    device: str = "",
    do_dispatch: bool = True,
    do_repair: bool = True,
    tactic_nodes: Optional[list[int]] = None,
    wood_amount: int = 0,
    home_grid: Optional[tuple[int, int]] = None,
    relic_grid: Optional[tuple[int, int]] = None,
    garrison_grid: Optional[tuple[int, int]] = None,
    attack_daily_max: int = 4,
    scan_radius: int = 6,
) -> dict:
    """Run all sea/season pure-WS tasks.

    Order: dispatch first (garrison → attack), then claim/repair.
    Dispatch is time-gated (00:00-10:00 → 4030); claims work 24h.
    """
    summary: dict = {}

    # --- 1-2. dispatch (garrison + attack) first ---
    if do_dispatch:
        hx, hy = home_grid or (0, 0)
        if not (hx and hy) and device:
            persisted = _load_home(device)
            if persisted:
                hx, hy = persisted
        summary["home_grid"] = (hx, hy) if (hx and hy) else None

        if hx and hy:
            # 5. garrison a resource (scan nearby, try dispatch)
            if garrison_grid:
                try:
                    g = _try_dispatch(client, ACTION_GARRISON, *garrison_grid)
                    summary["garrison"] = {
                        "ok": g.ok, "night_blocked": g.night_blocked,
                        "error_code": g.error_code,
                    }
                except Exception as exc:
                    summary["garrison"] = {"ok": False, "error": str(exc)}
            else:
                try:
                    g = find_garrison_target(client, hx, hy, radius=scan_radius)
                    if g:
                        summary["garrison"] = {
                            "ok": g.ok, "night_blocked": g.night_blocked,
                            "error_code": g.error_code,
                        }
                    else:
                        summary["garrison"] = {"ok": False, "error": "no reachable resource found"}
                except Exception as exc:
                    summary["garrison"] = {"ok": False, "error": str(exc)}
            logger.info("[sea_ws] garrison: %s", summary.get("garrison"))

            # 6. attack relic (fixed position derived from corner, daily cap)
            done_today = _load_attack_count(device) if device else 0
            if done_today >= attack_daily_max:
                summary["attack"] = {"skipped": "daily max %d reached (%d)" % (attack_daily_max, done_today)}
                logger.info("[sea_ws] attack skipped: %d/%d today", done_today, attack_daily_max)
            else:
                relic = relic_grid or nearest_relic(hy)
                try:
                    a = _try_dispatch(client, ACTION_ATTACK, *relic)
                    summary["attack"] = {
                        "ok": a.ok, "night_blocked": a.night_blocked,
                        "error_code": a.error_code,
                        "relic": relic,
                    }
                    if a.ok and device:
                        _save_attack_count(device, done_today + 1)
                        summary["attack"]["count"] = done_today + 1
                except Exception as exc:
                    summary["attack"] = {"ok": False, "error": str(exc)}
                logger.info("[sea_ws] attack: %s", summary.get("attack"))
        else:
            summary["garrison"] = {"skipped": "home_grid unknown"}
            summary["attack"] = {"skipped": "home_grid unknown"}
            logger.warning("[sea_ws] dispatch skipped: home_grid unknown for %s", device)

    # --- 3. claim map income ---
    try:
        summary["map_income"] = claim_map_income(client)
        logger.info("[sea_ws] map income: %s", summary["map_income"])
    except Exception as exc:
        summary["map_income"] = {"ok": False, "error": str(exc)}

    # --- 4. claim season tasks ---
    try:
        summary["tasks"] = claim_season_tasks(client)
        logger.info("[sea_ws] tasks: %s", summary["tasks"])
    except Exception as exc:
        summary["tasks"] = {"ok": False, "error": str(exc)}

    # --- 5. repair station ---
    if do_repair and wood_amount > 0:
        try:
            summary["repair"] = build_repair_station(client, wood_amount)
            logger.info("[sea_ws] repair: %s", summary["repair"])
        except Exception as exc:
            summary["repair"] = {"ok": False, "error": str(exc)}

    # --- 6. tactic upgrades ---
    if tactic_nodes:
        results = []
        for nid in tactic_nodes:
            try:
                r = upgrade_tactic(client, nid)
                results.append(r)
                if not r.get("ok"):
                    break
            except Exception as exc:
                results.append({"ok": False, "node_id": nid, "error": str(exc)})
                break
            time.sleep(0.15)
        summary["tactic"] = results

    return summary

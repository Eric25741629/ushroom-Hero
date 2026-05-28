"""菇菇車位 — 本服+跨界全 lot 槽位追蹤 (snapshot diff for kick-out detection).

Use case: monitor all SILVER lots (鉑銀1..鉑銀30 = 30 lots × 10 spots = 300
spots). Periodically snapshot each lot's occupancy via the UI flow that
the game itself uses (click lot → server returns space_list). Compare
consecutive snapshots: if a spot that was occupied by player X becomes
empty (or occupied by Y), X was kicked out (打掉) or replaced.

Why scan via UI clicks instead of戰報 cmds:
- 戰報 only shows ME-as-attacker / ME-as-defender events. Other players'
  fights don't appear.
- Scanning each lot directly is fast (~0.5–1s per lot × 30 lots = ~30s)
  and gives ground-truth occupancy.

Why we trust the data this time (not the stale label trap):
- Each click of a lot triggers `car_park.car_park_info_c2s` with that lot's
  master_id+ceng. Server returns `car_park_info_s2c` with FRESH `space_list`.
- The view's underlying data cache is updated from that response BEFORE
  the rendering pass that copies into the (virtualized, label-stale) UIList.
- So we read from the data cache directly (not buildingRoot1 Labels) —
  the cache holds the truthful per-spot state.

Persistence: snapshots go to `logs/<device>/carpark_snapshots.jsonl` (one
per scan). Each line:
{
  "ts": 1779300000,
  "lot_idx": 29,        // 0-based, 29 = 鉑銀30
  "spots": [
    {"pos": 1, "role_id": 89..., "name": "...", "mount_id": 1011, "expiry": 1779.. },
    {"pos": 2, "role_id": null, "name": null},   // empty
    ...
  ]
}

Then a diff pass loads two snapshots and reports kick-outs.

THIS MODULE IS SCAFFOLDED — the snapshot capture works conceptually but
needs ParkingDataCache access to read space_list cleanly. For now it
reads the rendered cells via stale-aware logic (only occupied/empty,
no player ID).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import carpark_state
from utils.carpark_state import LotSnapshot, get_current_lot_snapshot

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────
# Data model
# ───────────────────────────────────────────────────────────────────


@dataclass
class TrackedSpot:
    pos: int                       # 1..10 (matches building<N> name)
    occupied: bool
    role_id: Optional[int] = None  # player_id (None if empty or not yet decoded)
    name: Optional[str] = None     # e.g. "[s1467]xxx" (None if empty)
    mount_id: Optional[int] = None
    expiry: Optional[int] = None   # unix seconds
    raw_timer: Optional[str] = None  # forensics: timer label string


@dataclass
class LotScan:
    ts: int                         # unix seconds at scan time
    pool: str                       # "silver" | "gold" | …
    lot_idx: int                    # 0-based; 29 = 鉑銀30
    lot_id_internal: int            # 5..34 for silver
    master_id: Optional[int]        # whose lot this is
    spots: List[TrackedSpot] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "ts": self.ts, "pool": self.pool, "lot_idx": self.lot_idx,
            "lot_id_internal": self.lot_id_internal, "master_id": self.master_id,
            "spots": [{"pos": s.pos, "occupied": s.occupied,
                      "role_id": s.role_id, "name": s.name,
                      "mount_id": s.mount_id, "expiry": s.expiry,
                      "raw_timer": s.raw_timer} for s in self.spots],
        }


# ───────────────────────────────────────────────────────────────────
# JS helpers: read ParkingDataCache for fresh space_list
# ───────────────────────────────────────────────────────────────────


_READ_SPACE_LIST_JS = r"""
() => {
  // ParkingDataCache singleton: find via the netManager listener target
  // (we know on_car_park_info_s2c writes to IS(P).space_list).
  // Easier: scan all callbackTable targets for one with space_list field.
  const cbt = window.netManager._events._callbackTable;
  let cache = null;
  for (const k of Object.keys(cbt)) {
    if (!k.includes('car_park')) continue;
    const list = cbt[k];
    if (!list || !list.callbackInfos) continue;
    for (const ci of list.callbackInfos) {
      const t = ci.target;
      // Heuristic: target has both type & space_list & master_id fields.
      // (ParkingControl proto doesn't, ParkingDataCache does.)
      if (t && t.space_list !== undefined && t.type !== undefined && t.master_id !== undefined) {
        cache = t; break;
      }
    }
    if (cache) break;
  }
  if (!cache) {
    // Fallback: walk uiMgr open views for one with space_list refs
    const v = window.uiMgr && window.uiMgr.getView && window.uiMgr.getView('ParkingMainView');
    if (v) {
      const visited = new WeakSet();
      const walk = (obj, d) => {
        if (!obj || typeof obj !== 'object' || d > 4) return null;
        if (visited.has(obj)) return null;
        visited.add(obj);
        if (obj.space_list !== undefined && obj.type !== undefined) return obj;
        for (const k of Object.keys(obj)) {
          try { const r = walk(obj[k], d+1); if (r) return r; } catch(e){}
        }
        return null;
      };
      cache = walk(v, 0);
    }
  }
  if (!cache) return {err: 'cache not found'};
  // Snapshot the space_list — each entry typically has {pos, role_id, role_name?, mount_id, end_time, ...}
  const sl = cache.space_list || [];
  return {
    type: cache.type,
    master_id: cache.master_id,
    ceng: cache.ceng,
    name: cache.name,
    space_count: sl.length,
    space_list: sl.map(s => {
      if (!s) return null;
      // Each spot's shape is unknown — dump common fields + JSON-ish dump
      try { return JSON.parse(JSON.stringify(s)); } catch(e) { return Object.keys(s); }
    }),
  };
}
"""


def read_current_lot_data(page: Any) -> Optional[dict]:
    """Read the ParkingDataCache's space_list for the currently-loaded lot.

    This is the FRESH server data — not the stale labels. Returns None if
    cache can't be located.
    """
    try:
        result = page.evaluate(_READ_SPACE_LIST_JS)
    except Exception as e:
        logger.debug(f"[carpark_tracker] cache read failed: {e}")
        return None
    if isinstance(result, dict) and result.get("err"):
        logger.debug(f"[carpark_tracker] cache: {result['err']}")
        return None
    return result


# ───────────────────────────────────────────────────────────────────
# Snapshot capture
# ───────────────────────────────────────────────────────────────────


def snapshot_current_lot(page: Any, pool: str, lot_idx: int,
                         lot_id_internal: int) -> Optional[LotScan]:
    """Capture a snapshot of the CURRENTLY-LOADED lot.

    Requires that the caller has already navigated to this lot (clicked
    it via carpark_auto._click_silver_lot_by_idx or equivalent). The
    function will read the cache directly + fall back to the UI rendering
    (with stale-aware logic) if cache isn't accessible.
    """
    cache = read_current_lot_data(page)
    spots: List[TrackedSpot] = []
    master_id = None
    if cache and cache.get("space_list"):
        master_id = cache.get("master_id")
        for s in (cache["space_list"] or []):
            if not s:
                continue
            # Heuristic field mapping — verify with real captures over time
            pos = int(s.get("pos") if isinstance(s, dict) else 0) if s else 0
            role_id = s.get("role_id") if isinstance(s, dict) else None
            name = s.get("role_name") if isinstance(s, dict) else None
            mount_id = s.get("mount_id") if isinstance(s, dict) else None
            expiry = s.get("end_time") if isinstance(s, dict) else None
            spots.append(TrackedSpot(
                pos=pos, occupied=bool(role_id),
                role_id=int(role_id) if role_id else None,
                name=str(name) if name else None,
                mount_id=int(mount_id) if mount_id else None,
                expiry=int(expiry) if expiry else None,
            ))
    else:
        # Fallback: UI scan with stale-aware logic
        snap = get_current_lot_snapshot(page)
        if not snap:
            return None
        for s in snap.spots:
            spots.append(TrackedSpot(
                pos=s.slot_idx, occupied=s.occupied,
                # NOTE: stale_player_name MUST NOT be trusted as identity for
                # this spot — UIList reuse means it may be a previous occupant's
                # name. We capture it only for forensics.
                name=s.stale_player_name if s.occupied else None,
                raw_timer=s.stale_timer if s.occupied else None,
            ))
    return LotScan(
        ts=int(time.time()), pool=pool, lot_idx=lot_idx,
        lot_id_internal=lot_id_internal, master_id=master_id, spots=spots,
    )


def scan_all_silver_lots(page: Any, prefer_back: bool = True) -> List[LotScan]:
    """Iterate through all 30 SILVER lots, snapshot each.

    Returns one LotScan per lot. Order can be reversed via prefer_back.
    Total time ~30s (1s per lot click + server response).
    """
    return scan_pool(page, "silver", prefer_back=prefer_back)


# Per-pool lot count + internal id base (from configCross_parking_lot dump).
# These are observed live counts — pool dimensions can be re-verified by
# clicking the level cell + reading UIList _datas.length.
POOL_LAYOUT = {
    "diamond": {"lot_count": 1,  "id_base": 1},     # 曜鑽車座 1 lot × 10 spots
    "gold":    {"lot_count": 3,  "id_base": 2},     # 鎏金車座 3 lots × 10 spots
    "silver":  {"lot_count": 30, "id_base": 5},     # 泊銀車座 30 lots × 10 spots
    "bronze":  {"lot_count": 35, "id_base": 35},    # 灰銅車座 (estimated, verify)
    "server":  {"lot_count": 1,  "id_base": 5},     # 奇星車場 1 special pool
}


def scan_pool(page: Any, pool: str, prefer_back: bool = True) -> List[LotScan]:
    """Iterate through all lots of a given pool tier and snapshot each.

    Supported pools: 'diamond' (曜鑽), 'gold' (鎏金), 'silver' (泊銀),
    'bronze' (灰銅), 'server' (奇星 cross-server pool).
    """
    from utils.carpark_auto import (
        _click_silver_lot_by_idx, _ensure_parking_main_open,
        _open_space_view_and_cross_tab, _click_pool_tier, POOL_TYPE_TO_ID,
    )

    layout = POOL_LAYOUT.get(pool)
    if not layout:
        logger.warning(f"[carpark_tracker] unknown pool {pool!r}")
        return []
    pool_id = POOL_TYPE_TO_ID.get(pool)
    if pool_id is None:
        return []

    if not _ensure_parking_main_open(page):
        return []
    if not _open_space_view_and_cross_tab(page):
        return []
    if not _click_pool_tier(page, pool_id):
        return []

    out: List[LotScan] = []
    n = layout["lot_count"]
    order = list(range(n - 1, -1, -1)) if prefer_back else list(range(n))
    for idx in order:
        # _click_silver_lot_by_idx works for any pool since it uses scrollTo
        # on the detail UIList — name is misleading but logic is generic.
        if not _click_silver_lot_by_idx(page, idx):
            logger.debug(f"[carpark_tracker] skip {pool} lot {idx+1}: nav fail")
            continue
        scan = snapshot_current_lot(page, pool, idx, layout["id_base"] + idx)
        if scan:
            out.append(scan)
        if idx != order[-1]:
            if not _open_space_view_and_cross_tab(page):
                break
            if not _click_pool_tier(page, pool_id):
                break
    return out


# ───────────────────────────────────────────────────────────────────
# Persistence + diff
# ───────────────────────────────────────────────────────────────────


def persist_scans(scans: List[LotScan], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for s in scans:
            f.write(json.dumps(s.to_json(), ensure_ascii=False) + "\n")


def load_latest_scans(log_path: Path, max_lines: int = 500) -> List[dict]:
    if not log_path.exists():
        return []
    out: list[dict] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-max_lines:]


@dataclass
class KickOutEvent:
    """A formerly-occupied spot is now empty / different player."""
    ts_prev: int
    ts_curr: int
    pool: str
    lot_idx: int
    pos: int
    prev_name: Optional[str]
    prev_role_id: Optional[int]
    curr_name: Optional[str]
    curr_role_id: Optional[int]


def diff_scans(prev: LotScan, curr: LotScan) -> List[KickOutEvent]:
    """Compare two scans of the same lot. Return events where a spot's
    occupant changed (kicked out / replaced).
    """
    if prev.lot_idx != curr.lot_idx or prev.pool != curr.pool:
        return []
    events: list[KickOutEvent] = []
    prev_by_pos = {s.pos: s for s in prev.spots if s.pos}
    curr_by_pos = {s.pos: s for s in curr.spots if s.pos}
    for pos, p in prev_by_pos.items():
        if not p.occupied:
            continue
        c = curr_by_pos.get(pos)
        if not c:
            continue
        if not c.occupied or (p.role_id and c.role_id and p.role_id != c.role_id):
            events.append(KickOutEvent(
                ts_prev=prev.ts, ts_curr=curr.ts,
                pool=prev.pool, lot_idx=prev.lot_idx, pos=pos,
                prev_name=p.name, prev_role_id=p.role_id,
                curr_name=c.name if c.occupied else None,
                curr_role_id=c.role_id if c.occupied else None,
            ))
    return events

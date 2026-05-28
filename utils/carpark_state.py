"""Read-only inspection of 菇菇車位 (parking) state via cocos scene tree.

All functions take a Playwright `page` bound to the running game tab and
require ParkingMainView to be open. They do NOT navigate or click anything
— callers are responsible for view state.

Key observations from reverse engineering (2026-05-20):

- `top/scrollHorse` shows MY currently DEPLOYED cars (cross-server parks).
  Each item has txtHp, txtTime, txtLimit. The txtTime label states 跨界車位
  for cross-server, the txtLimit shows 已达上限 when at max stack reward.

- `scrollMiddleShow/view/content/buildingRoot1` shows the CURRENT VIEWED LOT.
  When viewing my own park: 4 spots. When viewing a 跨界 lot: 10 spots.

- **CRITICAL — UIList virtualization stale labels**: the `playerName` and
  `playerServer` Labels inside each building cell DO NOT clear when the
  underlying data changes. Use `nodeName.active` (and the parallel
  `upgrade.active`) as the source of truth for occupancy. The Label string
  may still display the LAST occupant's name even after the spot was vacated.

- Each cross-lot has 10 spots. SILVER pool has 30 lots (鉑銀1~鉑銀30).
  When ≥5/9 cars from same source-server park together (抱團), bonus
  rewards activate.

- 5554 is constrained by user policy to ONLY park 泊銀 (SILVER) in cross.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DeployedCar:
    """A car currently deployed via top/scrollHorse."""
    slot_idx: int            # 0-based position in scrollHorse
    hp_pct: Optional[float]  # e.g. 100.0, 0.0
    timer_str: str           # raw txtTime — ELAPSED time since deploy (NOT remaining), e.g. "07:23:10" or "02:33"
    park_type: str           # raw stale txtTime-001 string — FORENSICS ONLY (UIList virtualization
                             # leaves "跨界车位" stuck even when cell is now a friend-lot park).
                             # Use `is_cross` for the real signal.
    at_limit: bool           # txtLimit shows 已达上限 (max stack reward)
    # Source of truth for park type: nodeCorss.active. True → cross-server,
    # False → friend-lot (same server). Verified live 2026-05-24.
    cross_node_active: bool = False

    @property
    def is_cross(self) -> bool:
        """True iff this is a cross-server deployment.

        Reads the live `nodeCorss.active` flag from the cell — `park_type`
        string is unreliable due to UIList virtualization (stale 跨界车位
        label persists across cell reuse).
        """
        return self.cross_node_active

    @property
    def timer_seconds(self) -> Optional[int]:
        """Parse HH:MM:SS or MM:SS into seconds. None if unparseable."""
        if not self.timer_str:
            return None
        parts = self.timer_str.split(":")
        try:
            if len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                return h * 3600 + m * 60 + s
            elif len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                return m * 60 + s
        except ValueError:
            pass
        return None


@dataclass
class LotSpot:
    """One parking spot inside the currently-viewed lot."""
    slot_idx: int            # 1..10 (matches building<N> name)
    occupied: bool           # based on upgrade.active (universal — true for any park)
    foreign_occupied: bool = False  # nodeName.active — true only for FOREIGN players
    stale_player_name: Optional[str] = None  # label string for forensics only
    stale_combat_power: Optional[str] = None
    stale_timer: Optional[str] = None  # ELAPSED time since park start (NOT remaining)


@dataclass
class LotSnapshot:
    """Snapshot of the currently-viewed lot (whoever's master_id is loaded)."""
    spots: List[LotSpot] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.spots)

    @property
    def occupied_count(self) -> int:
        return sum(1 for s in self.spots if s.occupied)

    @property
    def empty_count(self) -> int:
        return self.total - self.occupied_count

    @property
    def empty_slots(self) -> List[LotSpot]:
        return [s for s in self.spots if not s.occupied]

    @property
    def has_cluster_bonus(self) -> bool:
        """≥5 of 9 spots occupied triggers 抱團 bonus."""
        return self.occupied_count >= 5


@dataclass
class AvailableCar:
    """A car visible in ParkingHorseParkManageView's picker."""
    cell_idx: int            # 0-based position in the picker scroll
    name: str                # 焚寂破心 etc.
    today_park_min: int      # 今日累計停車時間 in minutes (0 = fresh today)
    rate_per_min: int = 0    # 菇車幣 rate; ≥20 = qualified (per user rule)
    is_deployed: bool = False  # already parked somewhere (parking_data.pos != 0)
    coords_cx: int = 0       # CSS px x for clicking the cell
    coords_cy: int = 0       # CSS px y


# ───────────────────────────────────────────────────────────────────
# Read APIs
# ───────────────────────────────────────────────────────────────────


_DEPLOYED_CARS_JS = r"""
() => {
  const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
  const sh = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','top','scrollHorse','view','content']);
  if (!sh) return {err: 'no scrollHorse — is ParkingMainView open?'};
  const grabRecursive = (node, name, depth) => {
    if (!node || depth > 6) return null;
    if (node.name === name) {
      for (const c of (node._components || [])) {
        if (c && typeof c.string === 'string') return c.string;
      }
    }
    for (const k of (node.children || [])) {
      const r = grabRecursive(k, name, depth + 1);
      if (r !== null) return r;
    }
    return null;
  };
  const out = [];
  for (const item of (sh.children || [])) {
    if (!item.activeInHierarchy) continue;
    // nodeCorss.active is the source of truth for cross-server vs friend-lot.
    // (The txtTime-001 "跨界车位" label persists due to UIList virtualization,
    //  so its string is forensic-only.) Note: the game itself uses the typo
    //  "nodeCorss" (not "nodeCross") — keep the spelling.
    const cross = (item.children || []).find(c => c.name === 'nodeCorss');
    out.push({
      slot_idx: parseInt(item.name) || 0,
      hp_str: grabRecursive(item, 'txtHp', 0),
      timer_str: grabRecursive(item, 'txtTime', 0),
      park_type: grabRecursive(item, 'txtTime-001', 0),
      limit_str: grabRecursive(item, 'txtLimit', 0),
      cross_node_active: !!(cross && cross.active),
    });
  }
  return out;
}
"""


def get_deployed_cars(page: Any) -> List[DeployedCar]:
    """Read top/scrollHorse — returns my currently-deployed cars.

    Requires ParkingMainView open. Returns [] if not.
    """
    try:
        raw = page.evaluate(_DEPLOYED_CARS_JS)
    except Exception as e:
        logger.debug(f"[carpark_state] deployed eval failed: {e}")
        return []
    if isinstance(raw, dict) and raw.get("err"):
        logger.debug(f"[carpark_state] deployed: {raw['err']}")
        return []
    out: List[DeployedCar] = []
    for r in (raw or []):
        hp_pct = None
        if isinstance(r.get("hp_str"), str) and r["hp_str"].endswith("%"):
            try:
                hp_pct = float(r["hp_str"].rstrip("%"))
            except ValueError:
                pass
        out.append(DeployedCar(
            slot_idx=int(r.get("slot_idx", 0)),
            hp_pct=hp_pct,
            timer_str=str(r.get("timer_str") or ""),
            park_type=str(r.get("park_type") or ""),
            at_limit=("上限" in str(r.get("limit_str") or "")),
            cross_node_active=bool(r.get("cross_node_active")),
        ))
    return out


_LOT_SNAPSHOT_JS = r"""
() => {
  const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
  const content = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content']);
  if (!content) return {err: 'scrollMiddleShow content not found'};
  // Find whichever buildingRoot* is currently active — cross uses buildingRoot1
  // (10 spots), normal/friend uses buildingRoot (4 spots), server/team uses
  // buildingRoot2 (4 spots).
  let br1 = null;
  for (const name of ['buildingRoot1','buildingRoot','buildingRoot2']) {
    const candidate = (content.children || []).find(c => c.name === name);
    if (candidate && candidate.active) { br1 = candidate; break; }
  }
  if (!br1) return {err: 'no active buildingRoot — not viewing a lot'};
  const out = [];
  for (const b of (br1.children || [])) {
    if (!/^building\d+$/.test(b.name)) continue;
    const nodeName = (b.children || []).find(c => c.name === 'nodeName');
    const upgrade = (b.children || []).find(c => c.name === 'upgrade');
    // Universal occupancy indicator: `upgrade.active` — timer node is active
    // iff a car is parked. Works for both cross (foreign player) and own
    // (my car, nodeName is INACTIVE because no foreign name to show).
    // `nodeName.active` is a SECONDARY signal — true only when a foreign
    // player is occupying (i.e. cross/friend lots, not own home).
    const occupied = !!(upgrade && upgrade.active);
    // Stale labels (for forensics — these don't clear on UIList virtualization)
    const grabLabel = (parent, kidName) => {
      const k = parent ? (parent.children || []).find(c => c.name === kidName) : null;
      if (!k) return null;
      for (const cmp of (k._components || [])) {
        if (cmp && typeof cmp.string === 'string') return cmp.string;
      }
      return null;
    };
    out.push({
      slot_idx: parseInt(b.name.replace('building','')) || 0,
      occupied,
      // True only for FOREIGN occupancy (other player parking on us);
      // false for own deployments. Useful for distinguishing "guest" parks.
      foreign_occupied: !!(nodeName && nodeName.active),
      stale_player_name: grabLabel(nodeName, 'playerName'),
      stale_combat_power: grabLabel(nodeName, 'playerServer'),
      stale_timer: grabLabel(upgrade, 'txtTime'),
    });
  }
  return out;
}
"""


def get_current_lot_snapshot(page: Any) -> Optional[LotSnapshot]:
    """Read buildingRoot1 — the currently viewed cross-server lot.

    Returns None if buildingRoot1 isn't active (you're not viewing a cross
    lot — e.g. on your own park or a different sub-view).
    """
    try:
        raw = page.evaluate(_LOT_SNAPSHOT_JS)
    except Exception as e:
        logger.debug(f"[carpark_state] lot snapshot eval failed: {e}")
        return None
    if isinstance(raw, dict) and raw.get("err"):
        logger.debug(f"[carpark_state] snapshot: {raw['err']}")
        return None
    spots = [LotSpot(
        slot_idx=int(s.get("slot_idx", 0)),
        occupied=bool(s.get("occupied")),
        foreign_occupied=bool(s.get("foreign_occupied")),
        stale_player_name=s.get("stale_player_name"),
        stale_combat_power=s.get("stale_combat_power"),
        stale_timer=s.get("stale_timer"),
    ) for s in (raw or [])]
    return LotSnapshot(spots=spots)


_AVAILABLE_CARS_JS = r"""
() => {
  const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
  const view = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingHorseParkManageView']);
  if (!view || !view.active) return {err: 'ParkingHorseParkManageView not open'};
  const content = find(view, ['root','ScrollView','view','content']);
  if (!content) return {err: 'no picker content'};
  const visible = cc.view.getVisibleSize();
  const canvas = document.querySelector('canvas');
  const rect = canvas.getBoundingClientRect();
  const out = [];
  for (const cell of (content.children || [])) {
    if (!cell.activeInHierarchy) continue;
    const wp = new cc.Vec3(); cell.getWorldPosition(wp);
    out.push({
      cell_idx: parseInt(cell.name) || 0,
      cssX: Math.round(rect.left + wp.x * (rect.width / visible.width)),
      cssY: Math.round(rect.top + (visible.height - wp.y) * (rect.height / visible.height))
    });
  }
  return out;
}
"""


_CAR_DETAIL_JS = r"""
() => {
  // Reads details for the CURRENTLY selected car: name, today_park_min,
  // 菇車幣 rate (first buff item's speed), and parking_data.pos to know
  // if the car is already deployed.
  const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
  const root = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingHorseParkManageView','root']);
  if (!root) return {err: 'picker not open'};
  const grabString = (path) => {
    const n = find(root, path.split('/').filter(Boolean));
    if (!n) return null;
    for (const c of (n._components || [])) {
      if (c && typeof c.string === 'string') return c.string;
    }
    return null;
  };
  // Try to get the currently-selected car id via the picker view object
  const view = window.uiMgr.getView('ParkingHorseParkManageView');
  const cur_id = view ? view.cur_Sel_Car : null;
  // Look up car_list[cur_id] in ParkingDataCache (via netManager listener targets)
  let is_deployed = null;
  if (cur_id) {
    try {
      const cbt = window.netManager._events._callbackTable;
      const list = cbt['car_park.car_park_info_s2c'];
      // The cache holds car_list — but accessing through closures is tricky.
      // Easier: check buildingRoot/scrollHorse for this car's mount via
      // visible nodes — but that's also fragile. Instead, rely on the
      // rate label: a fully-parked car shows '0/min' in its preview while
      // an idle car shows its full rate. NOT a clean signal either.
    } catch (e) {}
  }
  return {
    car_name: grabString('nodeShow/txtName'),
    park_time_rich: grabString('nodeChange/txtParkTime'),
    rate_speed_str: grabString('nodeChange/ScrollView/view/content/0/speed'),
    cur_id,
  };
}
"""


def _parse_today_min(rich_str: Optional[str]) -> Optional[int]:
    """Extract minutes from '今日累計停車時間 480分鐘' or rich-text variant."""
    if not rich_str:
        return None
    import re
    # Strip rich-text tags
    bare = re.sub(r"<[^>]+>", "", rich_str)
    m = re.search(r"(\d+)\s*分鐘", bare)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def get_available_cars_in_picker(page: Any) -> List[AvailableCar]:
    """Read the car picker (ParkingHorseParkManageView) — return one entry
    per visible car cell. Clicks each cell to read today_park_min.

    REQUIRES: ParkingHorseParkManageView is currently open.

    Side effect: leaves the LAST clicked car selected. Caller may want to
    re-click their target car after this scan.
    """
    import time
    try:
        cells_raw = page.evaluate(_AVAILABLE_CARS_JS)
    except Exception as e:
        logger.debug(f"[carpark_state] picker scan eval failed: {e}")
        return []
    if isinstance(cells_raw, dict) and cells_raw.get("err"):
        logger.debug(f"[carpark_state] picker: {cells_raw['err']}")
        return []
    out: List[AvailableCar] = []
    for c in (cells_raw or []):
        try:
            page.mouse.click(c["cssX"], c["cssY"])
        except Exception:
            continue
        time.sleep(0.35)
        try:
            detail = page.evaluate(_CAR_DETAIL_JS) or {}
        except Exception:
            detail = {}
        if isinstance(detail, dict) and detail.get("err"):
            continue
        out.append(AvailableCar(
            cell_idx=int(c.get("cell_idx", 0)),
            name=str(detail.get("car_name") or ""),
            today_park_min=_parse_today_min(detail.get("park_time_rich")) or 0,
            coords_cx=int(c.get("cssX", 0)),
            coords_cy=int(c.get("cssY", 0)),
        ))
    return out


def parking_view_is_open(page: Any) -> bool:
    """True iff ParkingMainView is currently active."""
    try:
        return bool(page.evaluate(
            "(() => { const v = window.uiMgr && window.uiMgr.getView && window.uiMgr.getView('ParkingMainView'); return !!(v && v.node && v.node.active); })()"
        ))
    except Exception:
        return False

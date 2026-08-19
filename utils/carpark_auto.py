"""菇菇車位 — automated park / unpark actions via Playwright on a live game.

Drives the game UI through real mouse clicks (not raw WS sends) so the
client's protobuf wrappers + UIList virtualization are respected.

Target state (per the project's stated goal for 5554, 2026-05-20):

| Taiwan hour | total deployed | of which 跨界 |
|-------------|---------------|---------------|
| 10:00–21:59 | 6             | 1 (must be 泊銀 for 5554) |
| 22:00–09:59 | 5             | 0 |

Configurable per-device under `bot_config.json` → device → `carpark`:

```json
"carpark": {
  "enabled": true,
  "cross_tier": "silver",        // 5554 must be silver per user policy
  "cross_lot_preference": "back",// "front" (low id, hi reward, competitive)
                                 // or "back" (high id, lo reward, more empties)
  "cluster": true,               // prefer lots with ≥5 others (抱團 bonus)
  "daytime_total": 6,
  "nighttime_total": 5,
  "daytime_cross": 1,
  "nighttime_cross": 0
}
```

THIS MODULE IS SCAFFOLDED — only the cross-park flow (park_one_cross_silver)
is implemented end-to-end. 一般 (本服 / 好友) parking flow needs separate
exploration before it can be automated; calls into that path are marked
TODO.

The reverse-engineered UI flow for SILVER cross-park (validated 2026-05-20):

1. ParkingMainView open
2. Click bottom/btnSpace                  → opens ParkingSpaceView
3. Click 跨界 tab (content/128)           → ParkingCrossSpaceView2 active
4. Click SILVER level cell btnParkingSpace → emit PARKING_CROSS_SHOW_PUBLIC_SPOT
                                            → root/item.active = true (lot list)
5. Inner ScrollView's UIList has 30 entries (鉑銀1 … 鉑銀30)
   call `scrollList.scrollTo(N, 0)` to render specific lot
6. Click chosen lot's btnParkingSpace      → sends car_park.car_park_info_c2s
                                            → server returns lot detail
                                            → ParkingMainView re-renders w/ lot's
                                              buildingRoot1 of 10 spots
7. **Use `nodeName.active` NOT label string** to find truly empty spots
8. Click empty spot                        → opens ParkingHorseParkManageView
9. Click car cell (filter by today_park_min=0)
10. Click 開始停車 (nodeStatus/nodePark)    → sends parking_start cmd
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

from utils import carpark_state
from utils.carpark_state import (
    AvailableCar, DeployedCar, LotSnapshot, get_available_cars_in_picker,
    get_current_lot_snapshot, get_deployed_cars, parking_view_is_open,
)
from utils.carpark_click_recorder import get_recorder

logger = logging.getLogger(__name__)


def _click(page: Any, x: int, y: int, *, action: str, **ctx: Any) -> None:
    """Issue a coordinate click + record it (if a recorder is set on this thread).

    `action` is a short tag (e.g. "lot.silver.btnParkingSpace", "spot.empty",
    "picker.car", "picker.confirm_park") so JSONL consumers can group clicks
    by purpose. `ctx` carries any extra fields (lot_idx, slot_idx, in_view…).
    """
    # Honor pause requests (live-view manual takeover etc.) at every click site.
    # If the cocos view stack changed during the pause, this raises TaskAborted
    # and bubbles out of reconcile() so the scheduler can restart fresh.
    from utils import pause_guard
    pause_guard.check()
    rec = get_recorder()
    if rec is not None:
        try:
            rec.record(action=action, x=int(x), y=int(y), **ctx)
        except Exception:
            pass
    page.mouse.click(x, y)


# UI views that can remain above ParkingMainView after a carpark action.
_CARPARK_TRANSIENT_VIEWS = [
    "ParkingWareHouseView",
    "ParkingGainView",
    "RedBagShowView",
    "GoodsGetView",
    "GoodsGetView2",
    "ItemTipsView",
    "ParkingSpaceView",
    "ParkingRobView",
    "ParkingOneKeyReturnView",
    "ParkingHorseManageView",
    "ParkingHorseParkManageView",
]


def _close_carpark_transient_views(page: Any) -> bool:
    """Close carpark popups/sub-views that intercept later main-page tasks."""
    try:
        return bool(page.evaluate(r"""(views) => {
          const um = window.uiMgr;
          if (um && um.getView && um.close) {
            for (const v of views) {
              try {
                if (um.getView(v)) um.close(v);
              } catch (e) {}
            }
          }
          if (typeof cc !== 'undefined' && cc.director) {
            const find = (root, parts) => {
              let n = root;
              for (const p of parts) {
                if (!n || !n.children) return null;
                n = n.children.find(c => (c.name || '') === p);
                if (!n) return null;
              }
              return n;
            };
            const boxTips = find(cc.director.getScene(), ['UIRoot','TopView','MessageView','boxTips']);
            if (boxTips && boxTips.active) boxTips.active = false;
          }
          return true;
        }""", _CARPARK_TRANSIENT_VIEWS))
    except Exception as e:
        logger.debug(f"[carpark_auto] close transient views failed: {e}")
        return False


def _return_parking_to_main(page: Any) -> bool:
    """Leave ParkingMainView through its known bottom close button."""
    _close_carpark_transient_views(page)
    clicked = False
    try:
        res = page.evaluate(r"""() => {
          if (typeof cc === 'undefined' || !cc.director) return {open: false, clicked: false, err: 'no_cc'};
          const find = (root, parts) => {
            let n = root;
            for (const p of parts) {
              if (!n || !n.children) return null;
              n = n.children.find(c => (c.name || '') === p);
              if (!n) return null;
            }
            return n;
          };
          const view = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView']);
          if (!view || !view.active) return {open: false, clicked: false};
          const btn = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','bottom','btnClose']);
          if (!btn || !btn.activeInHierarchy) return {open: true, clicked: false, err: 'bottom_btnClose_missing'};
          btn.emit('click', btn);
          return {open: true, clicked: true};
        }""") or {}
        clicked = bool(res.get("clicked"))
        if clicked:
            time.sleep(1.5)
        elif res.get("open"):
            logger.warning(f"[carpark_auto] ParkingMainView bottom close unavailable: {res.get('err')}")
    except Exception as e:
        logger.warning(f"[carpark_auto] return to main failed while clicking ParkingMainView close: {e}")

    try:
        if parking_view_is_open(page):
            logger.warning("[carpark_auto] ParkingMainView still open after bottom close")
            return False
    except Exception:
        return clicked

    try:
        from utils.cocos_navigator import CocosNavigator
        nav = CocosNavigator(page)
        view = nav.current_view()
        if view == "main":
            return True
        if view == "home":
            return nav.goto_main()
        return clicked
    except Exception as e:
        logger.debug(f"[carpark_auto] post-carpark main verification skipped: {e}")
        return clicked


# Pool type → cell id in ParkingCrossSpaceView2 level list (from JS reverse)
POOL_TYPE_TO_ID = {
    "diamond": 1,   # 曜鑽車座
    "gold":    2,   # 鎏金車座
    "silver":  3,   # 泊銀車座 (5554 must use this)
    "bronze":  4,   # 灰銅車座
    "server":  5,   # 奇星車場
}

# Lot id range per pool (from configCross_parking_lot dump 2026-05-20).
# Each pool has 30 distinct lots (1~30 user-visible, ids 5~34 internal for silver).
# (Other tiers may have different internal id ranges — only SILVER verified.)
SILVER_LOT_ID_BASE = 5     # internal id for 鉑銀1
SILVER_LOT_COUNT = 30      # 鉑銀1 .. 鉑銀30

TAIWAN_TZ = timezone(timedelta(hours=8))


# ───────────────────────────────────────────────────────────────────
# Time policy
# ───────────────────────────────────────────────────────────────────


def taiwan_now() -> datetime:
    return datetime.now(TAIWAN_TZ)


def is_daytime_window(now: Optional[datetime] = None) -> bool:
    """Taiwan 10:00 ≤ hour < 22:00 == daytime."""
    if now is None:
        now = taiwan_now()
    return 10 <= now.hour < 22


@dataclass
class CarparkTarget:
    total_deployed: int
    cross_count: int

    @property
    def normal_count(self) -> int:
        return self.total_deployed - self.cross_count


def target_state(cfg: dict, now: Optional[datetime] = None) -> CarparkTarget:
    """Resolve target deployment counts per the user policy + device config."""
    daytime = is_daytime_window(now)
    if daytime:
        return CarparkTarget(
            total_deployed=int(cfg.get("daytime_total", 6)),
            cross_count=int(cfg.get("daytime_cross", 1)),
        )
    return CarparkTarget(
        total_deployed=int(cfg.get("nighttime_total", 5)),
        cross_count=int(cfg.get("nighttime_cross", 0)),
    )


# ───────────────────────────────────────────────────────────────────
# Backend-agnostic tier-aggregate parsing (shared by the ADB/OCR path)
# ───────────────────────────────────────────────────────────────────


def parse_occupied_total(texts: List[str]) -> Optional[tuple]:
    """Parse a 泊銀 tier "occupied/total" aggregate out of OCR text fragments.

    The ADB path OCRs the cropped tier number, which can come back as
    ``['299/300']``, ``['299', '300']``, ``['299 / 300']``, or with stray
    glyphs. We anchor on the slash pattern first (most reliable), then fall
    back to "exactly two integers". A result is only accepted when it's
    internally consistent: total a positive multiple of 10 (the real value is
    always 10 × lot_count) and 0 ≤ occupied ≤ total. Returns ``(occupied,
    total)`` or ``None`` when no trustworthy reading is found — the caller must
    treat ``None`` conservatively (don't fabricate a full/empty verdict).

    NOTE: this cannot catch every OCR digit error (e.g. 300→30 still passes
    %10); it only rejects gross garbage. Callers should prefer the stricter
    expected-total check where the tier's lot count is known.
    """
    import re
    joined = " ".join(t for t in texts if t)
    cands = []
    m = re.search(r"(\d{1,4})\s*/\s*(\d{1,4})", joined)
    if m:
        cands.append((int(m.group(1)), int(m.group(2))))
    else:
        nums = [int(n) for n in re.findall(r"\d{1,4}", joined)]
        if len(nums) == 2:
            cands.append((nums[0], nums[1]))
    for occ, tot in cands:
        if tot > 0 and tot % 10 == 0 and 0 <= occ <= tot:
            return (occ, tot)
    return None


def silver_tier_full(occ_total: Optional[tuple]) -> Optional[bool]:
    """True if the whole 泊銀車座 is full (no empty spot), False if it has space,
    None if the reading was untrustworthy. Mirrors the H5 _silver_tier_has_empty
    verdict (which returns the inverse) so both backends share one decision."""
    if not occ_total:
        return None
    occ, tot = occ_total
    return occ >= tot


# ───────────────────────────────────────────────────────────────────
# Navigation helpers
# ───────────────────────────────────────────────────────────────────


def _ensure_parking_main_open(page: Any) -> bool:
    """Make sure ParkingMainView is open. Returns True iff confirmed open.

    Uses CocosNavigator to go through main → home → carpark if needed.
    """
    if parking_view_is_open(page):
        return True
    try:
        from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
        nav = CocosNavigator(page)
        # Clear popups (incl. startup 公告/獎勵/boxTips) and only navigate once we
        # actually reached 主頁面. emit-click bypasses the visual layer, so
        # navigating with a popup still up would strand it on top of the carpark.
        nav.dismiss_blocking_popups()
        if not nav.goto_main():
            nav.dismiss_blocking_popups()
            if not nav.goto_main():
                logger.warning("[carpark_auto] 無法回到主頁面（彈窗未清除？），跳過進入車位")
                return False
        time.sleep(0.8)
        nav._click_path(COCOS_PATHS["home_tab"])
        time.sleep(1.5)
        nav._click_path(COCOS_PATHS["carpark_node"])
        time.sleep(2.5)
    except Exception as e:
        logger.warning(f"[carpark_auto] navigate to ParkingMainView failed: {e}")
        return False
    return parking_view_is_open(page)


def _open_space_view_and_cross_tab(page: Any) -> bool:
    """Open ParkingSpaceView then click 跨界 tab. Returns True on success."""
    try:
        page.evaluate(r"""() => {
          const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
          const btn = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','bottom','btnSpace']);
          if (btn) btn.emit('click', btn);
        }""")
        time.sleep(2.0)
        page.evaluate(r"""() => {
          const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
          const sub = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','container','content','nodeSubRoot']);
          const cross_active = sub && (sub.children || []).find(c => c.name === 'ParkingCrossSpaceView2' && c.active);
          if (!cross_active) {
            const tab = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','tab','scrollTab','view','content','128']);
            if (tab) tab.emit('click', tab);
          }
        }""")
        time.sleep(2.0)
    except Exception as e:
        logger.warning(f"[carpark_auto] open cross tab failed: {e}")
        return False
    return True


def _click_pool_tier(page: Any, pool_id: int) -> bool:
    """Click the SILVER (or other) level cell in ParkingCrossSpaceView2.

    Walks the scroll's _datas to find the cell with `id == pool_id`, then
    clicks via mouse coords. Returns True if the lot detail view opened.
    """
    coords = page.evaluate(r"""([poolId]) => {
      const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
      const content = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','container','content','nodeSubRoot','ParkingCrossSpaceView2','root','scroll','view','content']);
      if (!content) return null;
      // Find cell whose data is in the level scroll — but we need to match
      // by data.id which we don't directly access; iterate cells, look at txtName
      // and infer. Easier: pick cell by name pattern (cells named "0".."4"),
      // and use position-based mapping from data dump:
      //   cells in this scroll show pool levels in order: 奇星(5) 曜鑽(1) 鎏金(2) 泊銀(3) 灰銅(4)
      //   so cell name → pool_id: "0"→5, "1"→1, "2"→2, "3"→3, "4"→4
      const cellByPool = {5:"0", 1:"1", 2:"2", 3:"3", 4:"4"};
      const cellName = cellByPool[poolId];
      if (!cellName) return null;
      const cell = (content.children || []).find(c => c.name === cellName && c.activeInHierarchy);
      if (!cell) return null;
      const btn = (cell.children || []).find(c => c.name === 'btnParkingSpace');
      if (!btn) return null;
      const wp = new cc.Vec3(); btn.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      return {x: Math.round(r.left + wp.x*r.width/v.width), y: Math.round(r.top + (v.height-wp.y)*r.height/v.height)};
    }""", [pool_id])
    if not coords:
        logger.warning(f"[carpark_auto] no cell for pool_id={pool_id}")
        return False
    _click(page, coords["x"], coords["y"], action="pool.btnParkingSpace", pool_id=pool_id)
    time.sleep(2.5)
    # Verify root/item became active (detail list)
    return bool(page.evaluate(r"""() => {
      const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
      const it = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','container','content','nodeSubRoot','ParkingCrossSpaceView2','root','item']);
      return !!(it && it.active);
    }"""))


def _click_silver_lot_by_idx(page: Any, lot_idx: int) -> bool:
    """Enter SILVER detail list at lot_idx (0-based: 0=鉑銀1, 29=鉑銀30).

    Uses scrollList.scrollTo to render the target cell, then clicks its
    btnParkingSpace. The click sends car_park.car_park_info_c2s and the
    server responds with the lot detail (~2-3KB), which re-renders
    buildingRoot1 with this lot's 10 spots.
    """
    if not (0 <= lot_idx < SILVER_LOT_COUNT):
        logger.warning(f"[carpark_auto] silver lot_idx {lot_idx} out of range")
        return False
    # Ask UIList to render the target index
    page.evaluate(f"""() => {{
      const v = window.uiMgr.getView('ParkingSpaceView');
      if (!v) return;
      const csv = v._subViewDict && v._subViewDict['ParkingCrossSpaceView2'];
      if (!csv) return;
      const ul = (csv._uilists || [])[1];
      if (ul && typeof ul.scrollTo === 'function') ul.scrollTo({lot_idx}, 0);
    }}""")
    time.sleep(1.2)
    # Pull coords of the now-rendered cell with name == str(lot_idx)
    coords = page.evaluate(f"""() => {{
      const find = (root, parts) => {{ let n = root; for (const p of parts) {{ if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; }} return n; }};
      const content = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','container','content','nodeSubRoot','ParkingCrossSpaceView2','root','item','ScrollView','view','content']);
      if (!content) return null;
      const cell = (content.children || []).find(c => c.name === '{lot_idx}' && c.activeInHierarchy);
      if (!cell) return null;
      const btn = (cell.children || []).find(c => c.name === 'btnParkingSpace');
      if (!btn) return null;
      const wp = new cc.Vec3(); btn.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      return {{x: Math.round(r.left + wp.x*r.width/v.width), y: Math.round(r.top + (v.height-wp.y)*r.height/v.height)}};
    }}""")
    if not coords:
        logger.warning(f"[carpark_auto] silver lot {lot_idx+1} cell not visible after scrollTo")
        return False
    _click(page, coords["x"], coords["y"], action="lot.silver.btnParkingSpace",
           lot_idx=lot_idx, lot_number=lot_idx + 1)
    time.sleep(3.5)  # lot detail fetch from server
    return True


def _click_empty_spot_in_current_lot(page: Any) -> Optional[int]:
    """Find an empty building in buildingRoot1 (current lot) + click it.

    Returns the building index (1..10) of the clicked spot, or None if no
    visible empty spot. Honors UIList stale-label rule: uses nodeName.active
    not label string.

    Side effect: ScrollMiddleShow may need to scroll to bring the empty
    building into the viewport. This function first scrolls to left then
    locates the first empty.
    """
    # Scroll back to left so spots in first column become visible
    page.evaluate(r"""() => {
      const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
      const sms = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow']);
      const sv = sms && sms.getComponent('cc.ScrollView');
      if (sv && typeof sv.scrollToLeft === 'function') sv.scrollToLeft(0);
    }""")
    time.sleep(1.0)

    snapshot = get_current_lot_snapshot(page)
    if not snapshot:
        logger.warning("[carpark_auto] no current lot snapshot")
        return None
    empties = snapshot.empty_slots
    if not empties:
        logger.info(f"[carpark_auto] lot is full: {snapshot.occupied_count}/{snapshot.total}")
        return None
    target = empties[0]
    # Get coords — discover which buildingRoot is currently active (cross=br1,
    # normal/friend=br0, server=br2).
    active_root = page.evaluate(r"""() => {
      const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
      const content = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content']);
      for (const name of ['buildingRoot1','buildingRoot','buildingRoot2']) {
        const c = (content.children||[]).find(c => c.name === name);
        if (c && c.active) return name;
      }
      return null;
    }""")
    if not active_root:
        logger.warning("[carpark_auto] no active buildingRoot")
        return None
    coords = page.evaluate(f"""() => {{
      const find = (root, parts) => {{ let n = root; for (const p of parts) {{ if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; }} return n; }};
      const b = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content','{active_root}','building{target.slot_idx}']);
      if (!b) return null;
      const wp = new cc.Vec3(); b.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      const x = Math.round(r.left + wp.x*r.width/v.width);
      const y = Math.round(r.top + (v.height-wp.y)*r.height/v.height);
      const in_view = x >= 0 && x <= r.width && y >= 0 && y <= r.height;
      return {{x, y, in_view}};
    }}""")
    if not coords or not coords.get("in_view"):
        # Try scrolling right to bring empty into view
        page.evaluate(r"""() => {
          const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
          const sms = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow']);
          const sv = sms && sms.getComponent('cc.ScrollView');
          if (sv && typeof sv.scrollToRight === 'function') sv.scrollToRight(0);
        }""")
        time.sleep(1.0)
        # Re-pull coords from same active_root
        coords = page.evaluate(f"""() => {{
          const find = (root, parts) => {{ let n = root; for (const p of parts) {{ if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; }} return n; }};
          const b = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content','{active_root}','building{target.slot_idx}']);
          if (!b) return null;
          const wp = new cc.Vec3(); b.getWorldPosition(wp);
          const v = cc.view.getVisibleSize();
          const r = document.querySelector('canvas').getBoundingClientRect();
          const x = Math.round(r.left + wp.x*r.width/v.width);
          const y = Math.round(r.top + (v.height-wp.y)*r.height/v.height);
          return {{x, y, in_view: x >= 0 && x <= r.width && y >= 0 && y <= r.height}};
        }}""")
    if not coords or not coords.get("in_view"):
        logger.warning(f"[carpark_auto] empty building{target.slot_idx} not in viewport after scroll")
        return None
    _click(page, coords["x"], coords["y"], action="spot.empty",
           building_root=active_root, slot_idx=target.slot_idx)
    time.sleep(3.0)
    return target.slot_idx


def _pick_zero_minute_car_and_park(page: Any, fallback_to_lowest: bool = True) -> Optional[str]:
    """Inside ParkingHorseParkManageView, scan picker, select a car with
    today_park_min=0 (per user preference), click 開始停車.

    If no 0-min car exists (already deployed earlier today), falls back to
    the car with the LOWEST today_park_min — assuming the user's intent is
    "deploy something" over "deploy nothing", and the lowest-time car has
    received the least benefit-from-stacking-rewards so far.

    Returns the chosen car's name or None.
    """
    cars = get_available_cars_in_picker(page)
    if not cars:
        logger.warning("[carpark_auto] no cars visible in picker")
        return None
    zero_cars = [c for c in cars if c.today_park_min == 0]
    if zero_cars:
        chosen = zero_cars[0]
    elif fallback_to_lowest:
        chosen = min(cars, key=lambda c: c.today_park_min)
        logger.info(f"[carpark_auto] no 0-min car; falling back to lowest "
                    f"({chosen.name}, {chosen.today_park_min}min)")
    else:
        logger.info(f"[carpark_auto] no fresh-today car found in picker; "
                    f"available: {[(c.name, c.today_park_min) for c in cars]}")
        return None
    # Re-click to ensure selection (get_available_cars_in_picker left LAST as selected)
    _click(page, chosen.coords_cx, chosen.coords_cy, action="picker.car",
           car_name=chosen.name, today_park_min=chosen.today_park_min,
           cell_idx=chosen.cell_idx)
    time.sleep(0.5)
    # Get nodePark coords (Open Park button)
    np_coords = page.evaluate(r"""() => {
      const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
      const node = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingHorseParkManageView','root','nodeStatus','nodePark']);
      if (!node) return null;
      const wp = new cc.Vec3(); node.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      return {x: Math.round(r.left + wp.x*r.width/v.width), y: Math.round(r.top + (v.height-wp.y)*r.height/v.height)};
    }""")
    if not np_coords:
        logger.warning("[carpark_auto] nodePark button not found")
        return None
    _click(page, np_coords["x"], np_coords["y"], action="picker.confirm_park",
           car_name=chosen.name)
    time.sleep(3.5)
    return chosen.name


# ───────────────────────────────────────────────────────────────────
# Whole-tier emptiness check — lets us skip the cross-park instantly when the
# ENTIRE 泊銀車座 is full, instead of grinding through up to MAX_TRY full lots
# (each a full re-navigation + server fetch — the "卡住/狂點很久" the user saw).
# ───────────────────────────────────────────────────────────────────


# Reads the 泊銀 tier cell's rendered "occupied/total" aggregate in
# ParkingCrossSpaceView2's tier list. The cell is found by its icon sprite
# (gg_icon_dijichewei == SILVER) rather than a fixed index, because the tier
# order shifts when 奇星車場 (server parking) is open. The label is rich text
# (<color=#4a9d3e>occ</color>/<color=#543417>total</color>) so we MUST strip the
# tags before extracting numbers — the hex colour codes contain digits. The
# number is the client's own count from its cached search result, so total is
# always a multiple of 10 (10 spots × lot count); we use that as a sanity gate
# against a malformed/mid-render reading. (ParkingDataCache.null_space would be
# cleaner but is NOT reachable from page context — every car_park s2c listener's
# target is the message dispatcher, not the data cache.)
_SILVER_TIER_OCC_JS = r"""
() => {
  const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
  const csv=find(cc.director.getScene(),['UIRoot','NormalView','ParkingMainView','container','ParkingSpaceView','container','content','nodeSubRoot','ParkingCrossSpaceView2']);
  if(!csv || !csv.active) return {err:'cross_view_not_active'};
  const content=find(csv,['root','scroll','view','content']);
  if(!content) return {err:'tier_content_missing'};
  let silverCell=null;
  for(const cell of (content.children||[])){
    const icon=(cell.children||[]).find(c=>c.name==='icon');
    let sf=null;
    if(icon){ const sp=(icon._components||[]).find(c=>c&&c.spriteFrame); sf=sp&&sp.spriteFrame&&sp.spriteFrame.name; }
    if(sf==='gg_icon_dijichewei'){ silverCell=cell; break; }
  }
  if(!silverCell) return {err:'silver_cell_not_found'};
  const numSpot=(silverCell.children||[]).find(c=>c.name==='numSpot');
  const num=numSpot&&(numSpot.children||[]).find(c=>c.name==='num');
  let s=null;
  if(num){ for(const c of (num._components||[])){ if(c&&typeof c.string==='string'){ s=c.string; break; } } }
  if(s==null) return {err:'numSpot_missing'};
  // Strip the rich-text tags FIRST — the <color=#4a9d3e> hex codes contain
  // digits that would otherwise leak into the number extraction.
  const bare=s.replace(/<[^>]*>/g,'');
  const nums=(bare.match(/\d+/g)||[]).map(Number);
  if(nums.length<2) return {err:'parse_failed', raw:s};
  return {ok:true, occupied:nums[0], total:nums[1], raw:s};
}
"""


def _silver_tier_has_empty(page: Any, *, attempts: int = 6,
                           interval: float = 0.7) -> Optional[bool]:
    """Whole 泊銀車座 emptiness from the rendered tier aggregate.

    Returns True if any silver spot is free, False if the whole tier is full,
    or None if no trustworthy reading was obtained (caller falls back to the
    legacy per-lot iteration).

    The aggregate is the client's own occupied/total for the tier. We only
    trust a reading whose total is a positive multiple of 10 (the real value is
    always 10 × lot_count) as a sanity gate against a malformed/mid-render
    reading, polling again otherwise.
    """
    for _ in range(max(1, attempts)):
        try:
            res = page.evaluate(_SILVER_TIER_OCC_JS)
        except Exception as e:
            logger.debug(f"[carpark_auto] silver tier occ eval failed: {e}")
            res = None
        if isinstance(res, dict) and res.get("ok"):
            occ, tot = int(res.get("occupied", 0)), int(res.get("total", 0))
            if tot > 0 and tot % 10 == 0:
                logger.debug(f"[carpark_auto] 泊銀車座 {occ}/{tot}")
                return occ < tot
        time.sleep(interval)
    return None


# ───────────────────────────────────────────────────────────────────
# High-level actions
# ───────────────────────────────────────────────────────────────────


def _reenter_silver_detail_list(page: Any, pool_id: int) -> bool:
    """Re-open the SILVER detail list to advance to the next lot.

    Closing a full / no-cluster lot view back to the detail list isn't automatic,
    so restart the picker flow: ParkingMain → space+cross tab → pool tier.
    Returns False if any step fails (caller should return None / abort).
    """
    if not _ensure_parking_main_open(page):
        return False
    if not _open_space_view_and_cross_tab(page):
        return False
    return _click_pool_tier(page, pool_id)


def park_one_silver(
    page: Any,
    prefer_back: bool = True,
    cluster: bool = True,
    avoid_lots: Optional[List[int]] = None,
) -> Optional[str]:
    """Park one fresh-today car at a 泊銀 (SILVER) lot.

    Args:
        prefer_back: True → try 鉑銀30 → 29 → 28 → … (low reward, more empties)
                     False → 鉑銀1 → 2 → 3 → … (high reward, competitive)
        cluster: True → among candidates, prefer lots with ≥5 occupied (抱團 bonus)
        avoid_lots: 1-indexed lot numbers (鉑銀1..鉑銀30) to skip entirely. E.g.
                    [1,2,3,4] skips the four highest-reward / most-competitive
                    lots that often fill quickly. None / [] = no filter.

    Returns the parked car's name on success, None on any failure — including
    the deliberate skip when the WHOLE 泊銀車座 is full (checked up front via the
    tier's occupied/total aggregate, so we don't grind through every lot).

    Iterates lots in the chosen direction up to MAX_TRY tries, looking for
    one with at least one empty spot (so we can actually park).
    """
    if not _ensure_parking_main_open(page):
        logger.warning("[carpark_auto] ParkingMainView didn't open")
        return None
    if not _open_space_view_and_cross_tab(page):
        return None

    # Fast skip: if the WHOLE 泊銀車座 is full, bail out now instead of grinding
    # through up to MAX_TRY full lots (the "卡住/狂點很久" the user reported).
    # Opening the 跨界 tab above triggered the client's space search, so the
    # tier's occupied/total aggregate reflects current availability. None = no
    # trustworthy reading → fall through to the legacy iteration unchanged.
    if _silver_tier_has_empty(page) is False:
        logger.info("[carpark_auto] 整個泊銀車座已滿，跳過跨服停車")
        return None

    pool_id = POOL_TYPE_TO_ID["silver"]
    if not _click_pool_tier(page, pool_id):
        return None

    MAX_TRY = 8
    order = list(range(SILVER_LOT_COUNT - 1, -1, -1)) if prefer_back else list(range(SILVER_LOT_COUNT))
    if avoid_lots:
        avoid_idx = {int(n) - 1 for n in avoid_lots}  # 1-indexed → 0-indexed
        order = [i for i in order if i not in avoid_idx]
        if not order:
            logger.warning(f"[carpark_auto] avoid_lots={avoid_lots} eliminated every SILVER lot")
            return None
    # First pass: cluster preference (≥5 occupied, but with at least 1 empty)
    # Second pass (if no cluster found): any lot with empties
    for pass_no in (0, 1):
        tried = 0
        for idx in order:
            if tried >= MAX_TRY:
                break
            tried += 1
            if not _click_silver_lot_by_idx(page, idx):
                continue
            snap = get_current_lot_snapshot(page)
            if not snap:
                continue
            if snap.empty_count == 0:
                logger.debug(f"[carpark_auto] 鉑銀{idx+1} full ({snap.occupied_count}/{snap.total})")
                # Closing the lot view back to the detail list isn't automatic;
                # re-enter to advance to the next lot.
                if not _reenter_silver_detail_list(page, pool_id):
                    return None
                continue
            if pass_no == 0 and cluster and not snap.has_cluster_bonus:
                logger.debug(f"[carpark_auto] 鉑銀{idx+1} has empties but no cluster ({snap.occupied_count}/{snap.total})")
                if not _reenter_silver_detail_list(page, pool_id):
                    return None
                continue
            logger.info(f"[carpark_auto] 鉑銀{idx+1} OK ({snap.occupied_count}/{snap.total}); "
                        f"clicking empty spot")
            clicked_idx = _click_empty_spot_in_current_lot(page)
            if clicked_idx is None:
                return None
            return _pick_zero_minute_car_and_park(page)
        # If we got here in pass 0, cluster lots not found — retry without cluster filter
        if not cluster:
            break
    logger.warning("[carpark_auto] no suitable SILVER lot found after all attempts")
    return None


def claim_open_warehouse(page: Any) -> bool:
    """直接由 Cocos node 領取目前已開啟的車位倉庫。

    這個 helper 不負責導航，也不使用 OCR；呼叫端必須先確認目前是
    ``ParkingWareHouseView``。找不到 node 或 click listener 時回 False，
    讓 web_h5 呼叫端安全停止/重試，不回退到 OCR。
    """
    try:
        result = page.evaluate(r"""() => {
          const find=(r,p)=>{
            let n=r;
            for(const x of p){
              if(!n||!n.children)return null;
              n=n.children.find(c=>(c.name||'')===x);
              if(!n)return null;
            }
            return n;
          };
          const b=find(cc.director.getScene(),
            ['UIRoot','NormalView','ParkingWareHouseView','root','content','rewardBtn']);
          if(!b || !b.activeInHierarchy)
            return {ok:false,err:'rewardBtn_not_active'};
          if(typeof b.hasEventListener === 'function' && !b.hasEventListener('click'))
            return {ok:false,err:'rewardBtn_click_listener_missing'};
          b.emit('click', b);
          return {ok:true,node:b.name,label:(b.children||[]).map(c=>{
            const l=c.getComponent && cc.Label ? c.getComponent(cc.Label) : null;
            return l ? String(l.string||'') : '';
          }).filter(Boolean)};
        }""") or {}
    except Exception as exc:
        logger.warning("[carpark_auto] Cocos 領取車位倉庫例外: %s", exc)
        return False
    if not result.get("ok"):
        logger.warning("[carpark_auto] Cocos 領取車位倉庫失敗: %s", result.get("err"))
        return False
    time.sleep(3.5)
    logger.info("[carpark_auto] Cocos 領取車位倉庫成功: %s", result)
    return True


def claim_warehouse(page: Any) -> bool:
    """Claim all unclaimed rewards in the parking warehouse (倉庫).

    The 倉庫 button is at `/UIRoot/NormalView/ParkingMainView/btnWareHourse`
    (bottom-left of ParkingMainView). It has a RedPoint child when there
    are unclaimed rewards.

    Flow (validated 2026-05-20):
      1. Check `btnWareHourse/RedPoint.active` — if False, nothing to claim
      2. Click btnWareHourse → opens ParkingWareHouseView
      3. Click `root/content/rewardBtn` (label "領取")
         → sends `car_park.car_park_collect_all_bag_rewards_c2s` (cmd 0x322e)
         → server streams ~50 frames of distributed rewards

    Returns True iff claim was attempted (RedPoint was active + click went
    through). Returns False if no rewards pending.
    """
    if not _ensure_parking_main_open(page):
        return False
    # Close any blocking sub-views first
    _close_carpark_transient_views(page)
    time.sleep(1.0)

    # Probe warehouse button state
    info = page.evaluate(r"""() => {
      const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
      const b = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','btnWareHourse']);
      if (!b) return null;
      const rp = (b.children||[]).find(c => c.name === 'RedPoint');
      const has_red = !!(rp && rp.active);
      const wp = new cc.Vec3(); b.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      return {has_red, x: Math.round(r.left + wp.x*r.width/v.width), y: Math.round(r.top + (v.height-wp.y)*r.height/v.height)};
    }""")
    if not info:
        logger.warning("[carpark_auto] claim_warehouse: btnWareHourse not found")
        return False
    if not info.get("has_red"):
        logger.debug("[carpark_auto] claim_warehouse: no pending rewards (RedPoint off)")
        return False

    # Open warehouse view
    _click(page, info["x"], info["y"], action="warehouse.btnWareHourse")
    time.sleep(2.5)
    # Verify open
    wh_open = bool(page.evaluate(
        "(() => { const v = window.uiMgr.getView('ParkingWareHouseView'); return !!(v && v.node && v.node.active); })()"
    ))
    if not wh_open:
        logger.warning("[carpark_auto] claim_warehouse: ParkingWareHouseView didn't open")
        _close_carpark_transient_views(page)
        return False

    # Click 領取 button
    rb_coords = page.evaluate(r"""() => {
      const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
      const b = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingWareHouseView','root','content','rewardBtn']);
      if (!b) return null;
      const wp = new cc.Vec3(); b.getWorldPosition(wp);
      const v = cc.view.getVisibleSize();
      const r = document.querySelector('canvas').getBoundingClientRect();
      return {x: Math.round(r.left + wp.x*r.width/v.width), y: Math.round(r.top + (v.height-wp.y)*r.height/v.height)};
    }""")
    if not rb_coords:
        logger.warning("[carpark_auto] claim_warehouse: rewardBtn not found")
        _close_carpark_transient_views(page)
        return False
    _click(page, rb_coords["x"], rb_coords["y"], action="warehouse.rewardBtn")
    time.sleep(3.5)
    _close_carpark_transient_views(page)
    time.sleep(0.8)
    logger.info("[carpark_auto] claim_warehouse: 領取 click sent")
    return True


# ───────────────────────────────────────────────────────────────────
# Reconciliation entrypoint
# ───────────────────────────────────────────────────────────────────


@dataclass
class CarparkSnapshot:
    """Aggregate of what's deployed right now.

    - `deployed` = top/scrollHorse cars (跨界 deployments at remote lots)
    - `home_occupied` = buildingRoot occupied count (MY home park, including
      both my own cars + any foreign parkers paying tax).

    For target "1 跨界 + 5 一般":
      - cross_count must match deployed (跨界 from scrollHorse)
      - normal_count (home_occupied) tracks home park occupancy
    """
    deployed: List[DeployedCar]
    home_occupied: int = 0
    home_total: int = 0

    @property
    def cross_count(self) -> int:
        """Cars deployed cross-server (at remote lots)."""
        return sum(1 for d in self.deployed if d.is_cross)

    @property
    def normal_count(self) -> int:
        """Cars at MY home park (一般 deployment)."""
        return self.home_occupied

    @property
    def total(self) -> int:
        """All deployments visible: cross + home park occupied."""
        return len(self.deployed) + self.home_occupied


def take_snapshot(page: Any) -> Optional[CarparkSnapshot]:
    """Read current deployed state — scrollHorse (跨界) + buildingRoot (home).

    Requires ParkingMainView open. To read home park accurately, this
    function ensures we're on home view (clicks bottom/btnBack if needed),
    then reads both surfaces.
    """
    if not parking_view_is_open(page):
        if not _ensure_parking_main_open(page):
            return None
    deployed = get_deployed_cars(page)
    # Try to read home park (buildingRoot) — only valid when on own home view.
    # If currently viewing a remote lot, buildingRoot may be inactive; the
    # snapshot's home_occupied will be 0 in that case (caller can ignore).
    home_occupied = 0
    home_total = 0
    try:
        snap = get_current_lot_snapshot(page)
        if snap:
            # Need to confirm this snapshot is OWN home not someone else's lot.
            # Heuristic: buildingRoot (own) has 4 spots, buildingRoot1 (cross/foreign)
            # has 10 spots. Plus check `foreign_occupied` — if ALL occupied spots
            # have foreign_occupied=False, it's likely my own deployment.
            active_root = page.evaluate(r"""() => {
              const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
              const content = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','scrollMiddleShow','view','content']);
              for (const name of ['buildingRoot','buildingRoot1','buildingRoot2']) {
                const c = (content.children||[]).find(c => c.name === name);
                if (c && c.active) return name;
              }
              return null;
            }""")
            # Only treat snapshot as "home" when buildingRoot (not br1/br2) is active
            if active_root == "buildingRoot":
                home_occupied = snap.occupied_count
                home_total = snap.total
    except Exception:
        pass
    return CarparkSnapshot(deployed=deployed, home_occupied=home_occupied, home_total=home_total)


def _build_snapshot_summary(s):
    """Pure projection of a CarparkSnapshot into the dashboard summary dict.

    Hoisted from a nested closure in reconcile() (cx-7); reads only its arg
    ``s``. Keep the returned shape byte-identical — the dashboard reads it.
    """
    return {
        "cross_deployed": s.cross_count,
        "home_occupied": s.home_occupied if s.home_total else "not-visible",
        "home_capacity": s.home_total if s.home_total else "n/a",
        "normal_at_friends": "not-visible",
        "deployed_detail": [
            {"slot": d.slot_idx, "hp_pct": d.hp_pct, "elapsed": d.timer_str,
             "park_type": d.park_type, "at_limit": d.at_limit}
            for d in s.deployed
        ],
    }


def reconcile(page: Any, cfg: dict, now: Optional[datetime] = None, *,
              claim_warehouse_rewards: bool = True) -> dict:
    """One-shot reconciliation: compare current deployed state vs target,
    take corrective actions, return a summary dict.

    Returns:
        {
          'snapshot': {'cross_deployed': int,
                       'home_occupied': int|'not-visible',
                       'home_capacity': int|'n/a',
                       'normal_at_friends': 'not-visible',
                       'deployed_detail': [...]},
          'target':   {'cross_deployed': int, 'normal_at_friends': int},
          'actions':  ['parked X', 'recalled Y', ...]
        }

    Currently only acts on the "need more cross" case (calls park_one_silver).
    Other cases (need fewer cross, need more normal, need to recall) are
    logged but not executed yet — see TODO.
    """
    actions: list[str] = []
    try:
        # Reconcile may be called repeatedly across task cycles. Start clean so
        # stale ParkingSpaceView/ParkingGainView from earlier doesn't block clicks.
        if parking_view_is_open(page):
            try:
                _close_carpark_transient_views(page)
                time.sleep(1.0)
                # Click bottom/btnBack to ensure we're on OWN home view (buildingRoot
                # active) — so the snapshot can read home_occupied accurately.
                page.evaluate(r"""() => {
                  const find=(r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
                  const btn = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','bottom','btnBack']);
                  if (btn && btn.activeInHierarchy) btn.emit('click', btn);
                }""")
                time.sleep(1.5)
            except Exception:
                pass
        snap = take_snapshot(page)
        if snap is None:
            return {"err": "no snapshot", "actions": actions}
        tgt = target_state(cfg, now)

        # Snapshot shape — explicit field names, no ambiguous "total" or
        # overloaded "normal" (cross_deployed is *my* cars at remote lots;
        # home_occupied is whoever is parked at *my* home — mine + foreign;
        # normal_at_friends is what's deployed at friends' lots and is NOT
        # visible from this view, hence the "not-visible" marker).
        summary = {
            "snapshot": _build_snapshot_summary(snap),
            "target": {
                "cross_deployed": tgt.cross_count,
                "normal_at_friends": tgt.normal_count,
            },
            "config": {
                "daytime_total": cfg.get("daytime_total"),
                "nighttime_total": cfg.get("nighttime_total"),
                "tier": cfg.get("cross_tier"),
                "preference": cfg.get("cross_lot_preference"),
                "cluster": cfg.get("cluster"),
                "avoid_lots": cfg.get("avoid_lots") or [],
            },
            "actions": actions,
            "time_window": "daytime" if is_daytime_window(now) else "nighttime",
        }

        # 純 H5 模式仍維持舊行為；WS 車位計畫啟用時由 12846 一鍵領取，
        # 排程層會傳 False，避免 H5 再次導航到車位倉庫重複領取。
        if claim_warehouse_rewards and claim_warehouse(page):
            actions.append("claimed warehouse rewards")

        # Bot's job per user (2026-05-20 final clarification): auto-park CROSS
        # at the daily 10am window. Recall is NOT bot's job. 一般 is automated
        # by other systems. So we only PARK cross when count < target.
        while snap.cross_count < tgt.cross_count:
            prefer_back = (cfg.get("cross_lot_preference", "back") == "back")
            cluster = bool(cfg.get("cluster", True))
            avoid_lots = cfg.get("avoid_lots") or None
            car_name = park_one_silver(
                page, prefer_back=prefer_back, cluster=cluster,
                avoid_lots=avoid_lots,
            )
            if not car_name:
                actions.append("跳過跨服停車 (無銀空位 / 無可停車)")
                break
            actions.append(f"parked cross: {car_name}")
            snap = take_snapshot(page)
            if snap is None:
                break

        # Note excess cross — recall is delegated to other systems.
        if snap and snap.cross_count > tgt.cross_count:
            actions.append(
                f"excess cross observed: {snap.cross_count} > target {tgt.cross_count} (recall delegated)"
            )

        # 一般 deployment (friend lots, 1-per-friend, max 5) is automated
        # elsewhere (per user 2026-05-20 clarification: "這個部分有自動了 所以你不需要研究").
        # This bot module only manages CROSS deployment. The home_occupied
        # count is NOT the friend-lot deployment count — it's whoever (mine
        # OR foreign players) is parked at MY home. Log it that way.
        if snap is not None:
            home_str = (
                f"{snap.home_occupied}/{snap.home_total}"
                if snap.home_total else "not-visible"
            )
            actions.append(
                f"home park (mine + foreign): {home_str}; "
                f"normal-at-friends target {tgt.normal_count} handled by external system"
            )

        # Refresh the snapshot reflection with final state (after all actions).
        summary["actions"] = actions
        if snap:
            summary["snapshot"] = _build_snapshot_summary(snap)
        return summary
    finally:
        _return_parking_to_main(page)

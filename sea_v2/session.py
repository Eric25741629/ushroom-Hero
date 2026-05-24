"""H5 (cocos) implementation of the SeaSession the daily flow drives.

Thin IO boundary between :mod:`sea_v2.tasks` and the live game. Pure decision logic
lives in :mod:`sea_v2.tiles` / :mod:`sea_v2.navigator`.

Clicking: season UI buttons are custom components where ``node.emit('click')`` is
unreliable (verified on 5554: 一鍵修築 ignored emit but responded to a real tap). So we
tap the node's pixel — read ``node.worldPosition`` (UI space == 720x1280 logical) and map
it ``px = wx*0.75, py = (1280-wy)*0.75`` into the 540x960 frame, then ``mouse.click``.

Verified live (2026-05-25, night): enter, 定位, read objects, closed-loop bring_on_screen
(no overshoot), reward-claim node = active ``btnGet``, and the full repair path
港口 → 維修站 → 一鍵修築 (currently gated on 木材 materials → ``ItemGetWayView`` popup).

階段 B (needs daytime, 深夜無法行動): garrison/attack action-menu nodes; and 一鍵修築
actually completing once 木材 has been earned by sailing.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from sea_v2 import navigator as nav
from sea_v2 import tiles as T

logger = logging.getLogger(__name__)

# --- node paths (verified live) --------------------------------------------------
P_SEASON_BTN = "/UIRoot/NormalView/MainView/top/systemTop/btnRoot/btnSeason"
P_LOCATE = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnLocate"
P_TASK = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnTask"
P_TASK_CLOSE = "/UIRoot/NormalView/SeasonTask4View/btnClose"
P_SEASON_CLOSE = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnClose"
P_PORT = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnPort"
P_RESTORE_STATION = "/UIRoot/NormalView/SeasonMainView/bottom/btnRestore"   # 維修站
P_ONE_CLICK_REPAIR = "/UIRoot/NormalView/SeasonRestoreView/root/bot/btnRestore"  # 一鍵修築
P_RESTORE_CLOSE = "/UIRoot/NormalView/SeasonRestoreView/root/bot/btnClose"
P_PORT_CLOSE = "/UIRoot/NormalView/SeasonMainView/bottom/btnClose"
P_ITEMGETWAY = "/UIRoot/NormalView/ItemGetWayView"                # 材料不足 popup
P_ITEMGETWAY_CLOSE = "/UIRoot/NormalView/ItemGetWayView/node/btnClose"

_JS_FIND = (
    "const f=(r,ps)=>{let n=r;for(const p of ps){if(!p)continue;if(!n.children)return null;"
    "const c=n.children.find(k=>(k.name||'')===p);if(!c)return null;n=c;}return n;};"
)
_JS_NODE = (
    "(path)=>{%s const n=f(cc.director.getScene(),path.split('/'));if(!n)return null;"
    "return {wx:n.worldPosition.x,wy:n.worldPosition.y,active:n.active};}" % _JS_FIND
)
_JS_SEASON_LOADED = "()=>{%s return !!f(cc.director.getScene(),'/SeasonMapScene'.split('/'));}" % _JS_FIND
_JS_OBJS = (
    "()=>{%s const objc=f(cc.director.getScene(),'/SeasonMapScene/unit/obj'.split('/'));"
    "const cam=f(cc.director.getScene(),'/SeasonMapScene/SceneCamera'.split('/'));"
    "return {cam:cam?{x:cam.worldPosition.x,y:cam.worldPosition.y}:null,"
    "objs:(objc?objc.children:[]).map(c=>({name:c.name,wp:{x:c.worldPosition.x,y:c.worldPosition.y}}))};}" % _JS_FIND
)
_JS_W2S = (
    "(wp)=>{%s const camNode=f(cc.director.getScene(),'/SeasonMapScene/SceneCamera'.split('/'));"
    "const cam=camNode.getComponent('cc.Camera');const s=cam.worldToScreen({x:wp[0],y:wp[1],z:0});"
    "return {x:s.x,y:s.y};}" % _JS_FIND
)
_JS_CLAIM_POSITIONS = (  # world positions of ACTIVE btnGet under the task view
    "()=>{%s const tv=f(cc.director.getScene(),'/UIRoot/NormalView/SeasonTask4View'.split('/'));"
    "const out=[];if(!tv)return out;const walk=(x)=>{if(!x||!x.active)return;"
    "if((x.name||'')==='btnGet'){out.push({wx:x.worldPosition.x,wy:x.worldPosition.y});}"
    "(x.children||[]).forEach(walk);};walk(tv);return out;}" % _JS_FIND
)


def _ui_pixel(wx: float, wy: float) -> Tuple[float, float]:
    """UI node worldPosition (720x1280 logical, y-up) -> 540x960 frame pixel (y-down)."""
    return (wx * nav.FRAME_W / nav.DESIGN_W, (nav.DESIGN_H - wy) * nav.FRAME_H / nav.DESIGN_H)


class H5SeaSession:
    def __init__(self, page, settle: float = 1.2):
        self.page = page
        self.settle = settle

    # -- low-level helpers --
    def _node(self, path: str) -> Optional[dict]:
        return self.page.evaluate(_JS_NODE, path)

    def _view_open(self, path: str) -> bool:
        n = self._node(path)
        return bool(n and n.get("active"))

    def _tap(self, path: str, wait: float = None) -> bool:
        """Real pixel tap on a UI node by its worldPosition. False if node missing."""
        n = self._node(path)
        if not n:
            logger.debug("[sea] node not found: %s", path)
            return False
        px, py = _ui_pixel(n["wx"], n["wy"])
        self.page.mouse.click(px, py)
        time.sleep(self.settle if wait is None else wait)
        return True

    def _drag(self, a, b, steps: int = 16) -> None:
        self.page.mouse.move(a[0], a[1])
        self.page.mouse.down()
        for i in range(1, steps + 1):
            self.page.mouse.move(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
            time.sleep(0.01)
        self.page.mouse.up()

    # -- session interface --
    def enter_season(self) -> bool:
        if self.page.evaluate(_JS_SEASON_LOADED):
            return True
        self._tap(P_SEASON_BTN)
        for _ in range(20):
            if self.page.evaluate(_JS_SEASON_LOADED):
                time.sleep(self.settle)
                return True
            time.sleep(0.5)
        logger.warning("[sea] SeasonMapScene did not load")
        return False

    def locate(self) -> Optional[Tuple[float, float]]:
        self._tap(P_LOCATE)
        st = self.page.evaluate(_JS_OBJS)
        return (st["cam"]["x"], st["cam"]["y"]) if st.get("cam") else None

    def read_objects(self) -> List[dict]:
        return self.page.evaluate(_JS_OBJS)["objs"]

    def bring_on_screen(self, tile: T.Tile, max_steps: int = 8, margin: float = 120.0) -> bool:
        """Closed-loop pan until ``tile`` is inside the click-safe frame. No overshoot:
        each step is magnitude-capped and re-measured against worldToScreen feedback."""
        for _ in range(max_steps):
            res = self.page.evaluate(_JS_W2S, [tile.wx, tile.wy])
            px = nav.world_to_pixel(res["x"], res["y"])
            if nav.is_on_screen(px, margin=margin):
                return True
            start, end = nav.center_drag(px, gain=0.9, max_step=200)
            self._drag(start, end)
            time.sleep(1.0)
        res = self.page.evaluate(_JS_W2S, [tile.wx, tile.wy])
        return nav.is_on_screen(nav.world_to_pixel(res["x"], res["y"]), margin=margin)

    def use_repair_kit(self) -> bool:
        """港口 → 維修站 → 一鍵修築. Returns True if a repair fired, False if blocked on
        materials (``ItemGetWayView`` popup). Always closes the views it opened."""
        repaired = False
        try:
            if not self._tap(P_PORT):
                return False
            if not self._tap(P_RESTORE_STATION):
                return False
            self._tap(P_ONE_CLICK_REPAIR)
            if self._view_open(P_ITEMGETWAY):
                logger.info("[sea] repair blocked: insufficient 木材 (ItemGetWayView)")
                self._tap(P_ITEMGETWAY_CLOSE)
                repaired = False
            else:
                repaired = True
            return repaired
        finally:
            # tapping a missing node is a harmless no-op, so close both views unconditionally
            self._tap(P_RESTORE_CLOSE, wait=0.5)
            self._tap(P_PORT_CLOSE, wait=0.5)

    def garrison(self, tile: T.Tile) -> bool:
        logger.info("[sea][TODO-B] garrison %s — needs daytime action-menu mapping", tile.wp)
        return False

    def attack(self, tile: T.Tile) -> bool:
        logger.info("[sea][TODO-B] attack %s — needs daytime action-menu mapping", tile.wp)
        return False

    def claim_rewards(self) -> int:
        self._tap(P_TASK)
        positions = self.page.evaluate(_JS_CLAIM_POSITIONS) or []
        for pos in positions:
            px, py = _ui_pixel(pos["wx"], pos["wy"])
            self.page.mouse.click(px, py)
            time.sleep(0.4)
        self._tap(P_TASK_CLOSE, wait=0.5)
        return len(positions)

    def exit_season(self) -> None:
        self._tap(P_SEASON_CLOSE, wait=0.5)

"""H5 (cocos) implementation of the SeaSession the daily flow drives.

Thin IO boundary between :mod:`sea_v2.tasks` and the live game. Pure decision logic
lives in :mod:`sea_v2.tiles` / :mod:`sea_v2.navigator`.

Two click strategies, by element kind:

* **UI buttons** (HUD / dialogs): tap the node's pixel — ``node.worldPosition`` is in
  720x1280 UI space → ``px = wx*0.75, py = (1280-wy)*0.75`` into the 540x960 frame, then
  ``mouse.click``. (``emit('click')`` no-ops on custom widgets; pixel tap is reliable.)
* **Map tiles + their action buttons** (resource/relic select, 駐守/進攻/領取):
  ``worldToScreen`` pixel-taps SELECT-MISS the tile, so these are driven by **OCR-clicking
  the rendered label** (verified 2026-05-25 on 5560). See ``_ocr_tap`` / ``garrison`` /
  ``attack`` / ``claim_rewards``. World-coords only decide the pan *direction*.

Verified live (2026-05-25): enter, 定位, read objects, closed-loop bring_on_screen,
garrison end-to-end (資源→駐守→開始航行→行軍中), reward claim, attack-select (進攻 button),
and the ship-repair gate (底部維修→維修點, blocked unless ship at 大本營).

待補: auto-returning the ship to 大本營 so 使用1次修船套件 can complete; ADB backend (階段 C).
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
# 修船 (daily 使用1次修船套件): 地圖底部「維修」→ SeasonRepairView → 用維修點修船。
# 需船停在大本營；出航時點修會跳『僅位於大本營時才能維修船隻』(2026-05-25 5560 實測)。
# NB: 港口→維修站→「一鍵修築」是另一回事(用木材升級維修站建築)，**不會**讓「使用1次修船套件」打勾。
P_REPAIR_OPEN = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/di/btnRepair"  # 底部「維修」
P_REPAIR_VIEW = "/UIRoot/NormalView/SeasonRepairView"
P_SHIP_REPAIR = "/UIRoot/NormalView/SeasonRepairView/root/bot/btnRepair"           # 「維修」鈕
P_REPAIR_CLOSE = "/UIRoot/NormalView/SeasonRepairView/root/bot/btnClose"
# 升級維修站 (一鍵修築, 用木材): 港口 → 維修站 → 一鍵修築。提升維修點上限/恢復速率。
# 缺木材跳 ItemGetWayView。**關閉港口會離開賽季** → 流程排最後。
P_PORT = "/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnPort"              # 港口
P_PORT_VIEW = "/UIRoot/NormalView/SeasonMainView"
P_RESTORE_STATION = "/UIRoot/NormalView/SeasonMainView/bottom/btnRestore"          # 維修站
P_RESTORE_VIEW = "/UIRoot/NormalView/SeasonRestoreView"
P_ONE_CLICK_BUILD = "/UIRoot/NormalView/SeasonRestoreView/root/bot/btnRestore"     # 一鍵修築
P_RESTORE_CLOSE = "/UIRoot/NormalView/SeasonRestoreView/root/bot/btnClose"
P_PORT_CLOSE = "/UIRoot/NormalView/SeasonMainView/bottom/btnClose"
P_ITEMGETWAY = "/UIRoot/NormalView/ItemGetWayView"                                 # 材料不足 popup
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
# Action menu lives in the MAP scene (not UIRoot): selecting a tile populates
# /SeasonMapScene/unit/select with the available action button(s). The button label
# tells the action: 駐守 (garrison a free tile) / 進攻 (attack an enemy tile or relic).
_JS_SELECT_MENU = (
    "()=>{%s const s=f(cc.director.getScene(),'/SeasonMapScene/unit/select'.split('/'));"
    "const out=[];if(!s)return out;const walk=(n)=>{if(!n||!n.active)return;"
    "if((n.name||'')==='txtName'){for(const c of(n._components||[])){"
    "const v=c&&(c.string!==undefined?c.string:c._string);"
    "if(typeof v==='string'&&v.trim()){out.push(v.trim());break;}}}"
    "(n.children||[]).forEach(walk);};walk(s);return out;}" % _JS_FIND
)
# count of ACTIVE claim buttons left under the task view (claim-loop terminator)
_JS_CLAIMABLE_COUNT = (
    "()=>{%s const tv=f(cc.director.getScene(),'/UIRoot/NormalView/SeasonTask4View'.split('/'));"
    "let n=0;if(!tv)return 0;const walk=(x)=>{if(!x)return;"
    "if((x.name||'')==='btnGet'&&x.active)n++;(x.children||[]).forEach(walk);};walk(tv);return n;}" % _JS_FIND
)

# 中心參考點：bring_on_screen 會把目標 tile 帶到畫面中央附近
_FRAME_CENTER = (nav.FRAME_W / 2.0, nav.FRAME_H / 2.0)


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
        from utils import pause_guard
        pause_guard.check()
        n = self._node(path)
        if not n:
            logger.debug("[sea] node not found: %s", path)
            return False
        px, py = _ui_pixel(n["wx"], n["wy"])
        self.page.mouse.click(px, py)
        time.sleep(self.settle if wait is None else wait)
        return True

    def _drag(self, a, b, steps: int = 16) -> None:
        from utils import pause_guard
        pause_guard.check()
        self.page.mouse.move(a[0], a[1])
        self.page.mouse.down()
        for i in range(1, steps + 1):
            self.page.mouse.move(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
            time.sleep(0.01)
        self.page.mouse.up()

    # -- OCR helpers (label-clicking is reliable; worldToScreen pixel-tap misses tiles) --
    def _ocr_results(self) -> List[dict]:
        """Run the project OCR on a fresh screenshot. Returns ``[{text, bbox=[x1,y1,x2,y2]}]``.

        Lazy-imports the heavy deps so importing ``sea_v2`` (e.g. in unit tests) stays cheap.
        """
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import img_tools  # noqa: PLC0415

        png = self.page.screenshot()
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        res = img_tools.analyze_skill_via_http(arr) or {}
        if res.get("success") is False:
            logger.warning("[sea] OCR failed: %s", res.get("error"))
            return []
        return res.get("ocr_results") or []

    @staticmethod
    def _ocr_matches(results, target: str, exclude: Optional[str] = None) -> List[Tuple[float, float, str]]:
        """Centres of OCR boxes whose text contains ``target`` (繁簡-normalised).

        ``exclude``: drop boxes whose text contains this (e.g. 領取 must not match 已領取,
        駐守 must not match 駐守中). Exact-text hits are preferred when present, so an exact
        "駐守"/"領取" button wins over a longer accidental superstring.
        """
        import opencc  # noqa: PLC0415

        s2t = opencc.OpenCC("s2t")
        target = s2t.convert(target)
        hits = []
        for it in results or []:
            t = s2t.convert(it.get("text", ""))
            if target in t and (exclude is None or exclude not in t):
                x1, y1, x2, y2 = it["bbox"]
                hits.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0, t))
        exact = [h for h in hits if h[2] == target]
        return exact or hits

    def _ocr_tap(self, target: str, exclude: Optional[str] = None,
                 near: Optional[Tuple[float, float]] = None, wait: float = 1.3) -> bool:
        """Tap the OCR match for ``target``. When ``near`` is given, tap the closest box
        to that point (used to pick the centred tile among several same-typed labels)."""
        from utils import pause_guard
        pause_guard.check()
        hits = self._ocr_matches(self._ocr_results(), target, exclude=exclude)
        if not hits:
            return False
        if near is not None:
            hits.sort(key=lambda h: (h[0] - near[0]) ** 2 + (h[1] - near[1]) ** 2)
        cx, cy, _ = hits[0]
        self.page.mouse.click(cx, cy)
        time.sleep(wait)
        return True

    def _select_menu(self) -> List[str]:
        """Action labels currently offered for the selected map tile (駐守 / 進攻 / ...)."""
        return self.page.evaluate(_JS_SELECT_MENU) or []

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

    def use_repair_kit(self, retries: int = 6, wait_step: float = 4.0) -> bool:
        """修船 (daily 使用1次修船套件): 底部「維修」→ SeasonRepairView → 「維修」鈕(維修點)。

        Ship can only be repaired at 大本營 (else the game toasts 『僅位於大本營時才能維修
        船隻』). Repair runs FIRST each day because the ship auto-returned to 大本營 from
        yesterday's attack, so it should already be docked when today's flow starts.
        Retries are kept in case of timing edge cases. The repair popup is a map overlay,
        so closing it does NOT exit the season."""
        try:
            if not self._tap(P_REPAIR_OPEN):
                return False
            if not self._view_open(P_REPAIR_VIEW):
                logger.info("[sea] repair view did not open")
                return False
            for attempt in range(max(1, retries)):
                self._tap(P_SHIP_REPAIR)
                texts = [it.get("text", "") for it in self._ocr_results()]
                if not any(("大本營" in t) or ("僅位於" in t) for t in texts):
                    logger.info("[sea] ship repair fired (維修點)")
                    return True
                logger.info("[sea] ship not at 大本營 yet (returning after 攻略遺跡); retry %d/%d",
                            attempt + 1, retries)
                time.sleep(wait_step)
            logger.info("[sea] repair gave up: ship did not return to 大本營 in time")
            return False
        finally:
            self._tap(P_REPAIR_CLOSE, wait=0.5)

    def _wait_view(self, path: str, timeout: float = 4.0, step: float = 0.4) -> bool:
        """Poll until a view node is active (heavy views like 港口 load slowly)."""
        end = time.time() + timeout
        while time.time() < end:
            if self._view_open(path):
                return True
            time.sleep(step)
        return False

    def upgrade_repair_station(self) -> bool:
        """一鍵修築: 港口 → 維修站 → 一鍵修築 (用木材升級維修站建築，提升維修點上限/恢復速率)。

        This is NOT the 使用1次修船套件 daily task (that's :meth:`use_repair_kit`); it's a
        separate base-build the user wants kept in the flow. Returns False if blocked on
        木材 (``ItemGetWayView``). **Closing 港口 exits the season**, so run this LAST."""
        built = False
        try:
            if not self._tap(P_PORT):
                return False
            if not self._wait_view(P_PORT_VIEW):     # 港口 view is heavy; wait before tapping 維修站
                logger.info("[sea] 港口 view did not open")
                return False
            if not self._tap(P_RESTORE_STATION):
                return False
            self._wait_view(P_RESTORE_VIEW)
            self._tap(P_ONE_CLICK_BUILD)
            if self._view_open(P_ITEMGETWAY):
                logger.info("[sea] 一鍵修築 blocked: insufficient 木材 (ItemGetWayView)")
                self._tap(P_ITEMGETWAY_CLOSE)
                built = False
            else:
                logger.info("[sea] 一鍵修築 fired (維修站升級)")
                built = True
            return built
        finally:
            self._tap(P_RESTORE_CLOSE, wait=0.5)
            self._tap(P_PORT_CLOSE, wait=0.5)        # 關港口 = 離開賽季

    def garrison(self, tile: T.Tile, max_candidates: int = 4) -> bool:
        """駐守 the centred resource: select a 資源 label, and if its action menu offers
        駐守 (free tile) fire 駐守 → 開始航行. Tries the nearest few 資源 labels because the
        closest tile may already be ours (駐守中, no 駐守 action). Verified live 2026-05-25."""
        time.sleep(self.settle)  # let labels render after the caller's pan settles
        tried = 0
        seen = set()
        while tried < max_candidates:
            hits = self._ocr_matches(self._ocr_results(), "資源", exclude=None)
            hits = [h for h in hits if (round(h[0]), round(h[1])) not in seen]
            if not hits:
                break
            hits.sort(key=lambda h: (h[0] - _FRAME_CENTER[0]) ** 2 + (h[1] - _FRAME_CENTER[1]) ** 2)
            cx, cy, _ = hits[0]
            seen.add((round(cx), round(cy)))
            tried += 1
            self.page.mouse.click(cx, cy)
            time.sleep(self.settle)
            if "駐守" in self._select_menu():
                if not self._ocr_tap("駐守", exclude="中", near=(cx, cy)):
                    continue
                self._ocr_tap("開始航行")
                ok = self._verify_dispatched()
                logger.info("[sea] garrison %s dispatched=%s", tile.wp, ok)
                return ok
        logger.info("[sea] garrison %s: no free 資源 (駐守) tile among %d candidates", tile.wp, tried)
        return False

    def attack(self, tile: T.Tile) -> bool:
        """進攻 the centred relic: select it (OCR drops a stroke -> match 跡) and if the menu
        offers 進攻, fire 進攻 → 開始航行. Same select→action→開始航行 shape as garrison."""
        time.sleep(self.settle)  # let labels render after the caller's pan settles
        if not self._ocr_tap("跡", near=_FRAME_CENTER):  # 遺跡, robust single char
            logger.info("[sea] attack %s: relic label not found on screen", tile.wp)
            return False
        if "進攻" not in self._select_menu():
            logger.info("[sea] attack %s: 進攻 not offered (menu=%s)", tile.wp, self._select_menu())
            return False
        self._ocr_tap("進攻")
        self._ocr_tap("開始航行")
        ok = self._verify_dispatched()
        logger.info("[sea] attack %s dispatched=%s", tile.wp, ok)
        return ok

    def _verify_dispatched(self) -> bool:
        """A dispatched fleet shows 行軍中 (marching). Short substring (OCR drops chars)."""
        texts = [it.get("text", "") for it in self._ocr_results()]
        return any("行軍" in t or "行车" in t or "行車" in t for t in texts)

    def claim_rewards(self, max_iters: int = 8) -> int:
        """Open the task panel and claim every completed reward. Claims by OCR-tapping the
        領取 button (excludes 已領取), looping because the list reflows after each claim.
        Terminates on the cocos active-btnGet count, not on OCR alone."""
        if not self._view_open("/UIRoot/NormalView/SeasonTask4View"):
            self._tap(P_TASK)
        start = int(self.page.evaluate(_JS_CLAIMABLE_COUNT) or 0)
        remaining = start
        stale = 0
        for _ in range(max_iters):
            if remaining <= 0:
                break
            if not self._ocr_tap("領取", exclude="已", wait=0.8):
                break
            now = int(self.page.evaluate(_JS_CLAIMABLE_COUNT) or 0)
            stale = stale + 1 if now >= remaining else 0
            remaining = now
            if stale >= 2:  # tapped twice with no progress (e.g. off-screen claim) -> stop
                break
        self._tap(P_TASK_CLOSE, wait=0.5)
        claimed = max(0, start - remaining)
        logger.info("[sea] claimed %d reward(s)", claimed)
        return claimed

    def exit_season(self) -> None:
        self._tap(P_SEASON_CLOSE, wait=0.5)

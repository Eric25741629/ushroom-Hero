"""Experimental: drive cocos navigation by calling `node.emit('click', node)`
through Playwright CDP, instead of synthesising taps on coordinates.

Why this exists:
  Coordinate taps require a screenshot+OCR loop to verify the page actually
  switched (animations, popups, stale UI). Cocos emit-click reaches the same
  game logic the human button press would — the game itself sends the WS
  cmd, updates state, and re-renders. We just verify by re-querying the
  scene tree.

Scope (today):
  - "home"  — 主頁 → 家園 (MysteryMainView)
  - "farm"  — 主頁/家園 → 農場 (PlantMainView)
  - "main"  — close any overlay + toggle off any selected tab → 主頁面

Gated behind a per-device `experimental_cocos_navigation` flag. Only enabled
devices route through this path; the others keep their coordinate-based
navigation untouched.

The flag check + page lookup happens in `try_cocos_navigate`, which is the
sole entry point callers should use. It returns `Optional[bool]`:
  - `None`  : not applicable (flag off, or device has no Playwright page).
              Caller MUST fall back to its original navigation.
  - `True`  : succeeded — caller can skip its original logic.
  - `False` : attempted but failed — caller MUST fall back.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import config_manager

logger = logging.getLogger(__name__)


# Forward-declare for the type hint on CocosNavigator.current_page()
# (avoids a top-level import that would slow startup for callers that
# don't need the detector).
def _lazy_page_detector():
    from utils.page_detector import PageDetector  # local import on first use
    return PageDetector


# ───────────────────────────────────────────────────────────────────
# Cocos node paths (Cocos Creator 3.6.3 build, 2026-05 schema).
# Update here if a game patch renames any of these.
# ───────────────────────────────────────────────────────────────────

COCOS_PATHS = {
    # The 6 tab cells under the bottom navigation:
    # content/1=角色, /2=同伴, /3=副本, /4=家園, /6=家族, /5=商店
    "home_tab": "/UIRoot/NormalView/MainView/tab/scrollTab/view/content/4",

    # MysteryMainView entry items (家園 page's clickable buildings):
    "farm_node":        "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Farm",
    "mine_node":        "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Mine",
    "statue_node":      "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/FarmStatue",
    "carpark_node":     "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/CarPark",
    "workshop_node":    "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/WorkShop",
    "marry_node":       "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Marry",
    "science_node":     "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Science",
    "mystery_shop_node": "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/mysteryShop",

    # Sub-view close buttons (each subview has its own close at a similar path):
    "farm_close_btn":   "/UIRoot/NormalView/PlantMainView/bottom/btnClose",
}


# Mapping of overlay view names → which view is open. Matched by walking the
# active scene tree once and looking for these names under /UIRoot/NormalView/.
_HOME_OVERLAY_VIEWS = {
    "PlantMainView",        # 農場
    "MysteryMineView",      # 礦山
    "StatueView",           # 菇菇雕像
    "ParkingMainView",      # 菇菇車位
    "WorkShopView",         # 加工坊
    "MarryMainView",        # 比格先生 (婚禮)
    "ScienceView",          # 科技
    "MysteryStoreView",     # 神秘商人
}


# ───────────────────────────────────────────────────────────────────
# JS snippets run in the page via page.evaluate(...).
# ───────────────────────────────────────────────────────────────────

# Click a node by path; returns {clicked, path, name?, active?} or {err}.
_CLICK_JS = r"""
([path]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked: false, err: 'no_cc', path};
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      const next = n.children.find(c => (c.name || '') === p);
      if (!next) return null;
      n = next;
    }
    return n;
  };
  const node = find(scene, path.split('/').filter(Boolean));
  if (!node) return {clicked: false, err: 'path_not_found', path};
  try {
    if (node.emit) node.emit('click', node);
    return {clicked: true, path, name: node.name, active: !!node.active};
  } catch (e) {
    return {clicked: false, err: String(e), path};
  }
}
"""


# Probe the active view state. Returns:
#   {main_active, home_active, farm_active, tab_selected_idx, open_overlay_views}
# - main_active: MainView (NormalView's main panel) is active
# - home_active: MysteryMainView prefab is active
# - farm_active: PlantMainView prefab is active
# - tab_selected_idx: which tab's "selected" sprite is on (None = none selected)
# - open_overlay_views: list of *View names currently active under NormalView,
#                       excluding MainView
_VIEW_STATE_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {err: 'no_cc'};
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      const next = n.children.find(c => (c.name || '') === p);
      if (!next) return null;
      n = next;
    }
    return n;
  };

  const out = {main_active: false, home_active: false, farm_active: false,
               tab_selected_idx: null, open_overlay_views: []};
  const mainView = find(scene, '/UIRoot/NormalView/MainView'.split('/').filter(Boolean));
  out.main_active = !!(mainView && mainView.active);

  // MysteryMainView — inner one is active when the home view is "loaded".
  const mInner = find(scene, '/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView'.split('/').filter(Boolean));
  out.home_active = !!(mInner && mInner.active);

  // PlantMainView is the farm overlay.
  const plant = find(scene, '/UIRoot/NormalView/PlantMainView'.split('/').filter(Boolean));
  out.farm_active = !!(plant && plant.active);

  // Walk NormalView children to find any overlay view that's currently active.
  const normalView = find(scene, '/UIRoot/NormalView'.split('/').filter(Boolean));
  if (normalView) {
    for (const v of normalView.children || []) {
      if (v.name === 'MainView' || !v.active) continue;
      if (/View$/.test(v.name || '')) out.open_overlay_views.push(v.name);
    }
  }

  // Inspect each tab cell's children to detect a "selected" state. We look
  // for an active child node named 'select', 'imgSelect', or similar.
  const tabContent = find(scene, '/UIRoot/NormalView/MainView/tab/scrollTab/view/content'.split('/').filter(Boolean));
  if (tabContent && tabContent.children) {
    for (let i = 0; i < tabContent.children.length; i++) {
      const cell = tabContent.children[i];
      const sel = (cell.children || []).find(c =>
        (c.name === 'select' || c.name === 'imgSelect' || /select/i.test(c.name || '')));
      if (sel && sel.active) { out.tab_selected_idx = i; break; }
    }
  }

  return out;
}
"""


# Find an active close button on the topmost open overlay view. Returns
# {paths: [...]} with the highest-priority close button first.
#
# Strategy:
#  - Walk NormalView's active overlay children in REVERSE order — last child
#    is the topmost-rendered in cocos, so we close that first.
#  - For each overlay, score candidates: exact `btnClose` > contains `close`
#    > contains `back`/`return`. `btnReturn` (which fires a feature-specific
#    action like "scroll-back-to-top" not "close the view") is intentionally
#    deprioritized.
# Dismiss popups that `sweep_popups` (NormalView-only) leaves behind: the
# TopView confirm dialog (MessageView/boxTips) and common reward/get popups
# closed via the UI manager. These matter because emit-click navigation
# bypasses the visual hit-test — if one of these is still on screen, the
# carpark/farm node click fires anyway and strands the popup on top.
_DISMISS_TOP_POPUPS_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {closed: 0};
  let closed = 0;
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p);
      if (!n) return null;
    }
    return n;
  };

  // 1) Close known reward / tip / notice popups through the UI manager. getView
  //    returns falsy for views that aren't open, so listing extras is harmless.
  const um = window.uiMgr;
  if (um && um.getView && um.close) {
    const views = ['GoodsGetView', 'GoodsGetView2', 'ItemTipsView', 'RedBagShowView',
                   'RewardView', 'NoticeView', 'SignView', 'TipsView'];
    for (const v of views) {
      try { if (um.getView(v)) { um.close(v); closed++; } } catch (e) {}
    }
  }

  // 2) Hide the TopView confirm dialog (boxTips) if it's up.
  const bt = find(scene, ['UIRoot', 'TopView', 'MessageView', 'boxTips']);
  if (bt && bt.active) { bt.active = false; closed++; }

  // 3) Walk any *other* active overlay under TopView and click its close button,
  //    for popups we don't have a UI-manager name for.
  const scoreName = (name) => {
    const l = (name || '').toLowerCase();
    if (l === 'btnclose') return 5;
    if (l.includes('close')) return 4;
    if (l === 'btncancel') return 3;
    if (l === 'btnback') return 2;
    return 0;
  };
  const topView = find(scene, ['UIRoot', 'TopView']);
  if (topView && topView.children) {
    for (const v of [...topView.children].reverse()) {
      if (!v.active || v.name === 'MessageView' || !/View$/.test(v.name || '')) continue;
      let best = null, bestScore = 0;
      const walk = (node, depth) => {
        if (!node || depth > 12 || !node.active) return;
        const s = scoreName(node.name);
        if (s > bestScore) { bestScore = s; best = node; }
        for (const k of (node.children || [])) walk(k, depth + 1);
      };
      walk(v, 0);
      if (best && best.emit) { try { best.emit('click', best); closed++; } catch (e) {} }
    }
  }
  return {closed};
}
"""


_FIND_CLOSE_BTN_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {paths: []};
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p);
      if (!n) return null;
    }
    return n;
  };

  const scoreName = (name) => {
    const l = (name || '').toLowerCase();
    if (l === 'btnclose') return 5;
    if (l.includes('close')) return 4;
    if (l === 'btncancel') return 3;  // dismiss confirmation dialogs
    if (l === 'btnback') return 2;
    if (l.includes('back') && !l.includes('background')) return 1;
    return 0;
  };

  const collectIn = (root, rootPath) => {
    const hits = [];
    const walk = (node, path, depth) => {
      if (!node || depth > 14 || !node.active) return;
      const s = scoreName(node.name);
      if (s > 0) hits.push({path, score: s});
      if (node.children) {
        for (const k of node.children) walk(k, path + '/' + (k.name || '?'), depth + 1);
      }
    };
    walk(root, rootPath, 0);
    hits.sort((a, b) => b.score - a.score);
    return hits.map(h => h.path);
  };

  const normalView = find(scene, ['UIRoot', 'NormalView']);
  if (!normalView) return {paths: []};
  const overlays = (normalView.children || [])
    .filter(c => c.active && c.name !== 'MainView');

  const out = [];
  // Reverse → topmost overlay first.
  for (let i = overlays.length - 1; i >= 0; i--) {
    const v = overlays[i];
    const candidates = collectIn(v, '/UIRoot/NormalView/' + v.name);
    for (const c of candidates) out.push(c);
    if (out.length >= 6) break;
  }
  return {paths: out.slice(0, 6)};
}
"""


# ───────────────────────────────────────────────────────────────────
# CocosNavigator — drives the page via Playwright.
# ───────────────────────────────────────────────────────────────────

_SETTLE_SEC = 1.5  # how long to wait after a click for cocos to update state


def close_open_notice(page: Any, timeout: float = 5.0) -> bool:
    """關閉全域 ``TopView/NoticeView`` 公告並驗證已消失。"""
    try:
        result = page.evaluate(r"""() => {
          if (typeof cc === 'undefined' || !cc.director)
            return {found:false, clicked:false, err:'no_cc'};
          const scene = cc.director.getScene();
          const find = (root, parts) => {
            let n = root;
            for (const p of parts) {
              if (!n || !n.children) return null;
              n = n.children.find(c => (c.name || '') === p);
              if (!n) return null;
            }
            return n;
          };
          const view = find(scene, ['UIRoot','TopView','NoticeView']);
          if (!view || !view.active) return {found:false, clicked:false};
          const btn = find(view, ['btnClose']);
          if (!btn || !btn.activeInHierarchy || typeof btn.emit !== 'function')
            return {found:true, clicked:false, err:'notice_close_button_unavailable'};
          btn.emit('click', btn);
          return {found:true, clicked:true};
        }""") or {}
    except Exception as exc:
        logger.warning("[cocos_nav] 關閉公告例外: %s", exc)
        return False

    if not result.get("found"):
        return True
    if not result.get("clicked"):
        logger.warning("[cocos_nav] 公告關閉按鈕不可用: %s", result.get("err"))
        return False

    deadline = time.monotonic() + max(0.5, float(timeout))
    while time.monotonic() < deadline:
        try:
            active = page.evaluate(r"""() => {
              const um = window.uiMgr;
              if (um && typeof um.getView === 'function') {
                const v = um.getView('NoticeView');
                if (v && v.node) return !!v.node.active;
              }
              const scene = (typeof cc !== 'undefined' && cc.director)
                ? cc.director.getScene() : null;
              const top = scene && scene.children && scene.children.find(c => c.name === 'UIRoot');
              const tv = top && top.children && top.children.find(c => c.name === 'TopView');
              const nv = tv && tv.children && tv.children.find(c => c.name === 'NoticeView');
              return !!(nv && nv.active);
            }""")
        except Exception as exc:
            logger.warning("[cocos_nav] 驗證公告關閉失敗: %s", exc)
            return False
        if not active:
            logger.info("[cocos_nav] 已關閉全域公告")
            return True
        time.sleep(0.2)

    logger.warning("[cocos_nav] 公告關閉 timeout")
    return False


class CocosNavigator:
    def __init__(self, page: Any) -> None:
        self.page = page

    # ─── Probing ─────────────────────────────────────────────────

    def _view_state(self) -> dict:
        return self.page.evaluate(_VIEW_STATE_JS) or {}

    def current_page(self, ocr_fallback: bool = False) -> Any:
        """Richer page identification — returns a `utils.page_detector.PageState`.

        Distinguishes mine/statue/carpark/workshop/marry/science/mystery_shop
        in addition to main/home/farm that `current_view()` covers. By default
        only uses the cocos fast-path; pass `ocr_fallback=True` to allow OCR
        when the cocos scan can't classify (e.g. during a loading screen).
        """
        from utils.page_detector import PageDetector
        det = PageDetector(self.page, ocr_enabled=ocr_fallback)
        state, _src = det.detect()
        return state

    def current_view(self) -> str:
        """Returns 'main' | 'home' | 'farm' | 'unknown'.

        Priority: farm > home > main. The bottom tab bar ALWAYS has one cell
        selected (角色/同伴/etc.), so tab selection is NOT used to identify
        the page state — only the prefab activation pattern is. The states
        are nested:
          - 主頁面: MainView active, MysteryMainView inactive, no overlay
          - 家園:   MainView + MysteryMainView active, no overlay
          - 農場:   家園 + PlantMainView (NormalView overlay) active
        """
        try:
            st = self._view_state()
        except Exception as e:
            logger.debug(f"[cocos_nav] view_state probe failed: {e}")
            return "unknown"
        if st.get("err"):
            return "unknown"
        if st.get("farm_active"):
            return "farm"
        overlays = st.get("open_overlay_views") or []
        # Any open overlay (Mine/Statue/CarPark/etc.) puts us in a sub-state —
        # NOT home, NOT main. Return "unknown" so the goto_main close-loop
        # keeps closing instead of exiting early. (Earlier versions returned
        # "home" here, which caused the loop to exit prematurely and then
        # the final assertion `current_view == "main"` to fail.)
        if overlays:
            return "unknown"
        if st.get("home_active"):
            return "home"
        if st.get("main_active"):
            return "main"
        return "unknown"

    # ─── Click primitive ──────────────────────────────────────────

    def _click_path(self, path: str) -> bool:
        """Emit click on a node by path. Returns True if cocos found and clicked."""
        try:
            res = self.page.evaluate(_CLICK_JS, [path]) or {}
        except Exception as e:
            logger.debug(f"[cocos_nav] click eval failed for {path}: {e}")
            return False
        if res.get("clicked"):
            logger.debug(f"[cocos_nav] clicked {path}")
            return True
        logger.debug(f"[cocos_nav] click {path} failed: {res.get('err')}")
        return False

    # ─── High-level navigation ────────────────────────────────────

    def goto_home(self) -> bool:
        """Open 家園 tab. Idempotent: if already on home, still re-clicks
        (cheap) and verifies — returns True either way."""
        if not self._click_path(COCOS_PATHS["home_tab"]):
            return False
        time.sleep(_SETTLE_SEC)
        return self.current_view() == "home"

    def goto_farm(self) -> bool:
        """Open 農場. If not already on 家園, navigates there first."""
        view = self.current_view()
        if view == "farm":
            return True
        if view != "home":
            if not self.goto_home():
                return False
        if not self._click_path(COCOS_PATHS["farm_node"]):
            return False
        time.sleep(_SETTLE_SEC)
        return self.current_view() == "farm"

    def sweep_popups(self, max_iter: int = 12) -> int:
        """關閉所有目前可見的 popup overlay。

        和 `goto_main` 不同：本方法只關 overlay (PlantMainView/MysteryMineView/
        各種彈窗)，但 **不會** 切離 home tab。任務迴圈間呼叫此 method 可清掉
        意外彈窗（連線重連、結算、廣告、紅包提示等）而不破壞當前 tab 狀態。

        Returns: 實際成功關閉的彈窗數量。
        """
        closed = 0
        for _ in range(max_iter):
            view = self.current_view()
            if view in {"main", "home", "farm"}:
                break
            try:
                res = self.page.evaluate(_FIND_CLOSE_BTN_JS) or {}
            except Exception as e:
                logger.debug(f"[cocos_nav] sweep close-btn probe failed: {e}")
                break
            paths = res.get("paths") or []
            if not paths:
                break
            if not self._click_path(paths[0]):
                break
            closed += 1
            time.sleep(_SETTLE_SEC * 0.6)
        return closed

    def dismiss_blocking_popups(self) -> int:
        """關閉所有會擋住導航的彈窗：NormalView overlay（透過 `sweep_popups`）
        加上 `sweep_popups` 掃不到的 TopView 彈窗（boxTips/獎勵/公告）。

        emit-click 導航會繞過畫面 hit-test，所以若有彈窗還蓋在最上面，
        後續對 carpark/farm 節點的 click 仍會生效、把彈窗孤立在 stack 上。
        導航前先呼叫本方法可避免這種「沒處理彈窗就導航」的狀態。

        Returns: 大致關閉的彈窗數量（NormalView + TopView）。
        """
        closed = self.sweep_popups()
        try:
            res = self.page.evaluate(_DISMISS_TOP_POPUPS_JS) or {}
            closed += int(res.get("closed", 0))
        except Exception as e:
            logger.debug(f"[cocos_nav] dismiss top popups failed: {e}")
        if closed:
            time.sleep(_SETTLE_SEC * 0.6)
        return closed

    def goto_main(self) -> bool:
        """Close any overlay + leave 家園 → back to 主頁面.

        Steps:
          1. Sweep all overlays via `sweep_popups`.
          2. If we're left on 家園 (MysteryMainView inner active), click the
             家園 tab again — that toggles it off and falls back to 主頁面.
          3. Return True iff `current_view()` is "main".

        All of the above are pure client-side state changes — no WS traffic.
        """
        if self.current_view() == "main":
            return True

        # PlantMainView 是可辨識的 farm 狀態，sweep_popups 會刻意保留它；
        # 因此必須先點畫面底部真正的「關閉」，確認回到家園總覽。
        if self.current_view() == "farm":
            if not self._click_path(COCOS_PATHS["farm_close_btn"]):
                return False
            time.sleep(_SETTLE_SEC)
            if self.current_view() != "home":
                return False

        self.sweep_popups()

        if self.current_view() == "home":
            if not self._click_path(COCOS_PATHS["home_tab"]):
                return False
            time.sleep(_SETTLE_SEC * 0.6)

        return self.current_view() == "main"


# ───────────────────────────────────────────────────────────────────
# Flag + caller entry point
# ───────────────────────────────────────────────────────────────────


def _device_flag_enabled(device_ip: Optional[str]) -> bool:
    if not device_ip:
        return False
    try:
        cfg = config_manager.get_device_config(device_ip) or {}
    except Exception:
        return False
    return bool(cfg.get("experimental_cocos_navigation", False))


def try_cocos_navigate(d: Any, device_ip: Optional[str], target: str) -> Optional[bool]:
    """Caller entry point. See module docstring.

    Returns None when the flag is off, the device is not web_h5 (has no
    `_page`), or the target is unknown — caller must fall back to its
    original navigation. Returns True/False for attempted runs.
    """
    if not _device_flag_enabled(device_ip):
        return None

    page = getattr(d, "_page", None)
    if page is None:
        return None

    if target not in {"main", "home", "farm", "sweep_popups"}:
        logger.debug(f"[cocos_nav] unsupported target {target!r}")
        return None

    try:
        nav = CocosNavigator(page)
        if target == "main":
            ok = nav.goto_main()
        elif target == "home":
            ok = nav.goto_home()
        elif target == "sweep_popups":
            ok = nav.sweep_popups() >= 0  # always succeeds, returns count
        else:  # "farm"
            ok = nav.goto_farm()
        logger.info(f"[cocos_nav] {device_ip} target={target} ok={ok}")
        return bool(ok)
    except Exception as e:
        logger.warning(f"[cocos_nav] {device_ip} target={target} raised: {e}")
        return False

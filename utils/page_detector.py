"""Identify the *page* the game is currently on, using two-tier detection:

1. **Cocos fast-path** (preferred): walk the active scene tree via Playwright
   `page.evaluate(...)` and check which prefab views (under `/UIRoot/NormalView`,
   `MainView/container`, etc.) are active. Sub-millisecond per query.
2. **OCR fallback** (slower, language-tolerant): screenshot the page and run
   `img_tools.get_all_text(...)`, then keyword-match the recognized strings
   against a per-page signature table.

The cocos path is the source of truth when the page binds; OCR exists for
robustness when a game patch renames a prefab or when cocos isn't loaded
yet (loading screens, reconnect).

Public API:
    PageState — enum of known pages
    PageDetector(page, *, ocr_enabled=True)
        .detect() -> (state, source)  source ∈ {"cocos", "ocr", "none"}
        .detect_via_cocos() -> PageState | None
        .detect_via_ocr() -> PageState | None
        .wait_for(state, timeout, poll_interval) -> bool

Used by `utils.cocos_navigator.CocosNavigator.current_page()` and by the
state-machine layer planned for the bot's task scheduler.
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import config_manager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# PageState enum
# ──────────────────────────────────────────────────────────────────────


class PageState(str, Enum):
    """Pages the bot can identify. New entries: add a `PAGE_FINGERPRINTS`
    cocos rule and (optionally) `PAGE_OCR_KEYWORDS` text signature."""

    # Main tab content (NormalView/MainView/container/*)
    MAIN = "main"          # No tab selected visually + no overlay
    ROLE = "role"          # 角色 tab
    PET = "pet"            # 同伴 tab
    DUNGEON = "dungeon"    # 副本 tab
    HOME = "home"          # 家園 tab (MysteryMainView active, no overlay)
    GUILD = "guild"        # 家族 tab
    SHOP = "shop"          # 商店 tab

    # Overlay views (under /UIRoot/NormalView/*View) — opened on top of any tab
    FARM = "farm"                 # PlantMainView (entered from home/Farm)
    MINE = "mine"                 # MysteryMineView (from home/Mine)
    STATUE = "statue"             # StatueView (from home/FarmStatue)
    CARPARK = "carpark"           # ParkingMainView (from home/CarPark)
    CARPARK_WAREHOUSE = "carpark_warehouse"  # ParkingWareHouseView popup
    OFFLINE_REWARD = "offline_reward"       # outlinePopView popup
    GOODS_REWARD = "goods_reward"           # GoodsGetView / 恭喜獲得 popup
    WORKSHOP = "workshop"         # WorkShopView (from home/WorkShop)
    MARRY = "marry"               # MarryMainView (from home/Marry)
    SCIENCE = "science"           # ScienceView (from home/Science)
    MYSTERY_SHOP = "mystery_shop" # MysteryStoreView (from home/mysteryShop)
    EQUIP_EDIT = "equip_edit"     # EquipEditView (equipment editing modal)

    # Top-level non-game states
    NOTICE = "notice"             # TopView/NoticeView 全域公告遮罩
    LOADING = "loading"           # GameLoadingView
    RECONNECT = "reconnect"       # ReconnectView (also active in normal state but children=0)
    GUIDE = "guide"               # GuideView/GuideView inner

    UNKNOWN = "unknown"


# ──────────────────────────────────────────────────────────────────────
# Cocos fingerprint table — primary identification
# ──────────────────────────────────────────────────────────────────────

# Each value is a "view name under NormalView" that uniquely identifies
# the page. Priority order: overlays first (since they cover tabs).
_OVERLAY_TO_STATE: dict[str, PageState] = {
    "PlantMainView":        PageState.FARM,
    "MysteryMineView":      PageState.MINE,
    "StatueView":           PageState.STATUE,
    "ParkingMainView":      PageState.CARPARK,
    "ParkingWareHouseView": PageState.CARPARK_WAREHOUSE,
    "outlinePopView":       PageState.OFFLINE_REWARD,
    "GoodsGetView":         PageState.GOODS_REWARD,
    "GoodsGetView2":        PageState.GOODS_REWARD,
    "WorkShopView":         PageState.WORKSHOP,
    "MarryMainView":        PageState.MARRY,
    "ScienceView":          PageState.SCIENCE,
    "MysteryStoreView":     PageState.MYSTERY_SHOP,
    "EquipEditView":        PageState.EQUIP_EDIT,
    "GameLoadingView":      PageState.LOADING,
    # GuideView is *always* active=true even when no guide is shown; its
    # inner child becomes active only during a guide. Handled separately.
}

# Tab cell name → state when that tab is selected and no overlay is open.
# Tab cells are named by their config order, not their position:
#   content/1=角色, /2=同伴, /3=副本, /4=家園, /6=家族, /5=商店, /role=hidden dup
_TAB_NAME_TO_STATE: dict[str, PageState] = {
    "1": PageState.ROLE,
    "2": PageState.PET,
    "3": PageState.DUNGEON,
    "4": PageState.HOME,
    "6": PageState.GUILD,
    "5": PageState.SHOP,
}


# JS to scan the scene tree. Returns:
#   {
#     "active_global_overlays": [view_name, ...],  # active children of TopView
#     "active_overlays": [view_name, ...],   # active children of NormalView (excluding MainView)
#     "home_active": bool,                    # inner MysteryMainView active
#     "selected_tab_name": str | null,        # name of selected tab cell, e.g. "4"
#     "guide_inner_active": bool,             # GuideView/GuideView is active
#     "loading_inner_active": bool,           # GameLoadingView has active children
#     "err": str | undef                      # if scene tree not ready
#   }
_SCAN_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {err: 'no_cc'};
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

  const out = {active_global_overlays: [], active_overlays: [], home_active: false,
               selected_tab_name: null, guide_inner_active: false,
               loading_inner_active: false};

  // 0. TopView sits above every NormalView page. MessageView is a persistent
  // toast/fight-tip container, not a blocking modal; other active children
  // must win over the page underneath them.
  const topView = find(scene, ['UIRoot', 'TopView']);
  if (topView) {
    for (const v of topView.children || []) {
      if (!v.active || v.name === 'MessageView') continue;
      out.active_global_overlays.push(v.name || '');
    }
  }

  // 1. Overlay views under NormalView (active children other than MainView).
  const normalView = find(scene, ['UIRoot', 'NormalView']);
  if (normalView) {
    for (const v of normalView.children || []) {
      if (v.name === 'MainView' || !v.active) continue;
      // GuideView is always active=true — only signal a guide overlay when its
      // inner child (also named GuideView) is active.
      if (v.name === 'GuideView') {
        const inner = (v.children || []).find(c => c.name === 'GuideView');
        if (inner && inner.active) out.guide_inner_active = true;
        continue;
      }
      out.active_overlays.push(v.name || '');
    }
  }

  // 2. MysteryMainView inner — defines 家園 state when no overlay covers it.
  const mInner = find(scene, ['UIRoot','NormalView','MainView','container','MysteryMainView','MysteryMainView']);
  out.home_active = !!(mInner && mInner.active);

  // 3. Tab selection: scan tab cells, find the one whose "selected" child is active.
  const tabContent = find(scene, ['UIRoot','NormalView','MainView','tab','scrollTab','view','content']);
  if (tabContent && tabContent.children) {
    for (const cell of tabContent.children) {
      const sel = (cell.children || []).find(c => /select/i.test(c.name || ''));
      if (sel && sel.active) { out.selected_tab_name = cell.name; break; }
    }
  }

  // 4. GameLoadingView inner content — separate from "I have a loading view present".
  const loadView = find(scene, ['UIRoot','GameLoadingView']);
  if (loadView && loadView.active && (loadView.children || []).some(c => c.active)) {
    out.loading_inner_active = true;
  }

  return out;
}
"""


# ──────────────────────────────────────────────────────────────────────
# OCR keyword signatures — fallback when cocos detection misses
# ──────────────────────────────────────────────────────────────────────
#
# Each value is a list of (keywords, min_matches) tuples. We OCR the page,
# then check: for each PageState, if ANY of its (keywords, min_matches)
# rules is satisfied — i.e. at least `min_matches` of the keywords appear
# in the OCR'd text list — that state wins. Highest specificity (highest
# min_matches) wins ties.
#
# Keywords are matched as substrings (Traditional Chinese).
_OcrRule = Tuple[Tuple[str, ...], int]
PAGE_OCR_KEYWORDS: dict[PageState, list[_OcrRule]] = {
    PageState.HOME: [(("礦山", "農場", "加工坊"), 2),
                     (("菇菇雕像", "比格先生"), 1)],
    PageState.FARM: [(("種植", "收成"), 1),
                     (("豐收", "成熟"), 1)],
    PageState.MINE: [(("礦山", "礦石"), 1),
                     (("挖礦",), 1)],
    PageState.STATUE: [(("菇菇雕像", "雕像祝福", "祝福加成"), 1)],
    PageState.CARPARK: [(("菇菇車位", "馬廄", "停靠"), 1)],
    PageState.WORKSHOP: [(("加工坊", "合成"), 1)],
    PageState.MARRY: [(("比格先生", "親密度"), 1)],
    PageState.SCIENCE: [(("科技", "研究中"), 1)],
    PageState.MYSTERY_SHOP: [(("神秘商人", "神秘商店"), 1)],
    # Tab content pages — distinguishing text in each tab body
    PageState.ROLE: [(("角色", "戰鬥力", "升星"), 2)],
    PageState.PET: [(("同伴", "夥伴", "出戰"), 2)],
    PageState.DUNGEON: [(("副本", "推圖", "挑戰"), 2)],
    PageState.GUILD: [(("家族", "幫貢", "宣戰"), 2)],
    PageState.SHOP: [(("商店", "限購", "禮包"), 2)],
    PageState.LOADING: [(("載入中", "加載中", "loading"), 1)],
    PageState.RECONNECT: [(("重新連線", "斷線", "重連"), 1)],
}


# ──────────────────────────────────────────────────────────────────────
# Detector
# ──────────────────────────────────────────────────────────────────────


class PageDetector:
    """Cocos-first, OCR-fallback page identifier.

    Cheap and side-effect-free — call `detect()` as often as needed. OCR
    can be disabled via `ocr_enabled=False` for hot loops where the cocos
    scan is enough.
    """

    def __init__(self, page: Any, *, ocr_enabled: bool = True) -> None:
        self.page = page
        self.ocr_enabled = ocr_enabled

    # ─── Public API ────────────────────────────────────────────────

    def detect(self) -> Tuple[PageState, str]:
        """Returns (state, source) where source is 'cocos' | 'ocr' | 'none'."""
        state = self.detect_via_cocos()
        if state is not None:
            return state, "cocos"
        if self.ocr_enabled:
            state = self.detect_via_ocr()
            if state is not None:
                return state, "ocr"
        return PageState.UNKNOWN, "none"

    def detect_via_cocos(self) -> Optional[PageState]:
        try:
            scan = self.page.evaluate(_SCAN_JS) or {}
        except Exception as e:
            logger.debug(f"[page_detector] cocos scan failed: {e}")
            return None
        if scan.get("err"):
            return None
        return _classify_cocos_scan(scan)

    def detect_via_ocr(self) -> Optional[PageState]:
        try:
            png_bytes = self.page.screenshot()
        except Exception as e:
            logger.debug(f"[page_detector] screenshot failed: {e}")
            return None
        texts = _ocr_to_text_list(png_bytes)
        if not texts:
            return None
        return classify_ocr_texts(texts)

    def wait_for(
        self,
        state: PageState,
        timeout: float = 10.0,
        poll_interval: float = 0.3,
    ) -> bool:
        """Block up to `timeout` seconds for `state` to become current.

        Cocos-only (no OCR) — wait_for is meant for tight UI-transition
        loops where the OCR roundtrip would dominate latency.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.detect_via_cocos() == state:
                return True
            time.sleep(poll_interval)
        return False


# ──────────────────────────────────────────────────────────────────────
# Classification helpers — exported so tests can hit them without a page
# ──────────────────────────────────────────────────────────────────────


def _classify_cocos_scan(scan: dict) -> PageState:
    """Translate a `_SCAN_JS` result dict into a PageState."""
    # 1. TopView sits above every NormalView page. Never misclassify a covered
    # page as MAIN/HOME just because its underlying page remains active.
    global_overlays = scan.get("active_global_overlays") or []
    if global_overlays:
        if "NoticeView" in global_overlays:
            return PageState.NOTICE
        return PageState.UNKNOWN

    # 2. Highest-priority states (must check before NormalView overlays).
    if scan.get("loading_inner_active"):
        return PageState.LOADING
    if scan.get("guide_inner_active"):
        return PageState.GUIDE

    # 3. Overlays (NormalView children excluding MainView) — topmost wins.
    overlays = scan.get("active_overlays") or []
    # Prefer the topmost known overlay. Cocos children are ordered from back
    # to front; live 7fe98fc6 has ParkingWareHouseView followed by
    # outlinePopView, so the visible offline-reward popup must win.
    known_states = [
        _OVERLAY_TO_STATE[ov]
        for ov in overlays
        if ov in _OVERLAY_TO_STATE
    ]
    if known_states:
        return known_states[-1]
    # If any overlay was active but unrecognized, return UNKNOWN — better than
    # silently misclassifying as MAIN.
    if overlays:
        return PageState.UNKNOWN

    # 4. Home (MysteryMainView) trumps tab content when active.
    if scan.get("home_active"):
        return PageState.HOME

    # 5. Tab cell selection → role/pet/dungeon/guild/shop.
    tab_name = scan.get("selected_tab_name")
    if tab_name:
        st = _TAB_NAME_TO_STATE.get(str(tab_name))
        if st is not None:
            return st

    # 6. Default: main page (no overlay, no MysteryMainView, no tab matched).
    return PageState.MAIN


def classify_ocr_texts(texts: Sequence[str]) -> Optional[PageState]:
    """Pick the best-matching PageState from an OCR text list.

    For each state, check its rule list; a rule is a (keywords, min_matches)
    pair. A rule passes if `min_matches` of its keywords appear (as
    substrings) somewhere in `texts`. The candidate with the highest passing
    min_matches wins; ties broken by enum order. Returns None if no rule
    passes.
    """
    joined = "\n".join(texts)
    best: Optional[PageState] = None
    best_score = 0
    for state, rules in PAGE_OCR_KEYWORDS.items():
        for keywords, min_matches in rules:
            hits = sum(1 for kw in keywords if kw in joined)
            if hits >= min_matches and min_matches > best_score:
                best = state
                best_score = min_matches
    return best


# ──────────────────────────────────────────────────────────────────────
# Integration helper — legacy stage detection fast-path
# ──────────────────────────────────────────────────────────────────────


def try_detect_main_page_fast(d: Any, device_ip: Optional[str]) -> Optional[str]:
    """Cocos fast-path for the bot's legacy `stage == "主頁面"` check.

    The bot's `get_stage_with_check(d, ip, Cnn_model)` normally takes a
    screenshot and runs OCR (~1–3s). For web_h5 devices with the
    experimental flag on, we can confirm "主頁面" by scanning the cocos
    scene tree in single-digit ms.

    Returns:
        "主頁面"  iff cocos confirms PageState.MAIN
        None     in every other case — caller MUST fall back to the
                 legacy OCR detector. Non-main cocos states (HOME, FARM,
                 MINE, …) also return None: legacy detection can identify
                 popups like 異地登錄/車位倉庫/家族戰 that cocos doesn't
                 know about, so the OCR path must still run.

    Gated by:
        1. device_ip's config has `experimental_cocos_navigation: true`
        2. device's `backend == "web_h5"`
        3. device exposes `_page` (Playwright page) — i.e. session alive
    """
    if not _legacy_fast_path_enabled(device_ip):
        return None
    page = getattr(d, "_page", None)
    if page is None:
        return None
    try:
        det = PageDetector(page, ocr_enabled=False)
        state = det.detect_via_cocos()
    except Exception as e:
        logger.debug(f"[page_detector] fast-path probe failed for {device_ip}: {e}")
        return None
    if state == PageState.MAIN:
        return "主頁面"
    return None


def detect_known_h5_page(d: Any, device_ip: Optional[str]) -> Optional[PageState]:
    """啟動流程用的唯讀 Cocos 探測。

    只回傳能由現有 fingerprint 明確辨識的狀態。未識別 overlay、Cocos 尚未
    載入或非 web_h5 一律回 None，讓呼叫端保留 OCR 安全網。
    """
    if getattr(d, "backend_kind", None) != "web_h5":
        return None
    page = getattr(d, "_page", None)
    if page is None:
        return None
    try:
        state = PageDetector(page, ocr_enabled=False).detect_via_cocos()
    except Exception as exc:
        logger.debug(f"[page_detector] known-page probe failed for {device_ip}: {exc}")
        return None
    if state in (None, PageState.UNKNOWN, PageState.LOADING, PageState.GUIDE):
        return None
    return state


_COCOS_STAGE_MAP = {
    PageState.MAIN: "主頁面",
    PageState.NOTICE: "公告",
    PageState.CARPARK_WAREHOUSE: "車位倉庫",
    PageState.OFFLINE_REWARD: "放置獎勵",
    PageState.GOODS_REWARD: "恭喜獲得",
}


def detect_known_h5_stage(d: Any, device_ip: Optional[str]) -> Optional[str]:
    """Return a legacy stage name from a verified Cocos page state.

    This is deliberately a small bridge for legacy callers. It only returns
    stages whose Cocos fingerprint has been live-verified; unknown H5 states
    still return ``None`` until their adapter is implemented.
    """
    state = detect_known_h5_page(d, device_ip)
    return _COCOS_STAGE_MAP.get(state)


def _legacy_fast_path_enabled(device_ip: Optional[str]) -> bool:
    """Both `experimental_cocos_navigation: true` AND `backend == 'web_h5'`."""
    if not device_ip:
        return False
    try:
        cfg = config_manager.get_device_config(device_ip) or {}
    except Exception:
        return False
    if not cfg.get("experimental_cocos_navigation", False):
        return False
    if str(cfg.get("backend", "")).lower() != "web_h5":
        return False
    return True


def _ocr_to_text_list(png_bytes: bytes) -> List[str]:
    """Decode PNG bytes → ndarray → OCR text list.

    Returns [] on any failure (decoding, OCR server down, etc.) so callers
    can treat OCR as best-effort.
    """
    try:
        import numpy as np
        import cv2
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []
    except Exception as e:
        logger.debug(f"[page_detector] PNG decode failed: {e}")
        return []
    try:
        import img_tools
        return img_tools.get_all_text(img) or []
    except Exception as e:
        logger.debug(f"[page_detector] OCR call failed: {e}")
        return []

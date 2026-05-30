"""Season daily *reward collection* — backend-agnostic (H5 + ADB).

These steps were missing from both the legacy ``Sea.sea`` and the H5 ``sea_v2``
flow: 地圖收益 / 碼頭補給 / 成就 / 任務>目標(攻堅戰里程碑). They are *claims*, not
map actions, so they work even inside the 深夜無法行動 night window.

Why one module for both backends: the device wrapper (:class:`device_wrapper`) exposes
the SAME ``click`` / ``screenshot`` / ``press`` API for the adb (uiautomator2) and the
web_h5 (Playwright) devices, and the season UI renders at an identical 540x960 on both.
So every coordinate and OCR check here is portable; only *entering* the season differs
per backend (handled by the caller / session, not here).

Coords + live findings: memory ``reference_sea_reward_collection`` (mapped on H5 cocos +
OCR cross-validated, 5554/7fe98fc6 2026-05-29; 目標 milestone claim verified clearing
4 red dots live).

All taps assume the device is already on the SeasonMapScene HUD. Each collector opens its
view, claims, dismisses the reward popup, and closes back to the HUD.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# --- season HUD entry buttons (540x960) ------------------------------------------
BTN_OUTLINE = (52, 770)     # 左下碼頭建築 → 地圖收益 / 碼頭補給 view
BTN_ACHIEVE = (492, 710)    # 成就
BTN_TASK = (492, 632)       # 任務 (內含 目標 tab)

# --- SeasonSceneOutline4View (地圖收益 / 碼頭補給) --------------------------------
# The two tabs do NOT share a claim button (live-verified 2026-05-29):
#   地圖收益: bottom btnStart「領取」(270,712) → 恭喜獲得, storage resets to 0.
#   碼頭補給: upper btnGet「領取」(270,392) → 恭喜獲得.  (270,712 here is 「一鍵補給」, NOT a claim.)
TAB_MAP_INCOME = (210, 812)
TAB_DOCK_SUPPLY = (327, 813)
MAP_INCOME_CLAIM = (270, 712)   # 地圖收益 領取 (btnStart)
DOCK_SUPPLY_CLAIM = (270, 392)  # 碼頭補給 領取 (btnGet)
OUTLINE_CLOSE = (270, 884)

# --- SeasonAchieveView (成就) -----------------------------------------------------
ACHIEVE_GET_X = 415
ACHIEVE_GET_YS = (328, 438, 547, 657, 765)   # claim column, top→bottom; list reflows
ACHIEVE_CLOSE = (275, 821)

# --- SeasonTask4View (任務 / 目標 / 懸賞) -----------------------------------------
# 目標 = 攻堅戰 milestone tracks. Claimable milestones carry a RED dot; the node positions
# SHIFT with progress (live: a row at y≈309 one moment, multi-row another), so they CANNOT
# be hardcoded — they must be found dynamically (see _red_milestone_nodes). Tapping a red
# node SWITCHES the displayed milestone, surfacing its 「領取」at GOAL_CLAIM.
TAB_REN = (154, 851)            # 任務 tab (daily season tasks, each completed row has 領取)
TAB_GOAL = (270, 851)           # 目標 tab
GOAL_CLAIM = (270, 724)         # 「領取」shown after selecting a milestone node
TASK_CLOSE = (270, 918)

# cocos: numbered milestone nodes (name = digits) carrying an active `red*` child, in
# SeasonTask4View. H5 only; ADB red-pixel detection is 待補 (daytime-verify).
_JS_RED_NODES = (
    "()=>{const f=(r,ps)=>{let n=r;for(const p of ps){if(!p)continue;if(!n.children)return null;"
    "const c=n.children.find(k=>(k.name||'')===p);if(!c)return null;n=c;}return n;};"
    "const nv=f(cc.director.getScene(),'/UIRoot/NormalView'.split('/'));"
    "const v=nv&&nv.children.find(c=>c.active&&c.name==='SeasonTask4View');if(!v)return [];"
    "const o=[];const w=(n)=>{if(!n||!n.active)return;if(/^[0-9]+$/.test(n.name||'')){"
    "let red=false;const rw=(x)=>{if(!x||!x.active)return;if(/red$/i.test(x.name||''))red=true;(x.children||[]).forEach(rw);};rw(n);"
    "if(red){const wp=n.worldPosition;o.push([Math.round(wp.x*540/720),Math.round((1280-wp.y)*960/1280)]);}}"
    "(n.children||[]).forEach(w);};w(v);return o;}"
)

# reward popup「點擊空白處關閉」— tap a blank corner, NOT the card centre. The popup is
# not always under /UIRoot/PopView (地圖收益/碼頭補給/成就 use a 恭喜獲得 overlay elsewhere),
# so detect it by OCR text, not by scene-tree layer.
# NB: hints must be popup-SPECIFIC. 「獎勵」alone is a false positive — the 成就 view body
# itself reads 達成獎勵 / 累計獲得…戰功, which would make us "see" a popup that isn't there
# and never terminate the claim loop. 恭喜獲得 + 點擊空白 are unique to the reward overlay.
BLANK_TAP = (510, 150)
_POPUP_HINTS = ("恭喜獲得", "恭喜获得", "點擊空白", "点击空白")
_SETTLE = 1.0


def _texts(d) -> List[str]:
    """OCR the current frame → list of traditional-Chinese strings ([] on failure)."""
    import img_tools  # lazy: keeps importing sea_v2 cheap for unit tests
    try:
        return img_tools.get_all_text(d.screenshot(format="opencv")) or []
    except Exception as exc:  # OCR pipeline hiccup must not crash the daily flow
        logger.warning("[sea-reward] OCR read failed: %s", exc)
        return []


def _any(texts: List[str], *subs: str) -> bool:
    return any(s in t for t in texts for s in subs)


def _ocr_find(d, target: str, exclude: Optional[str] = None):
    """Centre (x, y) of the first OCR box containing ``target`` (繁簡-normalised) and not
    ``exclude``, else None. Used to locate variable-position 「領取」buttons."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import img_tools  # noqa: PLC0415
    import opencc  # noqa: PLC0415

    img = d.screenshot(format="opencv")
    if isinstance(img, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
    res = img_tools.analyze_skill_via_http(img) or {}
    s2t = opencc.OpenCC("s2t")
    for it in res.get("ocr_results", []):
        t = s2t.convert(it.get("text", ""))
        if target in t and (exclude is None or exclude not in t):
            x1, y1, x2, y2 = it["bbox"]
            return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def _tap(d, xy, settle: float = _SETTLE) -> None:
    from utils import pause_guard
    pause_guard.check()
    d.click(xy[0], xy[1])
    time.sleep(settle)


def _close_view(d, close_xy) -> None:
    """Close a reward view back to the HUD, robustly.

    The close tap must land on the close button, not on a lingering reward popup — that was
    the observed bug: a 恭喜獲得 overlay was still up, so the close tap dismissed *it* and the
    view stayed open. So: clear any popup FIRST, then tap close, then clear a popup that the
    close itself may surface. (We don't OCR-verify the HUD afterwards: the season bottom bar
    bleeds through modals, so 港口/補給 are not reliable 'closed' signals.)"""
    _dismiss_reward_popup(d)
    _tap(d, close_xy, settle=0.9)
    _dismiss_reward_popup(d)


def _dismiss_reward_popup(d, tries: int = 4) -> bool:
    """Clear a reward popup that is ALREADY present by tapping a blank corner. Returns True
    if one was seen+cleared. Tapping the card centre does NOT close it (that's content).
    No appearance-wait — for that (after a claim) use :func:`_claim_and_clear`."""
    seen = False
    for _ in range(tries):
        if not _any(_texts(d), *_POPUP_HINTS):
            break
        seen = True
        d.click(*BLANK_TAP)
        time.sleep(0.6)
    return seen


def _claim_and_clear(d, claim_xy, appear_tries: int = 4, settle: float = 0.8) -> bool:
    """Tap a claim button, then POLL for the 恭喜獲得 reward popup to render before clearing.

    The popup renders slowly on a busy/slow device (screenshots 500–700ms), so an immediate
    OCR check after the tap misses it — which both under-counts the claim AND leaves the
    popup up to intercept the next tap (the close button), stranding the view. Polling for
    appearance fixes both. Returns True iff a reward was actually claimed (popup appeared)."""
    _tap(d, claim_xy, settle=settle)
    for _ in range(appear_tries):
        if _any(_texts(d), *_POPUP_HINTS):
            _dismiss_reward_popup(d)
            return True
        time.sleep(0.5)
    return False


def collect_map_income(d) -> bool:
    """地圖收益: open 碼頭建築 → 地圖收益 tab → 領取 accumulated production. Returns True if
    the view opened (claim is best-effort: 領取 no-ops when storage is empty)."""
    _tap(d, BTN_OUTLINE)
    if not _any(_texts(d), "地圖收益", "地图收益"):
        logger.info("[sea-reward] 地圖收益 view did not open")
        return False
    _tap(d, TAB_MAP_INCOME, settle=0.6)
    _claim_and_clear(d, MAP_INCOME_CLAIM)   # waits for + clears the 恭喜獲得 popup
    _close_view(d, OUTLINE_CLOSE)
    logger.info("[sea-reward] 地圖收益 collected")
    return True


def collect_dock_supply(d) -> bool:
    """碼頭補給: open 碼頭建築 → 碼頭補給 tab → 「領取」(``DOCK_SUPPLY_CLAIM`` 270,392, free).

    Live-verified 2026-05-29: this btnGet「領取」produces 恭喜獲得. Note (270,712) on this tab
    is 「一鍵補給」(a separate dispatch, no-op here) — NOT the claim. Returns True if a reward
    popup appeared (something was actually claimed)."""
    _tap(d, BTN_OUTLINE)
    if not _any(_texts(d), "碼頭補給", "码头补给", "地圖收益"):
        logger.info("[sea-reward] 補給 view did not open")
        return False
    _tap(d, TAB_DOCK_SUPPLY, settle=0.6)
    claimed = _claim_and_clear(d, DOCK_SUPPLY_CLAIM)   # 領取 (btnGet, free)
    _close_view(d, OUTLINE_CLOSE)
    logger.info("[sea-reward] 碼頭補給 領取 claimed=%s", claimed)
    return claimed


def _has_unclaimed(texts) -> bool:
    """A claimable 「領取」is on screen (excluding the claimed 「已領取」)."""
    return any(("領取" in t or "领取" in t) and "已" not in t for t in texts)


def claim_achievements(d, max_iters: int = 10) -> int:
    """成就 (= 戰功獎勵: rewards keyed on 累計獲得…戰功): open → claim every ready one.

    GATE EACH ITERATION on detecting a claimable 「領取」(excluding 已領取) FIRST; if none,
    exit immediately WITHOUT tapping. This is the crux: blind-tapping the claim coord on an
    all-claimed list opened an achievement detail that then intercepted the close tap and
    stranded the view. Claims fire at the FIXED TOP coord (list reflows up; OCR click_str
    proved fragile). Returns claims made."""
    top = (ACHIEVE_GET_X, ACHIEVE_GET_YS[0])
    _tap(d, BTN_ACHIEVE)
    if not _any(_texts(d), "成就", "戰功", "累計"):
        logger.info("[sea-reward] 成就/戰功獎勵 view did not open")
        return 0
    claimed = 0
    for _ in range(max_iters):
        if not _has_unclaimed(_texts(d)):   # nothing claimable -> exit (do NOT blind-tap)
            break
        if not _claim_and_clear(d, top):    # tapped a 領取 but no popup -> can't claim -> stop
            break
        claimed += 1
    _close_view(d, ACHIEVE_CLOSE)
    logger.info("[sea-reward] 成就/戰功獎勵 claimed %d", claimed)
    return claimed


def _red_milestone_nodes(d):
    """Screen-pixel coords of milestone nodes that carry a claimable RED dot.

    H5: read from the cocos tree (reliable). ADB: 待補 — returns [] for now (red-pixel
    detection of the milestone track is the daytime-verify follow-up)."""
    page = getattr(d, "_page", None)
    if page is None:
        logger.info("[sea-reward] 目標 red-node detection is H5-only for now (adb 待補)")
        return []
    try:
        return [tuple(n) for n in (page.evaluate(_JS_RED_NODES) or [])]
    except Exception as exc:
        logger.warning("[sea-reward] red-node eval failed: %s", exc)
        return []


def claim_task_panel(d, max_iters: int = 12) -> int:
    """任務 panel (btnTask): claim BOTH tabs — 任務 (daily season tasks) + 目標 (攻堅戰里程碑).

    任務 tab: each COMPLETED task row shows a 「領取」at a variable position (e.g. 421,321),
    so locate it by OCR (excluding 已領取) and claim, looping as the list reflows.
    目標 tab: claimable 攻堅戰 milestones carry a RED dot at a position that SHIFTS with
    progress, so find red nodes dynamically, tap one to SWITCH the displayed milestone, and
    claim its 「領取」(GOAL_CLAIM). ``seen`` skips a red node that yields no 領取 (a locked
    tier still shows red) so it cannot wedge the loop. Returns total claims made.

    Earlier bugs this fixes: (1) only handled 目標, never the 任務-tab daily 領取; (2) 目標
    used hardcoded node coords + break-after-first, so it never switched between nodes."""
    _tap(d, BTN_TASK)
    if not _any(_texts(d), "任務", "任务"):
        logger.info("[sea-reward] 任務 view did not open")
        return 0
    claimed = 0

    # --- 任務 tab: completed daily tasks (OCR-locate 領取, exclude 已領取) ---
    _tap(d, TAB_REN, settle=0.8)
    for _ in range(max_iters):
        pos = _ocr_find(d, "領取", exclude="已")
        if not pos or not _claim_and_clear(d, pos):
            break
        claimed += 1

    # --- 目標 tab: 攻堅戰 milestone red-nodes ---
    _tap(d, TAB_GOAL, settle=0.8)
    seen = set()
    for _ in range(max_iters):
        reds = [n for n in _red_milestone_nodes(d) if n not in seen]
        if not reds:
            break
        node = reds[0]
        seen.add(node)
        _tap(d, node, settle=0.8)               # switch to that milestone
        if _has_unclaimed(_texts(d)) and _claim_and_clear(d, GOAL_CLAIM):
            claimed += 1

    _close_view(d, TASK_CLOSE)
    logger.info("[sea-reward] 任務 panel (任務+目標) claimed %d", claimed)
    return claimed


# back-compat alias (older name referred to the 目標 milestones only)
claim_target_milestones = claim_task_panel


@dataclass
class RewardResult:
    map_income: bool = False
    dock_supply: bool = False
    achievements: int = 0
    target_milestones: int = 0


def collect_all_rewards(d) -> RewardResult:
    """Run every season daily claim from the SeasonMapScene HUD. Each step is independent
    and isolates its own failure so one stuck view can't abort the rest."""
    r = RewardResult()
    for name, fn, setter in (
        ("map_income", collect_map_income, lambda v: setattr(r, "map_income", bool(v))),
        ("dock_supply", collect_dock_supply, lambda v: setattr(r, "dock_supply", bool(v))),
        ("achievements", claim_achievements, lambda v: setattr(r, "achievements", int(v or 0))),
        ("target_milestones", claim_target_milestones, lambda v: setattr(r, "target_milestones", int(v or 0))),
    ):
        try:
            setter(fn(d))
        except Exception as exc:
            from utils import pause_guard
            if isinstance(exc, pause_guard.TaskAborted):
                raise
            logger.warning("[sea-reward] %s failed: %s", name, exc)
    logger.info("[sea-reward] done: %s", r)
    return r

"""web_h5 farm primitives — read the cocos PlantMainView scene and drive it by
PIXEL TAP (not emit('click'), which silently no-ops on editor-wired buttons).

All functions take a Playwright ``page``. The device wrapper exposes it as
``getattr(d, "_page", None)``; ``None`` means the adb backend, in which case the
caller must use its OCR/coordinate fallback instead of these helpers.

Coordinate conversion reuses ``sea_v2.navigator.world_to_pixel`` — UI node
``worldPosition`` is in the 720x1280 design space; frame is 540x960 (scale 0.75).

Ground truth verified live on 5554 (2026-05-28). See
``farm_v2/operations/harvest_card.py`` for the flow that composes these.
"""

from __future__ import annotations

import time
import logging
from typing import Any, Dict, Optional

from sea_v2.navigator import world_to_pixel

logger = logging.getLogger("farm_v2.web_farm")

# Settle time after a tap before the next read/tap.
SETTLE = 0.4

ONEKEY_NAMES = ("btnOneKeyPlant", "btnOneKeyGrow", "btnOneKeyPick", "btnOneKeyFetch")
WORK_PANEL_VIEW = "FarmPlantView"


# ---------------------------------------------------------------------------
# JS snippets (run inside the page)
# ---------------------------------------------------------------------------

_JS_READ_STATE = r"""
() => {
  const sc = cc.director.getScene();
  if (!sc) return {err: "no scene"};
  const pv = (() => {
    const st=[sc]; while(st.length){const n=st.pop(); if(!n)continue;
      if(n.name==="PlantMainView" && n.activeInHierarchy) return n;
      (n.children||[]).forEach(c=>st.push(c));} return null;
  })();
  if (!pv) return {err: "no PlantMainView"};
  const out = {onekey:{}, buff_active:false, buff_num:null,
               premium:null, high:null, gaochan:null, putong:null};
  const all=[]; const st=[pv];
  while(st.length){const n=st.pop(); if(!n)continue; all.push(n); (n.children||[]).forEach(c=>st.push(c));}
  const lblOf = (n) => { const l = n.getComponent ? n.getComponent(cc.Label) : null; return l ? String(l.string) : null; };
  for (const n of all) {
    if (/^btnOneKey/.test(n.name) && n.worldPosition) {
      const w = n.worldPosition;
      out.onekey[n.name] = {active: n.activeInHierarchy, wx: w.x, wy: w.y};
    }
    if (n.name === "SpecialBuff") out.buff_active = n.activeInHierarchy;
    const par = n.parent ? n.parent.name : "";
    if (n.name === "num"      && par === "SpecialBuff")       out.buff_num = lblOf(n);
    if (n.name === "txtCoin"  && par === "btnSeedRare")       out.premium  = lblOf(n);
    if (n.name === "txtCoin"  && par === "btnSeedBuy")        out.high     = lblOf(n);
    if (n.name === "txtCoin"  && par === "btnFertilizerBuy")  out.gaochan  = lblOf(n);
    if (n.name === "txtCoin"  && par === "btnFertilizerGet")  out.putong   = lblOf(n);
  }
  return out;
}
"""

# Find a selectable item inside a dialog by its Label text, climb to the
# clickable ancestor (btnSeed / btnFertilize…), return its worldPosition.
_JS_LABEL_ITEM = r"""
(args) => {
  const [viewName, labelText, climbPrefix] = args;
  const sc = cc.director.getScene();
  const view = (() => {
    const st=[sc]; while(st.length){const n=st.pop(); if(!n)continue;
      if(n.name===viewName && n.activeInHierarchy) return n;
      (n.children||[]).forEach(c=>st.push(c));} return null;
  })();
  if (!view) return {err: "no " + viewName};
  let target = null;
  const st=[view];
  while(st.length){const n=st.pop(); if(!n)continue;
    if(n.name==="txtName"){
      const l = n.getComponent(cc.Label);
      if(l && String(l.string).trim()===labelText){
        let p=n; while(p && !(p.name||"").startsWith(climbPrefix)) p=p.parent;
        if(p && p.worldPosition){const w=p.worldPosition; target={wx:w.x, wy:w.y};}
      }
    }
    (n.children||[]).forEach(c=>st.push(c));
  }
  return target ? target : {err: labelText + " not found in " + viewName};
}
"""

# Return worldPosition of a named button inside an active view.
_JS_VIEW_BTN = r"""
(args) => {
  const [viewName, btnName] = args;
  const sc = cc.director.getScene();
  const view = (() => {
    const st=[sc]; while(st.length){const n=st.pop(); if(!n)continue;
      if(n.name===viewName && n.activeInHierarchy) return n;
      (n.children||[]).forEach(c=>st.push(c));} return null;
  })();
  if (!view) return {err: "no " + viewName};
  let target=null;
  const st=[view];
  while(st.length){const n=st.pop(); if(!n)continue;
    if(n.name===btnName && n.worldPosition){const w=n.worldPosition; target={wx:w.x, wy:w.y}; break;}
    (n.children||[]).forEach(c=>st.push(c));
  }
  return target ? target : {err: btnName + " not found in " + viewName};
}
"""

_JS_VIEW_ACTIVE = r"""
(viewName) => {
  const sc = cc.director.getScene();
  const st=[sc]; while(st.length){const n=st.pop(); if(!n)continue;
    if(n.name===viewName && n.activeInHierarchy) return true;
    (n.children||[]).forEach(c=>st.push(c));}
  return false;
}
"""

_JS_WORK_ACTION = r"""
(action) => {
  const sc = cc.director.getScene();
  let view = null;
  const st = [sc];
  while (st.length) {
    const n = st.pop(); if (!n) continue;
    if (n.name === "FarmPlantView" && n.activeInHierarchy) { view = n; break; }
    (n.children || []).forEach(c => st.push(c));
  }
  if (!view) return {status: "closed"};
  let status = null, target = null;
  const q = [view];
  while (q.length) {
    const n = q.pop(); if (!n || !n.activeInHierarchy) continue;
    const label = n.getComponent ? n.getComponent(cc.Label) : null;
    const text = label ? String(label.string || "").trim() : "";
    if (text === "取消打工" || text === "取消工作") {
      status = "running";
      if (!target && action === "cancel" && n.worldPosition) target = n;
    } else if (text === "開始打工" || text === "开始打工" || text === "開始工作") {
      status = "stopped";
      if (!target && action === "start" && n.worldPosition) target = n;
    }
    (n.children || []).forEach(c => q.push(c));
  }
  if (target && action !== "read") {
    let btn = target;
    for (let i = 0; i < 8 && btn; i++, btn = btn.parent) {
      const button = btn.getComponent ? btn.getComponent(cc.Button) : null;
      const hasClick = btn.hasEventListener ? btn.hasEventListener("click") : false;
      if (btn.activeInHierarchy && (button || hasClick || String(btn.name || "").startsWith("btn"))) {
        btn.emit("click", btn);
        return {status, clicked: true, name: btn.name};
      }
    }
  }
  return {status: status || "unknown", clicked: false};
}
""";


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def read_farm_state(page: Any) -> Dict[str, Any]:
    """One scene read of PlantMainView. Returns dict with onekey button
    {active, wx, wy}, buff_active (SpecialBuff node activeInHierarchy),
    buff_num (stale string, logging only), and seed/fertilizer count strings."""
    try:
        return page.evaluate(_JS_READ_STATE) or {"err": "empty"}
    except Exception as e:  # pragma: no cover - page errors
        logger.warning("read_farm_state failed: %s", e)
        return {"err": str(e)}


def buff_active(page: Any) -> bool:
    """True iff the SpecialBuff node is active (the only reliable buff signal —
    SpecialBuff/num string goes stale after the buff ends)."""
    return bool(read_farm_state(page).get("buff_active"))


def onekey_active(page: Any, name: str) -> bool:
    btn = read_farm_state(page).get("onekey", {}).get(name)
    return bool(btn and btn.get("active"))


def seed_dialog_open(page: Any) -> bool:
    return _view_active(page, "SeedSelectView")


def fert_dialog_open(page: Any) -> bool:
    return _view_active(page, "FertilizeSelectView")


def work_panel_open(page: Any) -> bool:
    """種植小隊（打工）視窗目前是否仍覆蓋在農場上。"""
    return _view_active(page, WORK_PANEL_VIEW)


def _view_active(page: Any, view_name: str) -> bool:
    try:
        return bool(page.evaluate(_JS_VIEW_ACTIVE, view_name))
    except Exception as e:  # pragma: no cover
        logger.warning("_view_active(%s) failed: %s", view_name, e)
        return False


def work_status(page: Any, action: str = "read") -> str:
    """讀取或操作 H5 打工面板；回傳 running/stopped/unknown/closed。"""
    try:
        result = page.evaluate(_JS_WORK_ACTION, action) or {}
        return str(result.get("status") or "unknown")
    except Exception as e:  # pragma: no cover
        logger.warning("work_status(%s) failed: %s", action, e)
        return "unknown"


def click_work_action(page: Any, action: str) -> bool:
    """用 Cocos 節點 emit 點擊取消/開始打工。"""
    try:
        result = page.evaluate(_JS_WORK_ACTION, action) or {}
        return bool(result.get("clicked"))
    except Exception as e:  # pragma: no cover
        logger.warning("click_work_action(%s) failed: %s", action, e)
        return False


# ---------------------------------------------------------------------------
# Taps (pixel)
# ---------------------------------------------------------------------------

def _click_world(page: Any, wx: float, wy: float) -> None:
    px, py = world_to_pixel(wx, wy)
    page.mouse.click(px, py)
    time.sleep(SETTLE)


def tap_onekey(page: Any, name: str, state: Optional[Dict[str, Any]] = None) -> bool:
    """Pixel-tap an OneKey button if it is active. The four buttons reposition by
    how many are active, so we read worldPosition fresh each time."""
    st = state if state is not None else read_farm_state(page)
    btn = st.get("onekey", {}).get(name)
    if not btn or not btn.get("active"):
        return False
    _click_world(page, btn["wx"], btn["wy"])
    return True


def _select_item_by_label(page: Any, view_name: str, label_text: str, climb_prefix: str) -> bool:
    try:
        r = page.evaluate(_JS_LABEL_ITEM, [view_name, label_text, climb_prefix])
    except Exception as e:  # pragma: no cover
        logger.warning("_select_item_by_label failed: %s", e)
        return False
    if not r or r.get("err"):
        logger.warning("select '%s' in %s: %s", label_text, view_name, (r or {}).get("err"))
        return False
    _click_world(page, r["wx"], r["wy"])
    return True


def _tap_view_btn(page: Any, view_name: str, btn_name: str) -> bool:
    try:
        r = page.evaluate(_JS_VIEW_BTN, [view_name, btn_name])
    except Exception as e:  # pragma: no cover
        logger.warning("_tap_view_btn failed: %s", e)
        return False
    if not r or r.get("err"):
        logger.warning("tap %s/%s: %s", view_name, btn_name, (r or {}).get("err"))
        return False
    _click_world(page, r["wx"], r["wy"])
    return True


def select_seed_by_name(page: Any, name: str = "特級種子") -> bool:
    """Select a seed in SeedSelectView by its Label text (NOT child index — the
    ScrollView order is not stable, index picks the wrong tier)."""
    return _select_item_by_label(page, "SeedSelectView", name, "btnSeed")


def tap_seed_confirm(page: Any) -> bool:
    return _tap_view_btn(page, "SeedSelectView", "btnUse")


def select_fertilizer_by_name(page: Any, name: str) -> bool:
    """Select a fertilizer in FertilizeSelectView by Label text
    ('普通肥料'=btnFertilizeGet / '高產肥料'=btnFertilizeBuy)."""
    return _select_item_by_label(page, "FertilizeSelectView", name, "btnFertilize")


def tap_fert_confirm(page: Any) -> bool:
    return _tap_view_btn(page, "FertilizeSelectView", "btnUse")


# ---------------------------------------------------------------------------
# Watch-ad seed top-up (初級種子). Verified live on 7fe98fc6 (2026-05-31):
# SeedSelectView row 0 = 初級種子; when exhausted its btnSeed hides and
# btnSeedAd shows "種子×3 (N/2)" (N = watches left today). Tapping it grants
# seeds instantly with the no-ad card (no video) and opens GoodsGetView
# (恭喜獲得) — the reliable "reward granted" signal. Gotcha: btnSeedAd stays
# active even at (0/2), so the (N) count (or GoodsGetView appearing) is the
# real stop condition, NOT activeInHierarchy alone.
# ---------------------------------------------------------------------------

_JS_SEED_AD_STATUS = r"""
() => {
  const sc = cc.director.getScene();
  function findActive(name){ const st=[sc]; while(st.length){const n=st.pop(); if(!n)continue;
    if(n.name===name && n.activeInHierarchy) return n; (n.children||[]).forEach(c=>st.push(c));} return null; }
  const ss = findActive("SeedSelectView");
  if (!ss) return {open:false};
  function byPath(root, path){ let c=root; for(const s of path){ if(!c)return null;
    c=(c.children||[]).find(k=>k.name===s)||null;} return c; }
  const row0 = byPath(ss, ["view","ScrollView","view","content","0"]);
  let ad = null;
  if (row0) {
    const adNode = (row0.children||[]).find(k=>k.name==="btnSeedAd");
    if (adNode) {
      const labs=[]; const st=[adNode];
      while(st.length){const n=st.pop(); if(!n)continue;
        const l=n.getComponent?n.getComponent(cc.Label):null; if(l&&l.string)labs.push(String(l.string));
        (n.children||[]).forEach(c=>st.push(c));}
      let remaining=null;
      for(const s of labs){ const m=String(s).match(/\((\d+)\s*\/\s*\d+\)/); if(m){remaining=parseInt(m[1],10); break;} }
      const w=adNode.worldPosition;
      ad={active:adNode.activeInHierarchy, remaining:remaining, wx:w?w.x:null, wy:w?w.y:null};
    }
  }
  const um=window.uiMgr;
  const rewardOpen = !!(um && um.getView && um.getView("GoodsGetView"));
  return {open:true, ad:ad, rewardOpen:rewardOpen};
}
"""

_JS_UIMGR_CLOSE = r"""
(viewName) => { const um=window.uiMgr;
  if (um && um.close) { try { um.close(viewName); return true; } catch(e){ return false; } }
  return false; }
"""

_JS_UIMGR_HAS = r"""
(viewName) => { const um=window.uiMgr;
  return !!(um && um.getView && um.getView(viewName)); }
"""


def open_seed_select(page: Any) -> bool:
    """Open SeedSelectView (種植選擇) by tapping 一鍵種植; verify it opened."""
    if seed_dialog_open(page):
        return True
    tap_onekey(page, "btnOneKeyPlant")
    time.sleep(0.8)
    return seed_dialog_open(page)


def seed_ad_status(page: Any) -> Dict[str, Any]:
    """Row-0 (初級種子) watch-ad button state:
    {open, ad:{active, remaining, wx, wy}, rewardOpen}. remaining = N from the
    "(N/2)" label (watches left today); None if it couldn't be parsed."""
    try:
        return page.evaluate(_JS_SEED_AD_STATUS) or {"open": False}
    except Exception as e:  # pragma: no cover
        logger.warning("seed_ad_status failed: %s", e)
        return {"open": False}


def tap_seed_ad(page: Any, ad: Dict[str, Any]) -> bool:
    """Pixel-tap the row-0 btnSeedAd at its current worldPosition."""
    if not ad or ad.get("wx") is None:
        return False
    _click_world(page, ad["wx"], ad["wy"])
    return True


def reward_open(page: Any) -> bool:
    """True iff GoodsGetView (恭喜獲得) is open — the reward-granted signal."""
    try:
        return bool(page.evaluate(_JS_UIMGR_HAS, "GoodsGetView"))
    except Exception as e:  # pragma: no cover
        logger.warning("reward_open failed: %s", e)
        return False


def close_reward(page: Any) -> bool:
    """Close the 恭喜獲得 reward popup via uiMgr (verified)."""
    return _uimgr_close(page, "GoodsGetView")


def close_seed_select(page: Any) -> bool:
    """Close the 種植選擇 dialog via uiMgr (verified)."""
    return _uimgr_close(page, "SeedSelectView")


def close_work_panel(
    page: Any,
    retries: int = 3,
    observe_for: float = 0.0,
) -> bool:
    """關閉種植小隊視窗，並確認它真的已從 active scene tree 消失。

    ``FarmPlantView`` 的官方 ``btnClose`` 會呼叫 view.close()。優先透過
    uiMgr 關閉，若遊戲當下的 view manager 沒反應，再點官方關閉節點。
    """
    observe_until = time.monotonic() + max(0.0, observe_for)

    while True:
        if not work_panel_open(page):
            remaining = observe_until - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.2, remaining))
            continue

        for _ in range(max(1, retries)):
            _uimgr_close(page, WORK_PANEL_VIEW)
            time.sleep(SETTLE)
            if not work_panel_open(page):
                break

            _tap_view_btn(page, WORK_PANEL_VIEW, "btnClose")
            time.sleep(SETTLE)
            if not work_panel_open(page):
                break
        else:
            logger.warning("種植小隊視窗關閉失敗: %s 仍為 active", WORK_PANEL_VIEW)
            return False

        # 離場防護會繼續觀察一段時間，攔住點擊打工後延遲載入的 view。
        if time.monotonic() >= observe_until:
            return True


def _uimgr_close(page: Any, view_name: str) -> bool:
    try:
        return bool(page.evaluate(_JS_UIMGR_CLOSE, view_name))
    except Exception as e:  # pragma: no cover
        logger.warning("_uimgr_close(%s) failed: %s", view_name, e)
        return False

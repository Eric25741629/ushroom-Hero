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


def _view_active(page: Any, view_name: str) -> bool:
    try:
        return bool(page.evaluate(_JS_VIEW_ACTIVE, view_name))
    except Exception as e:  # pragma: no cover
        logger.warning("_view_active(%s) failed: %s", view_name, e)
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

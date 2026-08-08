"""Live driver for 賞金之路 (Escort) — auto-fight the map's monster NPCs on a
running Cocos H5 client via ``emit('click')``.

賞金之路 = the biweekly weekend Escort event (alternates with 跨服戰). Its NPC
challenges run a client-side 護送戰鬥 (deterministic battleMain + anti-cheat).
本檔保留 H5 UI fallback；純 WS 主流程由 ``ws_token.escort`` /
``ws_token.escort_fight`` 負責，A 端直接走 Escort API，B 端載入官方
BattleMainServer 計算。這裡的 navigation/combat 仍全部走 cocos node clicks。

LIVE-verified end-to-end (emulator-5556 / CDP 9223, 2026-07-04; see memory
``reference_escort_bounty_road`` and docs/protocol notes):
  ENTRY   home ``btnEscort`` -> ``WeekActivitySelectView`` (週末玩法) ->
          click the 賞金之路 item (date=="當前開啟") -> ``EscortView`` ->
          ``escortBtn`` -> ``EscortTransportSceneView`` (map).
  OPEN    the selector item's ``date`` label == "當前開啟" (server-authoritative,
          biweekly-aware; "下一輪開啟" == this is a 跨服戰 weekend, skip).
  FIGHT   map ``.../content/player`` children named ``monster`` are the NPCs
          (虛偽騎士 / 巫師娃娃 / 海灘奸商). Click one -> ``EscortMonsterFightView``
          -> ``btnStart`` (挑戰) -> battle (~15-25s) -> ``EscortResultView``
          (``nodeWin`` / ``nodeDefeat``) -> click ``imgMask`` to close. A beaten
          NPC becomes ``active:false``. NPC fights cost NO 挑戰次數.
  EXIT    does NOT auto-return home; close 3 stacked overlays in order:
          map ``topNode/btnClose`` -> ``EscortView/btnClose`` ->
          ``WeekActivitySelectView/root/bg/btnClose``.

Bounded by design: ``fight_npc_round`` dedupes by node index and caps at
``max_fights`` so a run never grinds forever (a lost NPC stays active but is
attempted at most once per round -> skipped, per user spec).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NV = "UIRoot/NormalView"
BTN_ESCORT = f"{NV}/MainView/top/systemTop/btnRoot/btnEscort"
SELECTOR = f"{NV}/WeekActivitySelectView"
SELECTOR_CONTENT = f"{SELECTOR}/root/ScrollView/view/content"
SELECTOR_CLOSE = f"{SELECTOR}/root/bg/btnClose"
ESCORT_VIEW = f"{NV}/EscortView"
ESCORT_BTN = f"{ESCORT_VIEW}/container/EscortMainView/EscortMainView/nodeBattle/escortBtn"
ESCORT_VIEW_CLOSE = f"{ESCORT_VIEW}/btnClose"
MAP = f"{NV}/EscortTransportSceneView"
MAP_CLOSE = f"{MAP}/topNode/btnClose"
MAP_PLAYER = f"{MAP}/ScrollView/view/content/player"
FIGHT_VIEW = f"{NV}/EscortMonsterFightView"
FIGHT_START = f"{FIGHT_VIEW}/btnStart"
RESULT_VIEW = f"{NV}/EscortResultView"
RESULT_CLOSE = f"{RESULT_VIEW}/imgMask"

ESCORT_LABEL = "賞金之路"
OPEN_LABEL = "當前開啟"


# ── JS primitives ──────────────────────────────────────────────────────

_CLICK_JS = r"""
([path]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked:false, err:'no_cc'};
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p);
      if (!n) return null;
    }
    return n;
  };
  const node = find(cc.director.getScene(), path.split('/').filter(Boolean));
  if (!node) return {clicked:false, err:'not_found', path};
  try { node.emit('click', node); return {clicked:true, active: !!node.active}; }
  catch(e) { return {clicked:false, err:String(e)}; }
}
"""

_VIEW_ACTIVE_JS = r"""
([name]) => {
  const nv = cc.director.getScene().children
    .find(c => c.name === 'UIRoot')?.children.find(c => c.name === 'NormalView');
  if (!nv) return false;
  const v = nv.children.find(c => (c.name || '') === name);
  return !!(v && v.active);
}
"""

# The label of the first non-empty Label component in a node's subtree.
_LABEL_HELPER = r"""
  const labelOf = (node) => {
    if (node && node._components) for (const c of node._components) {
      const s = c && (c.string || c._string);
      if (typeof s === 'string' && s.trim()) return s;
    }
    return null;
  };
"""

# Read the weekly selector items: [{idx, name, date}]. name = the '賞金之路'/
# '跨服戰' label; date = '當前開啟' / '下一輪開啟'.
_READ_SELECTOR_JS = r"""
() => {
  %s
  const find = (root, parts) => { let n=root; for (const p of parts){ if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p);} return n; };
  const content = find(cc.director.getScene(), "%s".split('/').filter(Boolean));
  if (!content) return {err:'no_selector'};
  const items = [];
  content.children.forEach((c, idx) => {
    const nameNode = c.children && c.children.find(x => x.name === 'name');
    const dateNode = c.children && c.children.find(x => x.name === 'date');
    items.push({idx, name: labelOf(nameNode), date: labelOf(dateNode)});
  });
  return {items};
}
""" % (_LABEL_HELPER, SELECTOR_CONTENT)

# List monster NPC children of the map's player node: [{idx, active, name}].
_LIST_MONSTERS_JS = r"""
() => {
  %s
  const find = (root, parts) => { let n=root; for (const p of parts){ if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p);} return n; };
  const player = find(cc.director.getScene(), "%s".split('/').filter(Boolean));
  if (!player) return {err:'no_map'};
  const out = [];
  player.children.forEach((c, idx) => {
    if (c.name !== 'monster') return;
    const nb = c.children.find(x => x.name === 'nameBg');
    const nn = nb && nb.children.find(x => x.name === 'name');
    out.push({idx, active: !!c.active, name: labelOf(nn)});
  });
  return {mons: out};
}
""" % (_LABEL_HELPER, MAP_PLAYER)

# Click the map monster node at player.children[idx] (only if active monster).
_CLICK_MONSTER_JS = r"""
([idx]) => {
  const find = (root, parts) => { let n=root; for (const p of parts){ if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p);} return n; };
  const player = find(cc.director.getScene(), "%s".split('/').filter(Boolean));
  if (!player || !player.children[idx]) return {err:'no_node'};
  const n = player.children[idx];
  if (n.name !== 'monster' || !n.active) return {err:'not_active_monster'};
  try { n.emit('click', n); return {ok:true}; } catch(e) { return {err:String(e)}; }
}
""" % MAP_PLAYER

# Read the battle result: {win, lose}.
_READ_RESULT_JS = r"""
() => {
  const nv = cc.director.getScene().children
    .find(c => c.name === 'UIRoot')?.children.find(c => c.name === 'NormalView');
  const rv = nv && nv.children.find(c => c.name === 'EscortResultView');
  if (!rv) return {err:'no_result'};
  const win = rv.children.find(c => c.name === 'nodeWin');
  const lose = rv.children.find(c => c.name === 'nodeDefeat');
  return {win: !!(win && win.active), lose: !!(lose && lose.active)};
}
"""


# ── python helpers ─────────────────────────────────────────────────────

def _click(page, path: str) -> Dict[str, Any]:
    return page.evaluate(_CLICK_JS, [path]) or {}


def _view_active(page, name: str) -> bool:
    return bool(page.evaluate(_VIEW_ACTIVE_JS, [name]))


def _wait_view(page, name: str, timeout: float, want: bool = True,
               poll: float = 0.4) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _view_active(page, name) is want:
            return True
        time.sleep(poll)
    return False


# ── public API ─────────────────────────────────────────────────────────

def enter_escort(page, settle: float = 2.2) -> bool:
    """From the main page, navigate into the 賞金之路 NPC map. Returns True once
    ``EscortTransportSceneView`` is open, False if the event is closed / this is
    a 跨服戰 weekend / any hop failed. Leaves overlays open on failure — the
    caller should ``close_to_home`` regardless."""
    _click(page, BTN_ESCORT)
    if not _wait_view(page, "WeekActivitySelectView", 4.0):
        logger.warning("[escort] 選擇器未開啟（活動入口不存在?），跳過")
        return False
    data = page.evaluate(_READ_SELECTOR_JS) or {}
    target = next((it for it in data.get("items", [])
                   if it.get("name") == ESCORT_LABEL), None)
    if target is None:
        logger.info("[escort] 週末玩法無賞金之路項，跳過")
        return False
    if target.get("date") != OPEN_LABEL:
        logger.info("[escort] 賞金之路非當前開啟（date=%s），跳過", target.get("date"))
        return False
    _click(page, f"{SELECTOR_CONTENT}/{target['idx']}")
    if not _wait_view(page, "EscortView", 4.0):
        logger.warning("[escort] EscortView 未開啟")
        return False
    time.sleep(settle)
    _click(page, ESCORT_BTN)
    if not _wait_view(page, "EscortTransportSceneView", 5.0):
        logger.warning("[escort] 押運地圖未開啟")
        return False
    time.sleep(settle)
    return True


def fight_npc_round(page, max_fights: int = 8, battle_timeout: float = 45.0,
                    settle: float = 0.5) -> Dict[str, Any]:
    """Fight every currently-active monster NPC on the map once. A win greys the
    NPC out (active:false); a loss leaves it active but it is skipped (attempted
    at most once). Terminates when no active un-attempted monster remains or the
    ``max_fights`` cap is hit. Must already be on the map (call ``enter_escort``).

    Returns {"fought": [{"name","outcome"}...], "count", "win", "lose"}.
    """
    summary: Dict[str, Any] = {"fought": [], "count": 0, "win": 0, "lose": 0}
    attempted: set = set()

    listing = page.evaluate(_LIST_MONSTERS_JS) or {}
    if listing.get("err"):
        logger.warning("[escort] 不在押運地圖上（%s），跳過", listing["err"])
        return summary
    initial_active = [m for m in listing.get("mons", []) if m.get("active")]
    cap = min(max_fights, len(initial_active)) if initial_active else 0
    logger.info("[escort] 地圖 active NPC: %s（cap=%d）",
                [m.get("name") for m in initial_active], cap)

    for _ in range(cap + 2):
        mons = (page.evaluate(_LIST_MONSTERS_JS) or {}).get("mons", [])
        tgt = next((m for m in mons
                    if m.get("active") and m["idx"] not in attempted), None)
        if tgt is None:
            break
        attempted.add(tgt["idx"])
        if len(summary["fought"]) >= max_fights:
            logger.info("[escort] 達 max_fights 上限，停止")
            break

        r = page.evaluate(_CLICK_MONSTER_JS, [tgt["idx"]]) or {}
        if r.get("err"):
            logger.info("[escort] 點 NPC idx=%s 失敗（%s），跳過", tgt["idx"], r["err"])
            continue
        if not _wait_view(page, "EscortMonsterFightView", 4.0):
            logger.info("[escort] 挑戰面板未開，跳過 %s", tgt.get("name"))
            continue
        _click(page, FIGHT_START)
        if not _wait_view(page, "EscortResultView", battle_timeout):
            logger.warning("[escort] %s 戰鬥結果逾時", tgt.get("name"))
        res = page.evaluate(_READ_RESULT_JS) or {}
        outcome = "win" if res.get("win") else ("lose" if res.get("lose") else "unknown")
        summary["fought"].append({"name": tgt.get("name"), "outcome": outcome})
        if outcome == "win":
            summary["win"] += 1
        elif outcome == "lose":
            summary["lose"] += 1
        logger.info("[escort] %s -> %s", tgt.get("name"), outcome)

        _click(page, RESULT_CLOSE)
        _wait_view(page, "EscortResultView", 5.0, want=False)
        _wait_view(page, "EscortTransportSceneView", 6.0, want=True)
        time.sleep(settle)

    summary["count"] = len(summary["fought"])
    return summary


def close_to_home(page) -> bool:
    """Close the 3 stacked escort overlays back to the main page. Best-effort:
    clicks each known close in order, then reports whether only MainView remains.
    """
    for path, view in (
        (MAP_CLOSE, "EscortTransportSceneView"),
        (ESCORT_VIEW_CLOSE, "EscortView"),
        (SELECTOR_CLOSE, "WeekActivitySelectView"),
    ):
        if _view_active(page, view):
            _click(page, path)
            _wait_view(page, view, 3.0, want=False)
    on_home = not any(
        _view_active(page, v) for v in
        ("EscortTransportSceneView", "EscortView", "WeekActivitySelectView")
    )
    if not on_home:
        logger.warning("[escort] 收尾未完全回主頁（仍有活動 overlay 開著）")
    return on_home

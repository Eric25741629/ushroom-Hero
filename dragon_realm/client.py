"""龍骸聖域 H5 RPC 橋接。

透過 IS() wrapper 取得 ActivityLhsyDataCache singleton，直接讀 .info。
info_s2c 只在入場時發一次，listener 模式不可靠。
"""
from __future__ import annotations

import logging

from dragon_realm import constants as C
from dragon_realm.planner import Action

logger = logging.getLogger(__name__)

# ponytail: IS() wrapper 抓 singleton — info_s2c 只在入場發一次，listener 收不到
_INSTALL_JS = r"""
() => {
  const nm = window.netManager;
  if (!nm || typeof IS !== 'function') return false;
  if (window.__drCache) return true;
  const origIS = window.IS;
  window.IS = function(cls) {
    const r = origIS(cls);
    if (!window.__drCache && r && r.info && typeof r.info.update === 'function' && r.teamInfo) {
      window.__drCache = r;
    }
    return r;
  };
  const table = (nm._events && nm._events._callbackTable) || {};
  const entry = table['dragon_realm.dragon_realm_info_s2c'];
  const infos = (entry && entry.callbackInfos) || [];
  const ctrl = infos[0] && infos[0].target;
  if (ctrl) {
    const proto = Object.getPrototypeOf(ctrl);
    try { proto.on_dragon_realm_unlock_auto_explore_s2c.call(ctrl, {is_open: 0, is_sys: 1, action: []}); } catch(_) {}
  }
  window.IS = origIS;
  return !!window.__drCache;
}
"""

_READ_JS = r"""
() => {
  const cache = window.__drCache;
  if (!cache || !cache.info) return {ts: 0, raw: null};
  const info = cache.info;
  const pickEventData = (arr) => {
    const o = {}; (arr || []).forEach(it => { o[it.k] = it.v; }); return o;
  };
  const ed = pickEventData(info.event_data);
  // ponytail: event_id 是 config row ID 非 type constant，從 data keys 推導
  let et = 0;
  if (info.event_id) {
    if (ed[1] || ed[6]) et = 1;       // K_PVE_HP or K_MAX_HP -> monster (PVE)
    else if (ed[2] !== undefined) et = 4;  // K_TRAP_TIME -> trap
    else et = 5;                       // fallback: buff/cave/box -> advance
  }
  const raw = {
    activity_open: !!(info.ceng || info.hp != null),
    ceng: info.ceng || 1,
    hp: info.hp != null ? info.hp : 0,
    server_time: info.server_time || 0,
    help_hp: info.help_hp || 0,
    event_id: info.event_id || 0,
    event_type: et || 0,
    event_uid: info.event_uid || 0,
    event_data: ed,
    event_list: (cache.eventList || []).map(e => ({
      role_id: e.role_id, event_id: e.event_id, id: e.id || e.uid,
      event_type: e.event_type, back_kill_time: e.back_kill_time || 0,
    })),
    help_events: (cache.eventList || []).filter(e => e.event_id).map(e => e.event_id),
    bag: {},
  };
  return {ts: Date.now(), raw: raw};
}
"""

# 送具名 c2s。args 為 [msgName, payloadObj]。
_SEND_JS = r"""
(args) => {
  const nm = window.netManager;
  if (!nm) return false;
  nm.send(args[0], args[1] || {});
  return true;
}
"""

_PREFIX = "dragon_realm."


class DragonClient:
    """Playwright page 上的龍骸聖域 RPC 介面。"""

    def __init__(self, page, my_role_id: int):
        self._page = page
        self.my_role_id = my_role_id

    def install(self) -> bool:
        return bool(self._page.evaluate(_INSTALL_JS))

    def read_raw(self) -> dict:
        """回傳 {ts, raw}；raw 可直接餵 DragonState.from_raw。"""
        return self._page.evaluate(_READ_JS)

    def _send(self, msg: str, payload: dict) -> None:
        self._page.evaluate(_SEND_JS, [_PREFIX + msg, payload])

    def dispatch(self, action: Action) -> None:
        if action.kind == C.A_EXPLORE:
            self._send("dragon_realm_start_explore_c2s", {})
        elif action.kind == C.A_CHOICE:
            self._send("dragon_realm_event_choice_c2s",
                       {"choice": action.choice, "event_uid": action.uid})
        elif action.kind == C.A_ENTER_CENG:
            self._send("dragon_realm_enter_ceng_c2s", {"ceng": action.ceng})
        elif action.kind == C.A_PROVIDE_HELP:
            self._send("dragon_realm_provide_help_c2s",
                       {"help_target": action.role_id, "event_id": action.event_id})
        elif action.kind == C.A_RECEIVE_HELP:
            self._send("dragon_realm_receive_help_event_c2s", {"event_id": action.event_id})
            self._send("dragon_realm_help_event_list_c2s", {})
        # A_WAIT / A_STOP: no RPC

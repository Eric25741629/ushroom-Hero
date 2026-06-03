"""龍骸聖域 H5 RPC 橋接。

send 具名 c2s + 讀 window.__dr_state（一次性 listener 暫存最新 s2c）。
欄位攤平對照 docs/protocol/DRAGON_REALM_SCHEMA.md（Task 1 產出）。
"""
from __future__ import annotations

import logging

from dragon_realm import constants as C
from dragon_realm.planner import Action

logger = logging.getLogger(__name__)

# 一次性安裝 listener；把最新 info/help 存到 window.__dr_state，附 client 端 ts。
_INSTALL_JS = r"""
() => {
  const nm = window.netManager;
  if (!nm) return false;
  if (window.__dr_installed) return true;
  window.__dr_state = {};
  const cap = (k) => (e) => { try { window.__dr_state[k] = {ts: Date.now(), data: JSON.parse(JSON.stringify(e))}; } catch(_) {} };
  nm.addEventListener("dragon_realm.dragon_realm_info_s2c", cap("info"), window);
  nm.addEventListener("dragon_realm.dragon_realm_event_update_s2c", cap("info"), window);
  nm.addEventListener("dragon_realm.dragon_realm_help_event_list_s2c", cap("help"), window);
  window.__dr_installed = true;
  nm.send("dragon_realm.dragon_realm_info_c2s", {});
  return true;
}
"""

# 把 window.__dr_state 攤成 state.from_raw 的中間 dict。
# NB: 下列鍵路徑（info.ceng / cur.event_id / data 陣列 / eventList / 背包）依
#     DRAGON_REALM_SCHEMA.md 確認後填寫；此處為對照 schema 的實作位置。
_READ_JS = r"""
() => {
  const st = window.__dr_state || {};
  const info = (st.info && st.info.data) || null;
  const helpList = (st.help && st.help.data) || null;
  const ts = (st.info && st.info.ts) || 0;
  if (!info) return {ts: 0, raw: null};
  // pickEventData: 當前事件 data 陣列 [{k,v}] -> {k:v}
  const pickEventData = (arr) => {
    const o = {}; (arr || []).forEach(it => { o[it.k] = it.v; }); return o;
  };
  const cur = info.cur_event || info.current || {};
  const raw = {
    activity_open: true,
    ceng: info.ceng || 1,
    hp: info.hp != null ? info.hp : (info.stamina || 0),
    server_time: info.server_time || 0,
    help_hp: info.help_hp || 0,
    event_id: cur.event_id || 0,
    event_type: cur.event_type || 0,
    event_uid: cur.event_uid || cur.id || 0,
    event_data: pickEventData(cur.data),
    event_list: (info.event_list || []).map(e => ({
      role_id: e.role_id, event_id: e.event_id, id: e.id,
      event_type: e.event_type, back_kill_time: e.back_kill_time || 0,
    })),
    help_events: (helpList && helpList.list ? helpList.list : []).map(x => x.event_id || x.id),
    bag: window.__dr_bag || {},
  };
  return {ts: ts, raw: raw};
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

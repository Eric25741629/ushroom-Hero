# -*- coding: utf-8 -*-
"""A 頁：攔截官方 result send、擷取 combat payload、代送 result。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

INSTALL_JS = r"""
() => {
  if (window.__bc_hooks) return 'already';
  const nm = window.netManager;
  if (!nm || typeof nm.send !== 'function') return 'no_netManager';
  window.__bc = {
    block: false,
    combat_arena: null,
    combat_rogue: null,
    blocked: 0,
    last_blocked: null,
  };
  const orig = nm.send.bind(nm);
  nm.__bc_orig_send = orig;
  nm.send = function (cmd, body) {
    try {
      if (window.__bc && window.__bc.block &&
          (cmd === 'arena.arena_result_c2s' || cmd === 'rogue.rogue_main_result_c2s')) {
        window.__bc.blocked += 1;
        window.__bc.last_blocked = { cmd: cmd, body: body, ts: Date.now() };
        return;
      }
    } catch (e) {}
    return orig(cmd, body);
  };
  nm.addEventListener('arena.arena_combat_s2c', function (msg) {
    try {
      window.__bc.combat_arena = {
        ts: Date.now(),
        code: msg.code,
        eid: msg.eid,
        vid: msg.vid,
        seed: msg.seed,
        atk_data: msg.atk_data,
        def_data: msg.def_data,
      };
    } catch (e) {
      window.__bc.combat_arena_err = String(e);
    }
  }, window);
  nm.addEventListener('rogue.rogue_main_combat_s2c', function (msg) {
    try {
      window.__bc.combat_rogue = {
        ts: Date.now(),
        code: msg.code,
        seed: msg.seed,
        atk_data: msg.atk_data,
        def_data: msg.def_data,
      };
    } catch (e) {
      window.__bc.combat_rogue_err = String(e);
    }
  }, window);
  window.__bc_hooks = true;
  return 'installed';
}
"""


def install_hooks(page: Any) -> str:
    return str(page.evaluate(INSTALL_JS) or "")


def set_block_result(page: Any, block: bool) -> None:
    page.evaluate("(b) => { if (window.__bc) window.__bc.block = !!b; }", bool(block))


def clear_combat(page: Any, mode: str) -> None:
    key = "combat_arena" if mode == "arena" else "combat_rogue"
    page.evaluate(
        "(k) => { if (window.__bc) window.__bc[k] = null; }",
        key,
    )


def take_combat(page: Any, mode: str, timeout_s: float = 20.0) -> Optional[Dict[str, Any]]:
    key = "combat_arena" if mode == "arena" else "combat_rogue"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = page.evaluate(
            """(k) => {
              if (!window.__bc) return null;
              const m = window.__bc[k];
              if (!m) return null;
              window.__bc[k] = null;
              return m;
            }""",
            key,
        )
        if msg:
            return msg
        time.sleep(0.1)
    return None


def send_result(page: Any, mode: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """用原始 send 送 result（不受 block 影響）。"""
    if mode == "arena":
        cmd = "arena.arena_result_c2s"
    elif mode == "rogue":
        cmd = "rogue.rogue_main_result_c2s"
    else:
        raise ValueError(mode)
    return page.evaluate(
        """({ cmd, body }) => {
          const nm = window.netManager;
          if (!nm) return { ok: false, err: 'no_netManager' };
          const send = nm.__bc_orig_send || nm.send.bind(nm);
          try {
            // 短暫解除 block，確保走 orig
            const prev = window.__bc ? window.__bc.block : false;
            if (window.__bc) window.__bc.block = false;
            send(cmd, body);
            if (window.__bc) window.__bc.block = prev;
            return { ok: true, cmd: cmd, body: body };
          } catch (e) {
            return { ok: false, err: String(e) };
          }
        }""",
        {"cmd": cmd, "body": body},
    )

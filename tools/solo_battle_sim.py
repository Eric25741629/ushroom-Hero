# -*- coding: utf-8 -*-
"""切磋戰鬥本地模擬（透過 CDP 呼叫官方 BattleMainServer）。

client 真實流程 (PvpControl.on_solo_start_s2c)::

    const sim = new BattleMainServer(seed);
    sim.start(battleData);  # playerList[1]=atk, [2]=def, chapterType=RoleSolo(12)
    while (sim.runState === Running) sim.update(sim.frameTime);  # 0.033s
    winner = (sim.result === 0) ? atk.id : def.id;
    reqSoloResult(vid, winner);

用法::

    python tools/solo_battle_sim.py spar --port 9230     # 點目前 RoleNotice 的切磋並模擬
    python tools/solo_battle_sim.py wait --port 9230     # 等你手動點切磋
    python tools/solo_battle_sim.py replay foo.json --port 9230
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DEFAULT_PORT = 9230
GAME_HOST = "mushroomh5.acenetgame.com"

INSTALL_JS = r"""
() => {
  if (window.__solo_sim_hook) return 'already';
  const nm = window.netManager;
  if (!nm || typeof nm.addEventListener !== 'function') return 'no_netManager';
  window.__solo_sim_last = null;
  nm.addEventListener('solo.solo_start_s2c', function (msg) {
    try {
      window.__solo_sim_last = {
        ts: Date.now(),
        code: msg.code,
        target_id: msg.target_id,
        vid: msg.vid,
        seed: msg.seed,
        atk_data: msg.atk_data,
        def_data: msg.def_data,
      };
    } catch (e) {
      window.__solo_sim_last_err = String(e);
    }
  }, window);
  window.__solo_sim_hook = true;
  return 'installed';
}
"""

SIM_JS = r"""
async ({ times }) => {
  const msg = window.__solo_sim_payload_full;
  if (!msg || msg.seed == null || !msg.atk_data || !msg.def_data) {
    return { ok: false, err: 'missing payload on window.__solo_sim_payload_full' };
  }
  const n = times || 1;
  const { BattleMainServer } = await System.import('chunks:///_virtual/BattleMainServer.ts');
  const { BattleData, PlayerData, ChapterType } = await System.import('chunks:///_virtual/BattleData.ts');
  const { BattleDataFill } = await System.import('chunks:///_virtual/BattleDataFill.ts');
  let RunState;
  try {
    RunState = (await System.import('chunks:///_virtual/EnumDefine.ts')).RunState;
  } catch (e) {
    RunState = { Running: 3 };
  }

  const seed = msg.seed;
  const atk = msg.atk_data;
  const def = msg.def_data;
  const results = [];

  for (let t = 0; t < n; t++) {
    const data = new BattleData();
    data.chapterId = 120001;
    data.chapterType = ChapterType.RoleSolo;
    data.seed = seed;
    data.playerList[1] = new PlayerData(1);
    data.playerList[2] = new PlayerData(2);
    BattleDataFill.setPlayerList(atk, data.playerList[1], ChapterType.RoleSolo);
    BattleDataFill.setPlayerList(def, data.playerList[2], ChapterType.RoleSolo);

    const sim = new BattleMainServer(seed);
    const t0 = performance.now();
    sim.start(data);
    let frames = 0;
    const maxFrames = 200000;
    while (sim.runState === RunState.Running && frames < maxFrames) {
      sim.update(sim.frameTime);
      frames++;
    }
    const ms = performance.now() - t0;
    const winnerId = (0 === sim.result) ? atk.id : def.id;
    results.push({
      result: sim.result,
      winnerId,
      winnerName: winnerId == atk.id ? atk.name : def.name,
      frames,
      ms: Math.round(ms * 100) / 100,
      runState: sim.runState,
    });
    try { sim.stop(); } catch (e) {}
  }

  return {
    ok: true,
    seed,
    chapterType: ChapterType.RoleSolo,
    chapterId: 120001,
    atk: { id: atk.id, name: atk.name, lev: atk.lev, power: atk.power },
    def: { id: def.id, name: def.name, lev: def.lev, power: def.power },
    results,
    deterministic: results.every(r => r.winnerId === results[0].winnerId && r.result === results[0].result),
  };
}
"""

CLICK_JS = r"""
() => {
  let btn = null;
  const walk = (n, path, d) => {
    if (!n || d > 16 || btn) return;
    const p = path + '/' + (n.name || '');
    if (
      n.name === 'btnSolo' && n.active &&
      p.includes('RoleNoticeView') && p.includes('content/btnSolo') &&
      !p.endsWith('btnSolo/btnSolo')
    ) btn = n;
    (n.children || []).forEach(c => walk(c, p, d + 1));
  };
  walk(cc.director.getScene(), '', 0);
  if (!btn) {
    const walk2 = (n, d) => {
      if (!n || d > 16 || btn) return;
      if (n.name === 'btnSolo' && n.active) btn = n;
      (n.children || []).forEach(c => walk2(c, d + 1));
    };
    walk2(cc.director.getScene(), 0);
  }
  if (!btn) return { ok: false, err: 'no_btnSolo (open RoleNoticeView first)' };
  btn.emit('click', btn);
  return { ok: true };
}
"""


def connect(port: int):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    page = next(
        p
        for ctx in browser.contexts
        for p in ctx.pages
        if GAME_HOST in (p.url or "") and "pwa-sw" not in (p.url or "")
    )
    return pw, page


def wait_msg(page, timeout_s: float = 60.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = page.evaluate(
            "() => { const m = window.__solo_sim_last; if (!m) return null; window.__solo_sim_last = null; return m; }"
        )
        if msg:
            return msg
        time.sleep(0.15)
    return None


def sim_msg(page, msg: dict, times: int) -> dict:
    page.evaluate("(m) => { window.__solo_sim_payload_full = m; }", msg)
    return page.evaluate(SIM_JS, {"times": times})


def do_spar(port: int, times: int) -> int:
    pw, page = connect(port)
    try:
        print("hook:", page.evaluate(INSTALL_JS))
        page.evaluate("() => { window.__solo_sim_last = null; }")
        clicked = page.evaluate(CLICK_JS)
        print("click:", clicked)
        if not clicked.get("ok"):
            return 1
        msg = wait_msg(page, 30)
        if not msg:
            print("timeout waiting solo_start_s2c")
            return 1
        if msg.get("code") not in (0, None):
            print("solo_start error code:", msg.get("code"))
            return 2
        print(
            f"capture vid={msg.get('vid')} seed={msg.get('seed')} "
            f"atk={msg['atk_data'].get('name')} power={msg['atk_data'].get('power')} "
            f"def={msg['def_data'].get('name')} power={msg['def_data'].get('power')}"
        )
        res = sim_msg(page, msg, times)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        # also dump payload for offline replay (may be large)
        out = Path("tools/_tmp_battle_extract/last_solo_start.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
        print("saved", out)
        return 0 if res.get("ok") else 3
    finally:
        pw.stop()


def do_wait(port: int, times: int) -> int:
    pw, page = connect(port)
    try:
        print("hook:", page.evaluate(INSTALL_JS))
        print("等待手動切磋...")
        msg = wait_msg(page, 120)
        if not msg:
            print("timeout")
            return 1
        print(f"capture vid={msg.get('vid')} seed={msg.get('seed')}")
        res = sim_msg(page, msg, times)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 3
    finally:
        pw.stop()


def do_replay(path: str, port: int, times: int) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "seed" not in data and "random_seed" in data:
        data = {
            "seed": data["random_seed"],
            "atk_data": (data.get("roles_left") or [None])[0] or data.get("atk_data"),
            "def_data": (data.get("roles_right") or [None])[0] or data.get("def_data"),
        }
    pw, page = connect(port)
    try:
        res = sim_msg(page, data, times)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 3
    finally:
        pw.stop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["spar", "wait", "replay"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--times", type=int, default=3)
    args = ap.parse_args()
    if args.cmd == "spar":
        return do_spar(args.port, args.times)
    if args.cmd == "wait":
        return do_wait(args.port, args.times)
    if args.cmd == "replay":
        if not args.path:
            print("need json path")
            return 1
        return do_replay(args.path, args.port, args.times)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

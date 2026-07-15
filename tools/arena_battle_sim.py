# -*- coding: utf-8 -*-
"""競技場戰鬥本地模擬（CDP 呼叫官方 BattleMainServer）。

client 真實流程 (PvpControl.on_arena_combat_s2c)::

    data.chapterId = 50001
    data.chapterType = ChapterType.Arena  # 5
    const sim = new BattleMainServer(seed)
    for (sim.start(data); sim.runState == Running; ) sim.update(sim.frameTime)
    wid = (sim.result === 0) ? atk.id : def.id
    reqArenaResult(vid, wid)

用法::

    python tools/arena_battle_sim.py spar --port 9230     # 點列表第 0 個 btnGo
    python tools/arena_battle_sim.py wait --port 9230     # 等手動挑戰
    python tools/arena_battle_sim.py replay last.json --port 9230
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
  if (window.__arena_sim_hook) {
    window.__arena_sim_last = null;
    window.__arena_sim_result = null;
    return 'already';
  }
  const nm = window.netManager;
  if (!nm || typeof nm.addEventListener !== 'function') return 'no_netManager';
  window.__arena_sim_last = null;
  window.__arena_sim_result = null;
  nm.addEventListener('arena.arena_combat_s2c', function (msg) {
    window.__arena_sim_last = {
      ts: Date.now(), code: msg.code, eid: msg.eid, vid: msg.vid, seed: msg.seed,
      atk_data: msg.atk_data, def_data: msg.def_data,
    };
  }, window);
  nm.addEventListener('arena.arena_result_s2c', function (msg) {
    window.__arena_sim_result = {
      ts: Date.now(), is_win: msg.is_win, my_score: msg.my_score, my_rank: msg.my_rank,
      my_score_change: msg.my_score_change, e_name: msg.e_name, e_rank: msg.e_rank,
      e_score: msg.e_score, e_score_change: msg.e_score_change,
    };
  }, window);
  window.__arena_sim_hook = true;
  return 'installed';
}
"""

SIM_JS = r"""
async ({ times }) => {
  const msg = window.__arena_sim_payload_full;
  if (!msg || msg.seed == null || !msg.atk_data || !msg.def_data) {
    return { ok: false, err: 'missing payload on window.__arena_sim_payload_full' };
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
    data.chapterId = 50001;
    data.chapterType = ChapterType.Arena;
    data.seed = seed;
    data.playerList[1] = new PlayerData(1);
    data.playerList[2] = new PlayerData(2);
    BattleDataFill.setPlayerList(atk, data.playerList[1], ChapterType.Arena);
    BattleDataFill.setPlayerList(def, data.playerList[2], ChapterType.Arena);

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
    vid: msg.vid,
    eid: msg.eid,
    chapterType: ChapterType.Arena,
    chapterId: 50001,
    atk: { id: atk.id, name: atk.name, lev: atk.lev, power: atk.power },
    def: { id: def.id, name: def.name, lev: def.lev, power: def.power },
    results,
    deterministic: results.every(
      r => r.winnerId === results[0].winnerId && r.result === results[0].result
    ),
  };
}
"""

CLICK_JS = r"""
() => {
  let node = null, path = null;
  const walk = (n, p, d) => {
    if (!n || d > 16 || node) return;
    const pp = p + '/' + (n.name || '');
    if (n.active && n.name === 'btnGo' && pp.includes('PvpChalleneView') && pp.includes('/0/')) {
      node = n; path = pp;
    }
    (n.children || []).forEach(c => walk(c, pp, d + 1));
  };
  walk(cc.director.getScene(), '', 0);
  if (!node) {
    const walk2 = (n, p, d) => {
      if (!n || d > 16 || node) return;
      const pp = p + '/' + (n.name || '');
      if (n.active && n.name === 'btnGo' && pp.includes('PvpChalleneView')) {
        node = n; path = pp;
      }
      (n.children || []).forEach(c => walk2(c, pp, d + 1));
    };
    walk2(cc.director.getScene(), '', 0);
  }
  if (!node) return { ok: false, err: 'no btnGo (open PvpChalleneView first)' };
  node.emit('click', node);
  return { ok: true, path };
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
            "() => { const m = window.__arena_sim_last; if (!m) return null; "
            "window.__arena_sim_last = null; return m; }"
        )
        if msg:
            return msg
        time.sleep(0.15)
    return None


def wait_result(page, timeout_s: float = 20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = page.evaluate(
            "() => { const m = window.__arena_sim_result; if (!m) return null; "
            "window.__arena_sim_result = null; return m; }"
        )
        if msg:
            return msg
        time.sleep(0.15)
    return None


def sim_msg(page, msg: dict, times: int) -> dict:
    page.evaluate("(m) => { window.__arena_sim_payload_full = m; }", msg)
    return page.evaluate(SIM_JS, {"times": times})


def do_spar(port: int, times: int) -> int:
    pw, page = connect(port)
    try:
        print("hook:", page.evaluate(INSTALL_JS))
        page.evaluate("() => { window.__arena_sim_last = null; window.__arena_sim_result = null; }")
        clicked = page.evaluate(CLICK_JS)
        print("click:", clicked)
        if not clicked.get("ok"):
            return 1
        msg = wait_msg(page, 30)
        if not msg:
            print("timeout waiting arena_combat_s2c")
            return 1
        if msg.get("code") not in (0, None):
            print("arena_combat error code:", msg.get("code"))
            return 2
        print(
            f"capture vid={msg.get('vid')} seed={msg.get('seed')} eid={msg.get('eid')} "
            f"atk={msg['atk_data'].get('name')} p={msg['atk_data'].get('power')} "
            f"def={msg['def_data'].get('name')} p={msg['def_data'].get('power')}"
        )
        res = sim_msg(page, msg, times)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        official = wait_result(page, 15)
        if official:
            our_win = res.get("ok") and res["results"][0]["winnerId"] == msg["atk_data"]["id"]
            match = (official.get("is_win") == 1) == our_win
            print("official:", official)
            print("match_official:", match, "our_win:", our_win)
        out = Path("tools/_tmp_battle_extract/last_arena_combat.json")
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
        print("等待手動競技場挑戰...")
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

# -*- coding: utf-8 -*-
"""B 頁：把 pure-WS 的 combat s2c raw bytes 解成物件後跑 BattleMainServer。"""
from __future__ import annotations

import base64
from typing import Any, Dict

DECODE_AND_SIM_JS = r"""
async ({ mode, body_b64 }) => {
  const nm = window.netManager;
  if (!nm || !nm.protoRoot) return { ok: false, err: 'no protoRoot' };
  const typeName = mode === 'rogue'
    ? 'rogue.rogue_main_combat_s2c'
    : mode === 'escort'
      ? 'escort.escort_battle_start_s2c'
      : 'arena.arena_combat_s2c';
  const Type = nm.protoRoot.lookupType(typeName);
  if (!Type) return { ok: false, err: 'no type ' + typeName };

  const bin = atob(body_b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const msg = Type.decode(bytes);

  const { BattleMainServer } = await System.import('chunks:///_virtual/BattleMainServer.ts');
  const { BattleData, PlayerData, ChapterType } = await System.import('chunks:///_virtual/BattleData.ts');
  const { BattleDataFill } = await System.import('chunks:///_virtual/BattleDataFill.ts');
  let RunState;
  try { RunState = (await System.import('chunks:///_virtual/EnumDefine.ts')).RunState; }
  catch (e) { RunState = { Running: 3 }; }

  let atk, defRole, chapterType, chapterId, seed, vid;
  if (mode === 'rogue') {
    chapterType = ChapterType.Rogue;
    chapterId = 50001;
    seed = msg.seed;
    atk = (msg.atk_data && msg.atk_data[0]) ? msg.atk_data[0] : msg.atk_data;
    defRole = (msg.def_data && msg.def_data[0]) ? msg.def_data[0] : msg.def_data;
    vid = null;
  } else if (mode === 'escort') {
    const cfg = (typeof configEscort_chapter !== 'undefined')
      ? configEscort_chapter.getDataByKey(Number(msg.type))
      : null;
    if (!cfg || !msg.roles || !msg.roles[0] || !msg.monsters || !msg.monsters[0]) {
      return { ok: false, err: 'escort missing chapter/role/monster', code: msg.code };
    }
    chapterType = cfg.part_type;
    chapterId = cfg.id;
    seed = msg.seed;
    atk = msg.roles[0];
    defRole = null;
    vid = null;
  } else {
    chapterType = ChapterType.Arena;
    chapterId = 50001;
    seed = msg.seed;
    atk = msg.atk_data;
    defRole = msg.def_data;
    vid = msg.vid;
  }
  if (seed == null || !atk || (mode !== 'escort' && !defRole)) {
    return { ok: false, err: 'decode missing seed/atk/def', code: msg.code };
  }

  const data = new BattleData();
  data.chapterId = chapterId;
  data.seed = seed;
  data.chapterType = chapterType;
  data.playerList[1] = new PlayerData(1);
  if (mode === 'escort') {
    data.monster = BattleDataFill.setMonster(msg.monsters[0]);
    BattleDataFill.setPlayerList(atk, data.playerList[1], chapterType);
  } else {
    data.playerList[2] = new PlayerData(2);
    BattleDataFill.setPlayerList(atk, data.playerList[1], chapterType);
    BattleDataFill.setPlayerList(defRole, data.playerList[2], chapterType);
  }

  const sim = new BattleMainServer(seed);
  const t0 = performance.now();
  sim.start(data);
  let frames = 0;
  while (sim.runState === RunState.Running && frames < 200000) {
    sim.update(sim.frameTime);
    frames++;
  }
  const ms = performance.now() - t0;
  const result = sim.result;
  let precent = null;
  let wid = null;
  if (mode === 'rogue') {
    try {
      const l = sim.chapter.arenaPlayerCtr;
      precent = Math.floor(l.player.data.currenHp / l.player.data.maxHp * 100);
    } catch (e) { precent = 0; }
  } else if (mode === 'escort') {
    wid = null;
  } else {
    wid = (0 === result) ? atk.id : defRole.id;
  }
  try { sim.stop(); } catch (e) {}
  return {
    ok: true,
    mode,
    seed: Number(seed),
    vid: vid != null ? Number(vid) : null,
    eid: msg.eid != null ? Number(msg.eid) : null,
    result,
    wid: wid != null ? Number(wid) : null,
    precent,
    frames,
    ms: Math.round(ms * 100) / 100,
    atk_id: atk.id != null ? Number(atk.id) : null,
    def_id: defRole && defRole.id != null ? Number(defRole.id) : null,
    code: msg.code,
  };
}
"""


def simulate_combat_body(page: Any, mode: str, body: bytes) -> Dict[str, Any]:
    b64 = base64.b64encode(body).decode("ascii")
    out = page.evaluate(DECODE_AND_SIM_JS, {"mode": mode, "body_b64": b64})
    if not isinstance(out, dict):
        return {"ok": False, "err": "non-dict sim"}
    return out

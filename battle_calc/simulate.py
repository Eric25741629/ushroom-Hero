# -*- coding: utf-8 -*-
"""在 Playwright page 上跑官方 BattleMainServer；或轉發 B HTTP。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .config import get_battle_calc_global
from .modes import CHAPTER_ID, CHAPTER_TYPE_NAME, build_sim_request

SIM_JS = r"""
async (req) => {
  const mode = req.mode;
  const seed = req.seed;
  const atk = req.atk_data;
  const defRole = req.def_data;
  if (seed == null || !atk || !defRole) {
    return { ok: false, err: 'missing seed/atk/def' };
  }
  const { BattleMainServer } = await System.import('chunks:///_virtual/BattleMainServer.ts');
  const { BattleData, PlayerData, ChapterType } = await System.import('chunks:///_virtual/BattleData.ts');
  const { BattleDataFill } = await System.import('chunks:///_virtual/BattleDataFill.ts');
  let RunState;
  try {
    RunState = (await System.import('chunks:///_virtual/EnumDefine.ts')).RunState;
  } catch (e) {
    RunState = { Running: 3 };
  }
  const typeName = req.chapter_type_name || 'Arena';
  const chapterType = ChapterType[typeName];
  const chapterId = req.chapter_id || 50001;
  if (chapterType == null) {
    return { ok: false, err: 'unknown ChapterType.' + typeName };
  }

  const data = new BattleData();
  data.chapterId = chapterId;
  data.seed = seed;
  data.chapterType = chapterType;
  data.playerList[1] = new PlayerData(1);
  data.playerList[2] = new PlayerData(2);
  BattleDataFill.setPlayerList(atk, data.playerList[1], chapterType);
  BattleDataFill.setPlayerList(defRole, data.playerList[2], chapterType);

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
  try {
    if (mode === 'rogue') {
      const l = sim.chapter.arenaPlayerCtr;
      const hp = l.player.data.currenHp;
      const maxHp = l.player.data.maxHp;
      precent = Math.floor(hp / maxHp * 100);
    } else {
      wid = (0 === result) ? atk.id : defRole.id;
    }
  } catch (e) {
    precent = 0;
    if (mode !== 'rogue') wid = (0 === result) ? atk.id : defRole.id;
  }
  try { sim.stop(); } catch (e) {}
  return {
    ok: true,
    mode,
    seed,
    result,
    wid,
    precent,
    frames,
    ms: Math.round(ms * 100) / 100,
    vid: req.vid,
  };
}
"""


def simulate_on_page(page: Any, mode: str, combat: Dict[str, Any]) -> Dict[str, Any]:
    req = build_sim_request(mode, combat)
    # Playwright 會把 combat 序列化進頁；同頁 live 物件亦可直接傳
    out = page.evaluate(SIM_JS, req)
    if not isinstance(out, dict):
        return {"ok": False, "err": "sim returned non-dict"}
    return out


def result_body_from_sim(mode: str, combat: Dict[str, Any], sim: Dict[str, Any]) -> Dict[str, Any]:
    if not sim.get("ok"):
        raise RuntimeError(sim.get("err") or "sim failed")
    if mode == "arena":
        vid = combat.get("vid")
        wid = sim.get("wid")
        if vid is None or wid is None:
            raise RuntimeError("arena sim missing vid/wid")
        return {"vid": vid, "wid": wid}
    if mode == "rogue":
        return {
            "result": int(sim.get("result", 1)),
            "precent": int(sim.get("precent") or 0),
        }
    raise ValueError(mode)


def simulate_remote(
    mode: str,
    combat: Dict[str, Any],
    *,
    global_cfg: Optional[Dict[str, Any]] = None,
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """POST 到 B HTTP；B 必須已掛好 CDP 遊戲頁。"""
    bc = get_battle_calc_global(global_cfg)
    if not bc.get("enabled"):
        return {"ok": False, "err": "battle_calc global disabled"}
    host = bc["http_host"]
    port = int(bc["http_port"])
    to = float(timeout_sec if timeout_sec is not None else bc["timeout_sec"])
    req = build_sim_request(mode, combat)
    url = f"http://{host}:{port}/v1/simulate"
    data = json.dumps({"mode": mode, "combat": combat, "request": req}, ensure_ascii=False).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=to) as resp:
            body = resp.read().decode("utf-8")
            out = json.loads(body)
            if not isinstance(out, dict):
                return {"ok": False, "err": "remote non-dict"}
            return out
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = str(e)
        return {"ok": False, "err": f"http {e.code}: {detail}"}
    except Exception as e:
        return {"ok": False, "err": str(e)}


def health_remote(global_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    bc = get_battle_calc_global(global_cfg)
    host = bc["http_host"]
    port = int(bc["http_port"])
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "err": str(e)}

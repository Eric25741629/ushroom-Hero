"""Run the official WorldBoss battle engine on a calculator H5 page.

The live account stays pure WebSocket: this module only receives the raw
``dungeon_battle_more_start_s2c`` body and runs the same ``BattleMainServer``
that the H5 client uses.  It never sends a packet or touches the page's game
socket.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import time
from pathlib import Path
from typing import Any, Dict


WORLD_BOSS_SIM_JS = r"""
async ({ body_b64, max_frames, realtime, speed_scale }) => {
  const runSimulation = async () => {
    try {
      const nm = window.netManager;
      if (!nm || !nm.protoRoot) return { ok: false, err: 'no protoRoot' };
      const Type = nm.protoRoot.lookupType('dungeon.dungeon_battle_more_start_s2c');
      if (!Type) return { ok: false, err: 'no dungeon battle-more schema' };

      const raw = atob(body_b64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      const msg = Type.decode(bytes);
      if (Number(msg.code || 0) !== 0) {
        return { ok: false, err: 'start response code=' + Number(msg.code || 0) };
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
      if (ChapterType.WorldBoss == null) {
        return { ok: false, err: 'missing ChapterType.WorldBoss' };
      }

      // Mirrors DungeonControl.on_dungeon_battle_more_start_s2c exactly.
      const data = new BattleData();
      data.chapterId = Number(msg.dungeon_id || 0);
      data.chapterType = ChapterType.WorldBoss;
      data.chapterMode = 0;
      data.seed = Number(msg.random_seed || 0);
      data.battleCheckout = 2;
      for (const role of (msg.roles || [])) {
        const player = new PlayerData(role.id);
        data.playerList[role.id] = player;
        BattleDataFill.setPlayerList(role, player, ChapterType.WorldBoss);
      }
      BattleDataFill.setChapterExt(data, msg.ext || []);
      if (msg.deal_role && Number(msg.deal_role.id || 0) !== 0) {
        BattleDataFill.setDummy(data, msg.deal_role, ChapterType.WorldBoss);
      }

      const sim = new BattleMainServer(data.seed);
      const t0 = performance.now();
      sim.start(data);
      let frames = 0;
      const limit = Math.max(1, Number(max_frames || 30000));
      const scale = realtime ? Math.max(1, Math.min(10, Number(speed_scale || 2))) : 1;
      const frameMs = Math.max(1, Number(sim.frameTime || 0.033) * 1000);
      if (realtime) sim.timeScale = scale;

      while (sim.runState === RunState.Running && frames < limit) {
        const tick = performance.now();
        // Official BattleMain update path; do not inject chapter time or damage.
        sim.update(sim.frameTime);
        frames++;
        if (realtime) {
          const waitMs = frameMs - (performance.now() - tick);
          if (waitMs > 0) await new Promise(resolve => setTimeout(resolve, waitMs));
        }
      }
      const chapter = sim.chapter;
      const boss = chapter && chapter.bossUnit;
      const player = sim.mainCtr && sim.mainCtr.player;
      const playerDead = !!(player && player.isDead);
      const chapterTime = chapter ? Number(chapter.chapterTime || 0) : null;
      const endedBy = playerDead ? 'death'
        : (chapterTime !== null && chapterTime <= 0 ? 'time'
          : (sim.runState === RunState.Running ? 'frame_limit' : 'stopped'));
      const complete = !!boss && endedBy !== 'frame_limit';
      const out = {
        ok: !!boss,
        complete,
        err: boss ? (complete ? undefined : 'official battle did not finish')
          : 'WorldBoss bossUnit was not created',
        result: Number(sim.result || 0),
        run_state: Number(sim.runState || 0),
        ended_by: endedBy,
        player_dead: playerDead,
        speed_scale: scale,
        realtime: !!realtime,
        frames,
        ms: Math.round((performance.now() - t0) * 100) / 100,
        chapter_time: chapterTime,
        max_chapter_time: chapter ? Number(chapter.maxChapterTime || 0) : null,
        hp_num: boss ? String(boss.hpNum || 0) : '0',
        hurt_num: boss ? String(boss.hurtNum || 0) : '0',
        last_hurt_num: boss ? String(boss.lastHurtNum || 0) : '0',
        players: sim.playerCtrs ? sim.playerCtrs.length : 0,
      };
      try { sim.stop(); } catch (e) {}
      return out;
    } catch (e) {
      return { ok: false, err: String(e && (e.stack || e.message) || e) };
    }
  };

  if (!realtime) return await runSimulation();
  const jobs = window.__worldBossSimJobs || (window.__worldBossSimJobs = {});
  const jobId = 'worldboss-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  jobs[jobId] = { done: false };
  runSimulation().then(result => {
    jobs[jobId] = { done: true, result };
  }).catch(e => {
    jobs[jobId] = { done: true, result: { ok: false, err: String(e) } };
  });
  return { ok: true, pending: true, job_id: jobId, speed_scale: Number(speed_scale || 2) };
}
"""


WORLD_BOSS_SIM_POLL_JS = r"""
(job_id) => {
  const jobs = window.__worldBossSimJobs || {};
  const job = jobs[job_id];
  if (!job) return { done: true, result: { ok: false, err: 'simulation job not found' } };
  if (!job.done) return { done: false };
  const result = job.result;
  delete jobs[job_id];
  return { done: true, result };
}
"""


class RawCDPPage:
    """Small Playwright-compatible adapter for the repo's raw CDP client.

    Some running Chrome instances accept the DevTools protocol but stall during
    Playwright's multi-target ``connect_over_cdp`` attach. WorldBoss only needs
    ``evaluate``, so keep this fallback deliberately tiny and side-effect free.
    """

    def __init__(self, raw_cdp: Any):
        self._raw_cdp = raw_cdp

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        if arg is None:
            return self._raw_cdp.evaluate(expression)
        encoded = json.dumps(arg, ensure_ascii=False, separators=(",", ":"))
        return self._raw_cdp.evaluate(f"({expression})({encoded})")


def open_raw_cdp_runtime(cdp_port: int):
    """Attach to an existing game page without Playwright multi-target attach."""

    # ``tools`` is an existing script directory, not a Python package (and a
    # third-party module with the same name may already be importable), so load
    # the repo helper by its explicit path.
    helper = Path(__file__).resolve().parents[1] / "tools" / "rawcdp.py"
    spec = importlib.util.spec_from_file_location("mushroom_rawcdp", helper)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"raw CDP helper unavailable: {helper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    RawCDP = module.RawCDP

    raw = RawCDP(int(cdp_port), timeout=20.0)
    try:
        ready = raw.evaluate(
            "!!(window.netManager && window.netManager.protoRoot "
            "&& typeof System !== 'undefined')"
        )
        if not ready:
            raise RuntimeError(f"CDP {cdp_port} page missing protoRoot/System")
        return None, None, RawCDPPage(raw), "raw_cdp"
    except Exception:
        raw.close()
        raise


def close_raw_cdp_runtime(page: Any) -> None:
    raw = getattr(page, "_raw_cdp", None)
    if raw is not None:
        raw.close()


def simulate_start_body(
    page: Any,
    body: bytes,
    *,
    max_frames: int = 30_000,
    speed_scale: float = 2.0,
    realtime: bool = True,
    simulation_timeout_sec: float = 330.0,
) -> Dict[str, Any]:
    """用官方引擎以 2 倍實際速度計算，死亡或 600 秒邏輯時間即停止。"""

    payload = {
        "body_b64": base64.b64encode(bytes(body)).decode("ascii"),
        "max_frames": max(1, int(max_frames)),
        "speed_scale": max(1.0, min(10.0, float(speed_scale))),
        "realtime": bool(realtime),
    }
    out = page.evaluate(WORLD_BOSS_SIM_JS, payload)
    if not isinstance(out, dict):
        return {"ok": False, "err": "non-dict sim"}
    if not out.get("pending"):
        return out

    job_id = out.get("job_id")
    if not job_id:
        return {"ok": False, "err": "simulation job missing id"}
    deadline = time.monotonic() + max(1.0, float(simulation_timeout_sec))
    while time.monotonic() < deadline:
        status = page.evaluate(WORLD_BOSS_SIM_POLL_JS, job_id)
        if isinstance(status, dict) and status.get("done"):
            result = status.get("result")
            return result if isinstance(result, dict) else {"ok": False, "err": "invalid simulation result"}
        time.sleep(0.5)
    return {"ok": False, "err": "official simulation timeout"}

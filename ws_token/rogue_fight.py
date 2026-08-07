# -*- coding: utf-8 -*-
"""萬神試煉 pure WS 完整路徑：WS enter → (combat → B sim → result)×N → over。

Live-verified 7fe98fc6 CDP 9226（2026-07-28）。

架構對齊 ws_token/arena_fight.py：
  - RogueFightOutcome / RogueFightReport dataclass
  - fight_once(client, page)  → 1關：combat → B sim → result
  - run_rogue_run(client, page, *, stages=80)  → enter → fight_once loop → over
  - run_with_b(client, ...)  → 開B → run_rogue_run → 關B

B 頁與 arena 相同：
  - prefer_ephemeral=True → 全新瀏覽器（無 profile，WS 踢線後 JS 仍可算）
  - cdp_port=N → 連既有 CDP（live 測 / debug 時用）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from battle_calc.b_page_sim import simulate_combat_body
from battle_calc.runner import enforce_gap
from ws_token import rogue as rogue_mod
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

_PACE = 1.2          # 每關之間最小間隔秒數（對齊 rogue_h5._PACE）
_MAX_STAGES = 80     # 單局安全上限


# ─── dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class RogueFightOutcome:
    ok: bool
    stage: int = 0
    sim_ms: float | None = None
    result: int | None = None   # 0=我方勝, 1=失敗
    precent: int | None = None  # 我方殘血比例（0-100）
    error: str | None = None


@dataclass
class RogueFightReport:
    success: bool
    stages_fought: int = 0
    stages_won: int = 0
    rounds_completed: int = 0   # 完整跑完(enter→...→over)的局數，weekly_trials 用這個算「跑滿」
    cap_reached: bool = False
    cap_current: int | None = None
    cap_max: int | None = None
    outcomes: list[RogueFightOutcome] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "stages_fought": self.stages_fought,
            "stages_won": self.stages_won,
            "rounds_completed": self.rounds_completed,
            "cap_reached": self.cap_reached,
            "cap_current": self.cap_current,
            "cap_max": self.cap_max,
            "error": self.error,
            "skipped": self.skipped,
            "outcomes": [
                {
                    "ok": o.ok,
                    "stage": o.stage,
                    "sim_ms": o.sim_ms,
                    "result": o.result,
                    "precent": o.precent,
                    "error": o.error,
                }
                for o in self.outcomes
            ],
        }


# ─── B 頁管理（對齊 arena_fight） ─────────────────────────────────────────────

def connect_b_page(cdp_port: int):
    """連既有 CDP 頁，回 (pw, None, page)。呼叫端負責 pw.stop()。"""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{int(cdp_port)}")
    page = next(
        (
            p
            for ctx in browser.contexts
            for p in ctx.pages
            if "mushroomh5" in (p.url or "") and "pwa-sw" not in (p.url or "")
        ),
        None,
    )
    if page is None:
        pw.stop()
        raise RuntimeError(f"no mushroomh5 page on CDP {cdp_port}")
    warm = page.evaluate(
        "() => !!(window.netManager && window.netManager.protoRoot "
        "&& typeof System !== 'undefined')"
    )
    if not warm:
        pw.stop()
        raise RuntimeError(f"CDP {cdp_port} page missing protoRoot/System")
    return pw, None, page


def open_b_runtime(
    *,
    prefer_ephemeral: bool = True,
    cdp_port: Optional[int] = None,
    game_url: Optional[str] = None,
    headless: bool = True,
    ready_timeout_sec: float = 90.0,
):
    """開 B 計算機。Returns (pw, browser_or_none, page, kind)。"""
    if prefer_ephemeral or not cdp_port:
        from battle_calc.ephemeral_b import launch_ephemeral_b

        pw, browser, page = launch_ephemeral_b(
            game_url=game_url or "https://mushroomh5.acenetgame.com/",
            headless=headless,
            timeout_s=ready_timeout_sec,
        )
        return pw, browser, page, "ephemeral"
    pw, browser, page = connect_b_page(int(cdp_port))
    return pw, browser, page, "cdp"


def close_b_runtime(pw, browser, *, kind: str = "ephemeral") -> None:
    if kind == "ephemeral":
        from battle_calc.ephemeral_b import close_ephemeral

        close_ephemeral(pw, browser)
        return
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass


# ─── 核心邏輯 ─────────────────────────────────────────────────────────────────

def fight_once(
    client: WSGameClient,
    page: Any,
    *,
    stage: int = 0,
) -> RogueFightOutcome:
    """打 1 關：combat_c2s → B sim → result_c2s。
    result=0 → 我方勝；result=1 → 失敗。
    """
    try:
        combat = rogue_mod.start_combat(client)
        if not combat.success:
            return RogueFightOutcome(
                ok=False, stage=stage, error=combat.error or "combat failed"
            )

        sim = simulate_combat_body(page, "rogue", combat.body)
        if not sim.get("ok"):
            return RogueFightOutcome(
                ok=False, stage=stage,
                error=f"sim failed: {sim.get('err') or sim}",
            )

        result = int(sim.get("result", 1))
        precent = int(sim.get("precent") or 0)
        sim_ms = sim.get("ms")

        ack = rogue_mod.report_result(client, result, precent)
        if not ack.success:
            return RogueFightOutcome(
                ok=False, stage=stage,
                sim_ms=sim_ms, result=result, precent=precent,
                error=ack.error or "result_ack failed",
            )

        return RogueFightOutcome(
            ok=True,
            stage=stage,
            sim_ms=sim_ms,
            result=result,
            precent=precent,
        )
    except Exception as e:
        logger.exception("rogue pure_ws fight_once stage=%d failed", stage)
        return RogueFightOutcome(ok=False, stage=stage, error=str(e))


def run_rogue_run(
    client: WSGameClient,
    page: Any,
    *,
    stages: int = _MAX_STAGES,
    should_abort=None,
) -> RogueFightReport:
    """一局完整流程：check status → 清舊局 → enter → fight loop → over。

    stages：單局最大關數安全上限（預設 80，同 _MAX_STAGES）。
    """
    report = RogueFightReport(success=False)

    # 1. 查狀態：5554 live 已驗證 field1=1 代表有尚未結算的 run。
    status = None
    try:
        status = rogue_mod.fetch_status(client)
        logger.info("rogue pure_ws status: has_active_run=%s raw=%s",
                    status.has_active_run, status.raw_status)
    except Exception as e:
        logger.warning("rogue pure_ws: fetch_status failed (non-fatal): %s", e)

    # 2. 邊界清理：active run 代表上一輪尚未結算。5554 live 證實此狀態直接
    # enter 後再送 start_reward_info(0x4C24) 不會收到回覆，必須先 over 舊局。
    if status is not None and status.has_active_run:
        logger.info("rogue pure_ws: active run detected, end it before restart")
        try:
            previous = rogue_mod.end_run(client, return_type=0)
        except Exception as e:
            report.error = f"active run cleanup exception: {e}"
            return report
        if not previous.success:
            report.error = f"active run cleanup failed: {previous.error}"
            return report
        logger.info("rogue pure_ws: previous active run ended")

    # 3. 開新局（rogue_main_enter_s2c 依 schema 無 code 欄位，任何非
    # error 回覆即成功 —— 見 ws_token/rogue.py parse_enter）。
    logger.info("rogue pure_ws: enter run (return_type=1)")
    try:
        enter = rogue_mod.enter_run(client, return_type=1)
        if not enter.success:
            report.error = f"enter failed: {enter.error}"
            return report
        logger.info("rogue pure_ws: enter ok code=%s fields=%s",
                    enter.code, {k: v for k, v in enter.fields.items()
                                 if not isinstance(v, (bytes, bytearray))})
    except Exception as e:
        report.error = f"enter exception: {e}"
        return report

    # 4. 打關迴圈。5554 現行協議 enter 後可直接 combat；舊版帳號若第一關
    # 回 server error 2，才補送 start_reward 0x4C24/0x4C26 並重試一次。
    last_t = 0.0
    for i in range(max(1, min(_MAX_STAGES, stages))):
        if should_abort and should_abort():
            report.error = "aborted"
            break

        last_t = enforce_gap(last_t, _PACE)
        stage_num = i + 1
        logger.info("rogue pure_ws: stage %d/%d", stage_num, stages)

        out = fight_once(client, page, stage=stage_num)
        if stage_num == 1 and not out.ok and out.error == "server error 2":
            logger.info("rogue pure_ws: combat requires start_reward, confirm and retry")
            try:
                info = rogue_mod.fetch_start_reward_info(client)
                if not info.success:
                    report.error = f"start_reward_info failed: {info.error}"
                    break
                confirm = rogue_mod.confirm_start_reward(client)
                if not confirm.success:
                    report.error = f"start_reward_confirm failed: {confirm.error}"
                    break
            except Exception as e:
                report.error = f"start_reward exception: {e}"
                break
            logger.info("rogue pure_ws: start_reward ok, retry stage 1")
            out = fight_once(client, page, stage=stage_num)
        report.outcomes.append(out)

        if not out.ok:
            report.error = out.error or "fight failed"
            logger.warning("rogue pure_ws: stage %d failed: %s", stage_num, out.error)
            break

        report.stages_fought += 1
        won = (out.result == 0)  # result=0 → 我方勝
        if won:
            report.stages_won += 1
        logger.info(
            "rogue pure_ws: stage %d ok result=%s precent=%s sim_ms=%s",
            stage_num, out.result, out.precent, out.sim_ms,
        )
        last_t = time.monotonic()

        if not won:
            logger.info("rogue pure_ws: stage %d 失敗 → 本局結束", stage_num)
            break

    # 5. 結束本局
    try:
        over = rogue_mod.end_run(client, return_type=0)
        logger.info("rogue pure_ws: over code=%s fields=%s",
                    over.code,
                    {k: v for k, v in over.fields.items()
                     if not isinstance(v, (bytes, bytearray))})
    except Exception as e:
        logger.warning("rogue pure_ws: end_run failed (non-fatal): %s", e)

    report.success = (report.error is None)
    report.rounds_completed = 1 if report.success else 0
    return report


def run_rogue_rounds(
    client: WSGameClient,
    page: Any,
    *,
    rounds: int = 1,
    stages: int = _MAX_STAGES,
    until_cap: bool = False,
    should_abort=None,
) -> RogueFightReport:
    """連續跑 N 局（每局：enter/skip→打到失敗→over），對齊 rogue_h5.run_rounds 語意。

    只要某局 run_rogue_run 失敗（非戰鬥失敗，是流程異常）就停止並回報已完成局數；
    單局內的『失敗一關』是正常結束(該局視為完成)，不算異常。
    """
    agg = RogueFightReport(success=False)
    last_current: int | None = None
    for i in range(max(1, int(rounds))):
        if should_abort and should_abort():
            agg.error = "aborted"
            break
        if until_cap:
            try:
                cap = rogue_mod.fetch_science_cap(client)
            except Exception as e:
                logger.warning("rogue pure_ws: fetch science cap failed: %s", e)
                agg.error = f"weekly cap read failed: {e}"
                break
            else:
                if not cap.success:
                    agg.error = f"weekly cap read failed: {cap.error}"
                    break
                agg.cap_current = cap.current
                agg.cap_max = cap.cap
                logger.info("rogue pure_ws: 本周獲取上限 %d/%d", cap.current, cap.cap)
                if cap.current >= cap.cap:
                    agg.cap_reached = True
                    break
                if last_current is not None and cap.current <= last_current:
                    agg.error = f"weekly cap made no progress ({last_current}->{cap.current})"
                    break
                last_current = cap.current
        logger.info("rogue pure_ws: round %d/%d", i + 1, rounds)
        one = run_rogue_run(client, page, stages=stages, should_abort=should_abort)
        agg.outcomes.extend(one.outcomes)
        agg.stages_fought += one.stages_fought
        agg.stages_won += one.stages_won
        if not one.success:
            agg.error = one.error
            logger.warning("rogue pure_ws: round %d failed: %s", i + 1, one.error)
            break
        agg.rounds_completed += 1
    if until_cap and not agg.cap_reached and agg.error is None:
        try:
            cap = rogue_mod.fetch_science_cap(client)
        except Exception as e:
            logger.warning("rogue pure_ws: final science cap read failed: %s", e)
        else:
            if cap.success:
                agg.cap_current = cap.current
                agg.cap_max = cap.cap
                agg.cap_reached = cap.current >= cap.cap
    if until_cap:
        agg.success = agg.cap_reached
        if not agg.success and agg.error is None:
            agg.error = "weekly cap not reached before safety limit"
    else:
        agg.success = agg.rounds_completed == max(1, int(rounds))
    return agg


def run_with_b(
    client: WSGameClient,
    *,
    rounds: int = 1,
    stages: int = _MAX_STAGES,
    until_cap: bool = False,
    should_abort=None,
    prefer_ephemeral: bool = True,
    cdp_port: Optional[int] = None,
    game_url: Optional[str] = None,
    headless: bool = True,
    ready_timeout_sec: float = 90.0,
) -> RogueFightReport:
    """開 B（預設全新免洗 ephemeral 瀏覽器）、跑 N 局萬神、關閉 B（不關 client）。"""
    try:
        pw, browser, page, kind = open_b_runtime(
            prefer_ephemeral=prefer_ephemeral,
            cdp_port=cdp_port,
            game_url=game_url,
            headless=headless,
            ready_timeout_sec=ready_timeout_sec,
        )
    except Exception as e:
        return RogueFightReport(success=False, error=f"B page: {e}")
    try:
        logger.info("rogue pure_ws B kind=%s", kind)
        return run_rogue_rounds(
            client, page, rounds=rounds, stages=stages,
            until_cap=until_cap, should_abort=should_abort
        )
    finally:
        close_b_runtime(pw, browser, kind=kind)

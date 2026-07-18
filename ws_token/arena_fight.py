# -*- coding: utf-8 -*-
"""競技場 pure WS 完整路徑：WS 開戰 → B 頁 BattleMainServer → WS 回 result。

Live-verified 小寶 7fe98fc6（2026-07-17）：
  combat body → B decode+sim → result → server is_win + score_change。

B 頁：
  - 裝置自己的 H5（被 pure WS 踢線後 JS 仍可算）
  - 或 global.battle_calc.cdp_port 免洗常駐頁
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from battle_calc.b_page_sim import simulate_combat_body
from battle_calc.config import coerce_arena_gap_sec
from battle_calc.runner import enforce_gap
from ws_token import arena as arena_mod
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

DEFAULT_FIGHTS = 3


@dataclass
class FightOutcome:
    ok: bool
    eid: int = 0
    seed: int = 0
    vid: int = 0
    wid: int = 0
    result: int | None = None
    is_win: int | None = None
    my_score_change: int | None = None
    sim_ms: float | None = None
    error: str | None = None


@dataclass
class ArenaFightReport:
    success: bool
    fought: int = 0
    wins: int = 0
    fights: list[FightOutcome] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "fought": self.fought,
            "wins": self.wins,
            "error": self.error,
            "skipped": self.skipped,
            "fights": [
                {
                    "ok": f.ok,
                    "eid": f.eid,
                    "seed": f.seed,
                    "vid": f.vid,
                    "wid": f.wid,
                    "result": f.result,
                    "is_win": f.is_win,
                    "my_score_change": f.my_score_change,
                    "sim_ms": f.sim_ms,
                    "error": f.error,
                }
                for f in self.fights
            ],
        }


def connect_b_page(cdp_port: int):
    """Attach 既有 CDP 並回 (playwright, None browser, page)。呼叫端 pw.stop()。"""
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
    """開 B 計算機。

    預設 ephemeral：全新瀏覽器、無 profile。
    Returns (pw, browser_or_none, page, kind) where kind is 'ephemeral'|'cdp'.
    """
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


def resolve_b_cdp_port(
    *,
    device_cdp: Optional[int] = None,
    calc_cdp: Optional[int] = None,
) -> Optional[int]:
    """（相容）優先免洗 CDP，否則裝置 web_debug_port。ephemeral 模式可不需要。"""
    for p in (calc_cdp, device_cdp):
        try:
            if p is not None and int(p) > 0:
                return int(p)
        except (TypeError, ValueError):
            continue
    return None


def fight_once(client: WSGameClient, page: Any, *, enemy_id: Optional[int] = None) -> FightOutcome:
    """打 1 場：info→選敵→combat→B sim→result。"""
    try:
        if enemy_id is None:
            info = arena_mod.fetch_info(client)
            if not info.success or not info.enemies:
                return FightOutcome(ok=False, error="no enemies")
            enemy = arena_mod.pick_weakest(info.enemies)
            if enemy is None:
                return FightOutcome(ok=False, error="no enemies")
            eid = enemy.id
        else:
            eid = int(enemy_id)

        combat = arena_mod.start_combat(client, eid)
        if not combat.success:
            return FightOutcome(ok=False, eid=eid, error=combat.error or "combat failed")

        sim = simulate_combat_body(page, "arena", combat.body)
        if not sim.get("ok") or sim.get("wid") is None:
            return FightOutcome(
                ok=False,
                eid=eid,
                seed=combat.seed,
                vid=combat.vid,
                error=f"sim failed: {sim.get('err') or sim}",
            )
        wid = int(sim["wid"])
        res = arena_mod.report_result(client, combat.vid, wid)
        if not res.success:
            return FightOutcome(
                ok=False,
                eid=eid,
                seed=combat.seed,
                vid=combat.vid,
                wid=wid,
                result=sim.get("result"),
                sim_ms=sim.get("ms"),
                error=res.error or "result failed",
            )
        return FightOutcome(
            ok=True,
            eid=eid,
            seed=combat.seed,
            vid=combat.vid,
            wid=wid,
            result=sim.get("result"),
            is_win=res.is_win,
            my_score_change=res.my_score_change,
            sim_ms=sim.get("ms"),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("arena pure_ws fight_once failed")
        return FightOutcome(ok=False, error=str(e))


def run_daily_challenges(
    client: WSGameClient,
    page: Any,
    *,
    fights: int = DEFAULT_FIGHTS,
    gap_sec: float = 7.0,
    should_abort=None,
) -> ArenaFightReport:
    """連續打 N 場（預設每日 3 場），間隔 ≥ gap_sec。"""
    fights = max(1, min(10, int(fights or DEFAULT_FIGHTS)))
    gap_sec = coerce_arena_gap_sec(gap_sec)
    report = ArenaFightReport(success=False)
    last = 0.0
    for i in range(fights):
        if should_abort and should_abort():
            report.error = "aborted"
            break
        last = enforce_gap(last, gap_sec)
        logger.info("ws_token arena: fight %d/%d", i + 1, fights)
        out = fight_once(client, page)
        report.fights.append(out)
        if not out.ok:
            report.error = out.error or "fight failed"
            logger.warning("ws_token arena: fight %d failed: %s", i + 1, out.error)
            break
        report.fought += 1
        if out.is_win == 1:
            report.wins += 1
        logger.info(
            "ws_token arena: fight %d ok is_win=%s score%+s sim_ms=%s",
            i + 1,
            out.is_win,
            out.my_score_change,
            out.sim_ms,
        )
        last = time.monotonic()
    report.success = report.fought == fights and report.error is None
    return report


def run_with_b(
    client: WSGameClient,
    *,
    fights: int = DEFAULT_FIGHTS,
    gap_sec: float = 7.0,
    should_abort=None,
    prefer_ephemeral: bool = True,
    cdp_port: Optional[int] = None,
    game_url: Optional[str] = None,
    headless: bool = True,
    ready_timeout_sec: float = 90.0,
) -> ArenaFightReport:
    """開 B（預設全新無 profile 瀏覽器）、打完、關閉 B（不關 client）。"""
    try:
        pw, browser, page, kind = open_b_runtime(
            prefer_ephemeral=prefer_ephemeral,
            cdp_port=cdp_port,
            game_url=game_url,
            headless=headless,
            ready_timeout_sec=ready_timeout_sec,
        )
    except Exception as e:  # noqa: BLE001
        return ArenaFightReport(success=False, error=f"B page: {e}")
    try:
        logger.info("arena pure_ws B kind=%s", kind)
        return run_daily_challenges(
            client, page, fights=fights, gap_sec=gap_sec, should_abort=should_abort
        )
    finally:
        close_b_runtime(pw, browser, kind=kind)


def run_with_cdp(
    client: WSGameClient,
    cdp_port: int,
    *,
    fights: int = DEFAULT_FIGHTS,
    gap_sec: float = 7.0,
    should_abort=None,
) -> ArenaFightReport:
    """相容舊呼叫：連既有 CDP 當 B。"""
    return run_with_b(
        client,
        fights=fights,
        gap_sec=gap_sec,
        should_abort=should_abort,
        prefer_ephemeral=False,
        cdp_port=cdp_port,
    )

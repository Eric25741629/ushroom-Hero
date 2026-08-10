# -*- coding: utf-8 -*-
"""競技場 pure WS 完整路徑：WS 開戰 → B 頁 BattleMainServer → WS 回 result。

Live-verified 小寶 7fe98fc6（2026-07-17）：
  combat body → B decode+sim → result → server is_win + score_change。

B 頁：
  - 裝置自己的 H5（被 pure WS 踢線後 JS 仍可算）
  - 或 global.battle_calc.cdp_port 免洗常駐頁

已打過對手黑名單（``DailyFoughtBlacklist``）：以日期為鍵持久化在
``ws_state/<device>.json`` 的 ``arena_fought`` 欄位，日期變更即自動重製
（每天重製、非永久），避免整份對手列表永遠被排除到「沒有對手可打」。
"""
from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from battle_calc.b_page_sim import simulate_combat_body
from battle_calc.config import (
    DEFAULT_ARENA_DAILY_FIGHTS,
    coerce_arena_daily_fights,
    coerce_arena_gap_sec,
)
from battle_calc.runner import enforce_gap
from ws_token import arena as arena_mod
from ws_token import state as ws_state
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

DEFAULT_FIGHTS = DEFAULT_ARENA_DAILY_FIGHTS

_TPE = datetime.timezone(datetime.timedelta(hours=8))


class DailyFoughtBlacklist:
    """今日已打過的對手黑名單；日期變更（台北時區）即清空重製。

    ``exclude`` 判斷與持久化都只依賴 eid；黑名單本身是「今日不要再打同一
    人」的客戶端保證，實際能否重複開戰仍以伺服器為準。
    """

    _STATE_KEY = "arena_fought"

    def __init__(self, device: Optional[str] = None, state_dir: Optional[Path] = None) -> None:
        self.device = device
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self._date = self._today()
        self._eids: set[int] = set()
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.datetime.now(_TPE).strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not self.device:
            return
        try:
            data = ws_state.load_state(
                self.device, state_dir=self.state_dir or ws_state.STATE_DIR
            )
            entry = data.get(self._STATE_KEY)
        except Exception:  # noqa: BLE001 — 黑名單是 advisory，讀壞一律重製
            return
        if not isinstance(entry, dict) or entry.get("date") != self._date:
            return  # 無記錄或日期已變 → 重製
        raw = entry.get("eids")
        if isinstance(raw, list):
            self._eids = {int(e) for e in raw if isinstance(e, (int, float, str))}

    def _persist(self) -> None:
        if not self.device:
            return
        try:
            data = ws_state.load_state(
                self.device, state_dir=self.state_dir or ws_state.STATE_DIR
            )
            data[self._STATE_KEY] = {
                "date": self._date,
                "eids": sorted(self._eids),
            }
            ws_state.save_state(
                self.device, data, state_dir=self.state_dir or ws_state.STATE_DIR
            )
        except Exception:  # noqa: BLE001 — 寫失敗不影響本輪戰鬥
            logger.warning("arena blacklist persist failed (advisory)", exc_info=True)

    def reset_if_new_day(self) -> bool:
        """日期已跨日 → 清空重製。回傳是否真的重製。"""
        today = self._today()
        if today == self._date:
            return False
        self._date = today
        self._eids.clear()
        return True

    def contains(self, eid: int) -> bool:
        return eid in self._eids

    def add(self, eid: int) -> None:
        self._eids.add(int(eid))
        self._persist()

    def as_set(self) -> frozenset[int]:
        return frozenset(self._eids)

    def count(self) -> int:
        """回傳今日已成功結算的不重複對手數。"""
        self.reset_if_new_day()
        return len(self._eids)


def daily_fight_plan(device: Optional[str], target: int) -> tuple[int, int]:
    """回傳（今日已打，本輪待補）；無裝置時視為從 0 開始。"""
    target = coerce_arena_daily_fights(target)
    fought = DailyFoughtBlacklist(device).count() if device else 0
    return fought, max(0, target - fought)


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


def fight_once(
    client: WSGameClient,
    page: Any,
    *,
    enemy_id: Optional[int] = None,
    blacklist: Optional[DailyFoughtBlacklist] = None,
) -> FightOutcome:
    """打 1 場：info→選敵（跳過今日黑名單）→combat→B sim→result。"""
    try:
        if enemy_id is None:
            info = arena_mod.fetch_info(client)
            if not info.success or not info.enemies:
                return FightOutcome(ok=False, error="no enemies")
            enemy = arena_mod.pick_weakest(
                info.enemies, exclude=blacklist.as_set() if blacklist else None
            )
            if enemy is None:
                return FightOutcome(ok=False, error="all enemies blacklisted")
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
        if blacklist is not None:
            blacklist.add(eid)
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
    blacklist: Optional[DailyFoughtBlacklist] = None,
) -> ArenaFightReport:
    """連續打 N 場（預設每日 9 場），間隔 ≥ gap_sec。

    遇到「所有對手都在今日黑名單」時先刷新一次對手列表再重試；刷新後仍
    無可用對手才中止（避免整天反覆打同一人，也避免誤刷浪費次數）。
    """
    fights = coerce_arena_daily_fights(fights or DEFAULT_FIGHTS)
    gap_sec = coerce_arena_gap_sec(gap_sec)
    report = ArenaFightReport(success=False)
    last = 0.0
    refreshed = False
    for i in range(fights):
        if should_abort and should_abort():
            report.error = "aborted"
            break
        last = enforce_gap(last, gap_sec)
        logger.info("ws_token arena: fight %d/%d", i + 1, fights)
        out = fight_once(client, page, blacklist=blacklist)
        if not out.ok and out.error == "all enemies blacklisted" and not refreshed:
            logger.warning("ws_token arena: 今日對手皆已打過，刷新列表後重試")
            refreshed = True
            if arena_mod.refresh_info(client) is not None:
                out = fight_once(client, page, blacklist=blacklist)
            else:
                out = FightOutcome(ok=False, error="refresh failed, all enemies blacklisted")
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
    device: Optional[str] = None,
) -> ArenaFightReport:
    """開 B（預設全新無 profile 瀏覽器）、打完、關閉 B（不關 client）。

    ``device`` 給定時啟用「今日已打過」黑名單（存於 ws_state/<device>.json）。
    """
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
        blacklist = DailyFoughtBlacklist(device) if device else None
        return run_daily_challenges(
            client,
            page,
            fights=fights,
            gap_sec=gap_sec,
            should_abort=should_abort,
            blacklist=blacklist,
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

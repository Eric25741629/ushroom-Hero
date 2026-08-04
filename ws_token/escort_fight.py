"""賞金之路純 WS 執行器：WS A 端 + 官方 BattleMainServer B 計算頁。"""
from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import json_manager
from battle_calc.b_page_sim import simulate_combat_body
from battle_calc.runner import enforce_gap
from ws_token import escort as escort_mod
from ws_token.arena_fight import close_b_runtime, open_b_runtime

logger = logging.getLogger(__name__)

RECORD_KEY = "escort_last_run"
COOLDOWN_SECONDS = 20 * 3600
START_HOUR = 11
WEEKEND = (5, 6)
DEFAULT_MAX_FIGHTS = 3
DEFAULT_GAP_SEC = 2.0


@dataclass(frozen=True)
class EscortFightOutcome:
    ok: bool
    target_id: int = 0
    seed: int = 0
    result: int | None = None
    server_result: int | None = None
    sim_ms: float | None = None
    error: str | None = None


@dataclass
class EscortFightReport:
    success: bool
    fought: int = 0
    wins: int = 0
    fights: list[EscortFightOutcome] = field(default_factory=list)
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
                    "target_id": f.target_id,
                    "seed": f.seed,
                    "result": f.result,
                    "server_result": f.server_result,
                    "sim_ms": f.sim_ms,
                    "error": f.error,
                }
                for f in self.fights
            ],
        }


def in_window(now: Optional[datetime.datetime] = None) -> bool:
    now = now or datetime.datetime.now()
    return now.weekday() in WEEKEND and now.hour >= START_HOUR


def is_due(device: str) -> bool:
    return bool(json_manager.is_record_expired(
        json_manager.return_time(device, name=RECORD_KEY), COOLDOWN_SECONDS
    ))


def _pick_monsters(monsters: tuple[escort_mod.EscortMonster, ...], limit: int):
    # 低戰力優先，與既有競技場挑弱者策略一致；server 仍會檢查 target_id。
    return sorted(monsters, key=lambda m: (m.power or 0, m.id))[:limit]


def run_with_b(
    client,
    *,
    device: str,
    max_fights: int = DEFAULT_MAX_FIGHTS,
    gap_sec: float = DEFAULT_GAP_SEC,
    should_abort=None,
    prefer_ephemeral: bool = True,
    cdp_port: Optional[int] = None,
    game_url: Optional[str] = None,
    headless: bool = True,
    ready_timeout_sec: float = 90.0,
) -> EscortFightReport:
    """在活動窗內打一輪 NPC；A 端完全不開活動 UI。"""
    if not in_window():
        return EscortFightReport(success=True, skipped="outside weekend window")
    if not is_due(device):
        return EscortFightReport(success=True, skipped="escort already run")

    info = escort_mod.fetch_info(client)
    if not info.success:
        return EscortFightReport(success=False, error=info.error or "escort info failed")
    monsters = _pick_monsters(info.monsters, max(1, min(10, int(max_fights or DEFAULT_MAX_FIGHTS))))
    if not monsters:
        return EscortFightReport(success=True, skipped="no NPC monsters")

    try:
        pw, browser, page, kind = open_b_runtime(
            prefer_ephemeral=prefer_ephemeral,
            cdp_port=cdp_port,
            game_url=game_url,
            headless=headless,
            ready_timeout_sec=ready_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        return EscortFightReport(success=False, error=f"B page: {exc}")

    report = EscortFightReport(success=False)
    last = 0.0
    try:
        for monster in monsters:
            if should_abort and should_abort():
                report.error = "aborted"
                break
            last = enforce_gap(last, max(0.0, float(gap_sec)))
            started = escort_mod.start_battle(client, monster.id)
            if not started.success:
                report.error = started.error or "battle start failed"
                report.fights.append(EscortFightOutcome(
                    ok=False, target_id=monster.id, error=report.error
                ))
                break
            sim = simulate_combat_body(page, "escort", started.body)
            result = sim.get("result") if sim.get("ok") else None
            if not sim.get("ok") or not isinstance(result, int):
                report.error = f"sim failed: {sim.get('err') or sim}"
                report.fights.append(EscortFightOutcome(
                    ok=False, target_id=monster.id, seed=started.seed, error=report.error
                ))
                break
            acknowledged = escort_mod.report_result(client, monster.id, result)
            if not acknowledged.success:
                report.error = acknowledged.error or "battle result failed"
                report.fights.append(EscortFightOutcome(
                    ok=False, target_id=monster.id, seed=started.seed,
                    result=result, sim_ms=sim.get("ms"), error=report.error,
                ))
                break
            report.fights.append(EscortFightOutcome(
                ok=True,
                target_id=monster.id,
                seed=started.seed,
                result=result,
                server_result=acknowledged.result,
                sim_ms=sim.get("ms"),
            ))
            report.fought += 1
            if result == 0:
                report.wins += 1
            last = time.monotonic()
    except Exception as exc:  # noqa: BLE001
        logger.exception("escort pure_ws run failed")
        report.error = str(exc)
    finally:
        close_b_runtime(pw, browser, kind=kind)

    report.success = report.fought > 0 and report.error is None
    if report.fought > 0:
        # 成功收到至少一個 result ack 才記錄，失敗可由下一輪重試。
        json_manager.time_recording(device, name=RECORD_KEY)
    return report

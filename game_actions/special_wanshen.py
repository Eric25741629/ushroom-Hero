"""寶兒/暴哥的萬神專用排程與執行流程。"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Optional

import bot_state
import config_manager
from json_manager import JsonDataManager


logger = logging.getLogger(__name__)

ATTEMPT_RECORD = "萬神專用_嘗試"
COMPLETE_RECORD = "萬神專用_完成"
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def _taipei_now(now: Optional[datetime.datetime] = None) -> datetime.datetime:
    if now is None:
        return datetime.datetime.now(TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=TAIPEI)
    return now.astimezone(TAIPEI)


def _record_datetime(record: Any) -> Optional[datetime.datetime]:
    if not isinstance(record, dict):
        return None
    try:
        if record.get("timestamp") is not None:
            return datetime.datetime.fromtimestamp(float(record["timestamp"]), TAIPEI)
        if record.get("date"):
            parsed = datetime.datetime.strptime(str(record["date"]), "%Y-%m-%d")
            return parsed.replace(tzinfo=TAIPEI)
    except (TypeError, ValueError, OSError):
        return None
    return None


def _record_matches_day(manager: Any, name: str, day: datetime.date) -> bool:
    recorded_at = _record_datetime(manager.get_record(name))
    return bool(recorded_at and recorded_at.date() == day)


def _record_matches_iso_week(
    manager: Any,
    name: str,
    now: datetime.datetime,
) -> bool:
    recorded_at = _record_datetime(manager.get_record(name))
    return bool(
        recorded_at
        and recorded_at.date().isocalendar()[:2] == now.date().isocalendar()[:2]
    )


def is_due(
    *,
    now: datetime.datetime,
    account: bool,
    enabled: bool,
    attempted_today: bool,
    completed_this_week: bool,
) -> bool:
    """週二至週六，台灣時間 03:00（含）至 07:00（不含）才可執行。"""
    local = _taipei_now(now)
    return bool(
        account
        and enabled
        and 1 <= local.weekday() <= 5
        and 3 <= local.hour < 7
        and not attempted_today
        and not completed_this_week
    )


def _next_attempt_at(
    now: datetime.datetime,
    *,
    account: bool,
    enabled: bool,
    attempted_today: bool,
    completed_this_week: bool,
) -> Optional[str]:
    if not account or not enabled or completed_this_week:
        return None

    for offset in range(8):
        day = now.date() + datetime.timedelta(days=offset)
        if not 1 <= day.weekday() <= 5:
            continue
        if offset == 0:
            if attempted_today or now.hour >= 7:
                continue
            if now.hour >= 3:
                return now.isoformat()
        candidate = datetime.datetime.combine(day, datetime.time(3, 0), TAIPEI)
        return candidate.isoformat()
    return None


def get_status(
    ip: str,
    *,
    cfg: Any = None,
    now: Optional[datetime.datetime] = None,
    manager: Any = None,
) -> dict:
    """回傳設定旗標、每日嘗試與每週完成狀態。"""
    cfg = cfg or config_manager.get_device_config(ip)
    local = _taipei_now(now)
    manager = manager or JsonDataManager(ip)
    account = bool(cfg.get("special_wanshen_account", False))
    enabled = bool(cfg.get("special_wanshen_enabled", False))
    rounds = int(cfg.get("special_wanshen_rounds", 10) or 10)
    attempted_today = _record_matches_day(manager, ATTEMPT_RECORD, local.date())
    completed_this_week = _record_matches_iso_week(manager, COMPLETE_RECORD, local)
    due = is_due(
        now=local,
        account=account,
        enabled=enabled,
        attempted_today=attempted_today,
        completed_this_week=completed_this_week,
    )
    return {
        "account": account,
        "enabled": enabled,
        "rounds": rounds,
        "attempted_today": attempted_today,
        "completed_this_week": completed_this_week,
        "due": due,
        "next_attempt_at": _next_attempt_at(
            local,
            account=account,
            enabled=enabled,
            attempted_today=attempted_today,
            completed_this_week=completed_this_week,
        ),
    }


def run_if_due(
    d: Any,
    ip: str,
    *,
    cfg: Any = None,
    now: Optional[datetime.datetime] = None,
    manager: Any = None,
    fight_fn: Optional[Callable[..., bool]] = None,
) -> dict:
    """時間到時只嘗試一次；成功才標記本週完成，失敗順延至下一個有效日。"""
    cfg = cfg or config_manager.get_device_config(ip)
    local = _taipei_now(now)
    manager = manager or JsonDataManager(ip)
    status = get_status(ip, cfg=cfg, now=local, manager=manager)
    if not status["due"]:
        return status

    # 先落盤再進戰鬥，避免程序中斷後同一天重複入場。
    manager.record_timestamp(ATTEMPT_RECORD)
    bot_state.update_state(ip, task="萬神試煉", step="萬神專用模式執行中")
    succeeded = False
    try:
        if fight_fn is None:
            import new_battle

            fight_fn = new_battle.fight_test
        succeeded = bool(fight_fn(d, rounds=status["rounds"]))
    except Exception:
        logger.exception("[%s] 萬神專用流程執行失敗", ip)

    if succeeded:
        manager.record_timestamp(COMPLETE_RECORD)
        bot_state.update_state(ip, task="萬神試煉", step="本週已完成，不再執行")
        logger.info("[%s] 萬神專用流程完成，共 %d 局", ip, status["rounds"])
    else:
        bot_state.update_state(ip, task="萬神試煉", step="今日已嘗試，順延下一個有效日")
        logger.warning("[%s] 萬神專用流程未完成，今日不再重試", ip)

    return get_status(ip, cfg=cfg, now=local, manager=manager)

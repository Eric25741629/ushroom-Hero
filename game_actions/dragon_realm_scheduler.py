"""龍骸聖域排程：三周週期 + 週三四五 10-22 時間窗 + 每日一次冷卻。"""
from __future__ import annotations

import datetime

import config_manager
from dragon_realm import use_dragon_realm
from json_manager import is_record_expired, return_time, time_recording
from utils.logging_utils import logger

_RECORD_KEY = "dragon_realm_last_run"
_COOLDOWN_SECONDS = 20 * 3600  # 20h 確保每日一次且不重入

# ponytail: 三周週期錨點 — 2026-06-22 (週一) 是已知活動週；若遊戲改週期需更新錨點
_ANCHOR_MONDAY = datetime.date(2026, 6, 22)
_CYCLE_DAYS = 21  # 3 weeks
_ACTIVE_WEEKDAYS = (2, 3, 4)  # Wed, Thu, Fri (Python weekday)
_OPEN_HOUR = 10
_CLOSE_HOUR = 22


def _is_dragon_week(today: datetime.date | None = None) -> bool:
    today = today or datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday - _ANCHOR_MONDAY).days % _CYCLE_DAYS == 0


def _within_open_window(now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now()
    if now.weekday() not in _ACTIVE_WEEKDAYS:
        return False
    return _OPEN_HOUR <= now.hour < _CLOSE_HOUR


def _is_due(ip: str, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now()
    if not _is_dragon_week(now.date()):
        return False
    if not _within_open_window(now):
        return False
    record = return_time(ip, name=_RECORD_KEY)
    return is_record_expired(record, _COOLDOWN_SECONDS)


def _mark_done(ip: str) -> None:
    time_recording(ip, name=_RECORD_KEY)


def run_dragon_realm_if_due(ip: str, d) -> None:
    config = config_manager.load_config()
    if not use_dragon_realm(ip, config):
        return
    if not _is_due(ip):
        return
    import dragon_realm.service as service
    logger.info("[dragon_realm] %s — 開始龍骸聖域", ip)
    report = service.run(ip, d)
    logger.info(
        "[dragon_realm] %s — 結束：%s（actions=%s waits=%s）",
        ip, report.stop_reason, report.actions, report.waits,
    )
    if report.stop_reason in ("reached_tier_three_gate", "out_of_stamina"):
        _mark_done(ip)

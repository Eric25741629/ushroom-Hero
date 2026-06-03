"""龍骸聖域排程：flag 閘控 + 10:00 時間閘 + 每日一次冷卻。在 daily_pipeline 尾段呼叫。"""
from __future__ import annotations

import datetime

import config_manager
from dragon_realm import use_dragon_realm
from json_manager import is_record_expired, return_time, time_recording
from utils.logging_utils import logger

_RECORD_KEY = "dragon_realm_last_run"
_COOLDOWN_SECONDS = 20 * 3600  # 20h 確保每日一次且不重入
_OPEN_HOUR = 10                 # 活動每天 10:00 才開（同 sea 的時間閘）


def _within_open_window(now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now()
    return now.hour >= _OPEN_HOUR


def _is_due(ip: str) -> bool:
    if not _within_open_window():
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

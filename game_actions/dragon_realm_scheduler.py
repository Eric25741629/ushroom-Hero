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


def is_dragon_open(now: datetime.datetime | None = None) -> bool:
    """龍骸聖域目前是否開放（三周週期的活動週 ∧ 週三四五 10-22 時間窗）。

    供 dashboard SOS 按鈕判斷可見性用；不含 per-device 冷卻（那是排程跑任務的事）。
    """
    now = now or datetime.datetime.now()
    return _is_dragon_week(now.date()) and _within_open_window(now)


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


def _record_date(record: dict) -> datetime.date | None:
    """從 time record 取出日期：優先 timestamp（epoch），退回 date 字串。"""
    ts = record.get("timestamp")
    if ts:
        try:
            return datetime.datetime.fromtimestamp(float(ts)).date()
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    date_str = record.get("date")
    if date_str:
        try:
            return datetime.datetime.strptime(str(date_str), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _cycle_completed(ip: str, now: datetime.datetime | None = None) -> bool:
    """本次活動週期是否已完成龍骸（到達三樓門）。

    供 WS 路徑（ws_token.runner._run_dragon_realm）用：到達三樓門後真人接手上
    三樓、bot 門前不再花體力，本活動週期已無事可做，須跳過後續每輪 WS 以免
    deadloop 空轉。

    完成標記重用 ADB/H5 路徑同一份 ``_RECORD_KEY`` timestamp（單一儲存來源，
    共用同一個 TimeRecordDataManager 記錄）。因龍骸只在活動週的週三四五開放、
    每個 21 天週期只有一個活動週，同一活動週共用同一個「週一」錨點；只要完成
    標記時間落在與 *now* 相同的活動週（同週一），即視為本週期已完成；下一活動
    週在 21 天後（不同週一），標記自動失效 → 自動重跑。

    註：``_RECORD_KEY`` 也被 ``run_dragon_realm_if_due`` 的 20h ``_is_due`` 節流
    寫入（ADB/H5 路徑，含 out_of_stamina），兩路徑刻意共用同一 key（同帳號完成
    即完成）。目標機 adb-fc65396d 為 adb+ws 且 ADB service.run 直接 not_h5 abort
    （不在標記集合、不寫 key），只有 WS 路徑會寫，故不受影響。
    """
    now = now or datetime.datetime.now()
    record = return_time(ip, name=_RECORD_KEY)
    if not record:
        return False
    last = _record_date(record)
    if last is None:
        return False
    last_monday = last - datetime.timedelta(days=last.weekday())
    now_monday = now.date() - datetime.timedelta(days=now.weekday())
    return last_monday == now_monday


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

"""副本排程：萬神試煉（週）、雙週副本（5556 only）。

從 new_main_v2.py Phase 4 抽離。

時間窗：
- 萬神試煉：週一下午（tm_hour > 12）起算，週二～週六，週日跳過
- 雙週副本：週六/週日 20:00–20:59，只在 `emulator-5556` 執行

排程狀態：
- 萬神試煉：`return_time(ip, "萬神試煉").is_next_week == True`
- 雙週副本：`return_time(ip, "雙週副本").is_next_biweek == True`
- 兩者都在首次執行（record is None）時跑
"""
from __future__ import annotations

import datetime

import bot_state
import config_manager
import new_battle
from game_actions import task_due
from game_actions.scheduler_policy import SchedulerPolicy
from json_manager import time_recording
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch


def _struct_to_dt(t) -> datetime.datetime:
    """time.struct_time → 等值 naive datetime，供 task_due.is_due 取用。

    只用到 weekday()/hour（與 tm_wday/tm_hour 一致），tz 不影響週副本判斷。
    """
    return datetime.datetime(*t[:6])


def _wanshen_due(ip: str, now: datetime.datetime | None) -> bool:
    return task_due.is_due("萬神試煉", ip, now)


def _biweekly_due(ip: str, now: datetime.datetime | None) -> bool:
    return task_due.is_due("雙週副本", ip, now)


_WEEKLY_POLICY = SchedulerPolicy(
    record_key="萬神試煉",
    due_hook=_wanshen_due,
)
_BIWEEKLY_POLICY = SchedulerPolicy(
    record_key="雙週副本",
    due_hook=_biweekly_due,
)


def _wanshen_rounds(ip: str) -> int:
    """讀本機 萬神試煉 每週開局數(可調 config `wanshen_rounds`，預設 8)。讀失敗則退預設。"""
    try:
        return int(config_manager.get_device_config(ip).get("wanshen_rounds", 8))
    except Exception:
        return 8


def run_offline_wanshen_if_due(ip: str, logger_obj=None) -> bool:
    """手機 ADB 離線時，若萬神到期則直接執行 pure WS。"""
    log = logger_obj or logger
    cfg = config_manager.get_device_config(ip)
    if not bool(cfg.get("enable_wanshen", True)):
        log.info("[%s] 離線萬神：功能已停用，跳過", ip)
        return False
    try:
        from battle_calc.config import coerce_wanshen_battle_mode
        mode = coerce_wanshen_battle_mode(
            cfg.get("wanshen_battle_mode", "pure_ws"), default="pure_ws"
        )
    except Exception:
        mode = "pure_ws"
    if mode != "pure_ws":
        log.info("[%s] 離線萬神：模式=%s，跳過（離線只支援 pure_ws）", ip, mode)
        return False
    now = datetime.datetime.now()
    if not _WEEKLY_POLICY.is_due(ip, now):
        return False

    from battle.weekly_trials import _run_pure_ws_wanshen

    rounds = _wanshen_rounds(ip)
    until_cap = bool(cfg.get("wanshen_until_cap", True))
    log.info("[%s] 離線萬神：到期，開始 pure WS（目標 %d 局）", ip, rounds)
    report = _run_pure_ws_wanshen(None, ip, rounds, cfg, until_cap=until_cap)
    if report is not None and report.success:
        _WEEKLY_POLICY.mark_done(ip, time_recording=time_recording)
        log.info("[%s] 離線萬神：pure WS 完成，已寫入本週紀錄", ip)
        return True
    log.warning("[%s] 離線萬神：pure WS 未完成，本週紀錄保留未完成", ip)
    return False


def _run_weekly_dungeon(
    d,
    ip: str,
    stage: str,
    enable_wanshen: bool,
    current_time,
) -> None:
    """Run 萬神試煉 if scheduled for this week and appropriate day/time."""
    # due 判斷唯一來源：task_due.is_due("萬神試煉")（record.is_next_week + 星期時間窗）。
    due = _WEEKLY_POLICY.is_due(ip, _struct_to_dt(current_time))
    logger.info(
        "[%s] 萬神試煉檢查: 頁面=%s 時間=%02d:%02d wday=%d 啟用=%s due=%s",
        ip, stage, current_time.tm_hour, current_time.tm_min,
        current_time.tm_wday, enable_wanshen, due,
    )
    if not enable_wanshen:
        logger.info("[%s] 萬神試煉：已停用，跳過", ip)
        return
    if not due:
        logger.info("[%s] 萬神試煉：本週已執行或未到執行時間窗，跳過", ip)
        return
    if stage != "主頁面":
        log_main_page_mismatch(d, ip, stage, "萬神試煉", "萬神試煉到達執行時間但不在主頁面")
        return
    rounds = _wanshen_rounds(ip)
    logger.info("[%s] 萬神試煉：條件滿足，開始執行 fight_test (目標 %d 局)", ip, rounds)
    bot_state.update_state(ip, task="萬神試煉", step="執行中")
    ok = new_battle.fight_test(d, rounds=rounds)
    if ok:
        _WEEKLY_POLICY.mark_done(ip, time_recording=time_recording)
        logger.info("[%s] 萬神試煉：完成(跑滿 %d 局)，已寫入本週記錄", ip, rounds)
    else:
        logger.warning("[%s] 萬神試煉：未跑滿 %d 局(入場/進場/結算失敗)，不寫記錄，下次重試", ip, rounds)


def _run_biweekly_dungeon(
    d,
    ip: str,
    stage: str,
    enable_biweekly: bool,
    now_local,
) -> None:
    """Run 雙週副本 for emulator-5556 on Sat/Sun at 20:xx if due this biweek."""
    # 裝置範圍(5556)/enable 是呼叫端閘門，維持在此；純 due（record.is_next_biweek +
    # 週六/日 20:xx 時間窗）唯一來源 = task_due.is_due("雙週副本")。
    if ip == "emulator-5556" and enable_biweekly:
        due = _BIWEEKLY_POLICY.is_due(ip, _struct_to_dt(now_local))
        logger.info(
            "[%s] 雙週副本檢查: 頁面=%s wday=%d hour=%02d due=%s",
            ip, stage, now_local.tm_wday, now_local.tm_hour, due,
        )
        if due:
            if stage == "主頁面":
                bot_state.update_state(ip, task="雙週副本", step="排程觸發與穩定流程")
                new_battle.run_biweekly_bounty_road_single(
                    d,
                    ip,
                    logger_obj=logger,
                    should_stop=lambda: bot_state.check_pause(ip) or bot_state.check_skip_sleep(ip),
                )
                _BIWEEKLY_POLICY.mark_done(ip, time_recording=time_recording)
            else:
                log_main_page_mismatch(d, ip, stage, "雙週副本", "雙週副本到達執行時間但不在主頁面")

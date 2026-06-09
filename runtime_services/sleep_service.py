"""Sleep-cycle orchestration for per-device main loops.

Extracted from new_main_v2.py (Phase 5 of slim-down plan). Provides:

- `calc_aligned_wake_ts`: pure helper — aligns the next wake-up to the
  00–20-min window of an hour. Now top-level (was a nested def inside
  run_sleep_cycle).
- `stop_runtime_device_for_sleep`: teardown helper for force-sleep,
  handles web_h5 vs adb backends.
- `StartupBypassError`: raised when startup fails and the caller wants
  the 30-min bypass sleep policy.
- `run_sleep_cycle`: orchestrates one hibernation window — state update,
  logging, call into `sleep_until_wake_or_interrupt`.
- `_maybe_resume_sleep`: resume-sleep gate at the top of main()'s while
  loop; handles 5554's online-check priority specially.
"""
from __future__ import annotations

import random
import time
from typing import Optional

import bot_state
import config_manager
from game_initialization import check_on_line
from json_manager import return_time
from runtime_services.device_runtime_service import sleep_until_wake_or_interrupt
from runtime_services.web_session_service import process_online_check_requests
from utils.car_fight_utils import adjust_wake_time_for_cars
from utils.logging_utils import logger


class StartupBypassError(Exception):
    """遊戲啟動失敗，觸發避讓休眠。"""
    pass


def _parse_hour_parity(value) -> Optional[int]:
    """Map a device-config value to an hour-parity constraint.

    ``'even'`` / ``0`` -> 0, ``'odd'`` / ``1`` -> 1; anything else
    (including ``None``) -> ``None`` (no constraint, keep legacy hourly
    behavior). ``bool`` is rejected so ``True``/``False`` don't sneak in
    as 1/0.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "even":
            return 0
        if v == "odd":
            return 1
        return None
    if isinstance(value, int) and value in (0, 1):
        return value
    return None


def _hour_parity_ok(hour_start_ts: float, hour_parity: Optional[int]) -> bool:
    """True if the LOCAL hour of ``hour_start_ts`` matches ``hour_parity``.

    ``hour_parity`` of ``None`` always matches (no constraint).
    """
    if hour_parity is None:
        return True
    return time.localtime(hour_start_ts).tm_hour % 2 == hour_parity


def _parse_minute_offset(value) -> Optional[int]:
    """Map a device-config value to a fixed wake-minute offset.

    A non-negative int (e.g. 0, 5, 15) -> that minute within the window;
    anything else (incl. ``None``, ``bool``) -> ``None`` (random minute,
    legacy behavior).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def calc_aligned_wake_ts(
    base_ts: float,
    min_sleep_sec: int,
    win_min: int = 20,
    hour_parity: Optional[int] = None,
    wake_minute_offset: Optional[int] = None,
) -> float:
    """Align wake-up to the next 00~win_min window of an hour.

    If the earliest legal wake time already lies inside the window,
    randomize within [earliest, win_end]; otherwise, roll to the next
    hour and randomize within 00~win_min minutes of it.

    When ``hour_parity`` is 0 (even) or 1 (odd), the wake hour is further
    constrained to that local-hour parity — splitting devices across
    even/odd hours so only half are active in any given hour (load
    distribution / 分流). The 00~win_min window is preserved either way,
    so the 深淵之門 window (only the first 20 min of an hour) stays
    reachable. ``None`` keeps the legacy hourly behavior.

    When ``wake_minute_offset`` is given, the device wakes at exactly that
    minute within the window (clamped to ``win_min``) instead of a random
    minute — so devices sharing the same hour can be staggered (one at
    :00, one at :05, the rest at :15, …). Combines with ``hour_parity``.
    """
    earliest = base_ts + min_sleep_sec
    hour_floor = earliest - (earliest % 3600)
    win_end = hour_floor + win_min * 60

    if wake_minute_offset is not None:
        # Deterministic per-device minute: roll to the next parity-matching
        # hour whose :MM target is not in the past, then wake exactly there.
        off_sec = max(0, min(int(wake_minute_offset), win_min)) * 60
        candidate = hour_floor
        while not (_hour_parity_ok(candidate, hour_parity) and candidate + off_sec >= earliest):
            candidate += 3600
        return float(candidate + off_sec)

    if earliest <= win_end and _hour_parity_ok(hour_floor, hour_parity):
        return float(random.randint(int(earliest), int(win_end)))

    # Roll forward hour-by-hour to the next window whose hour matches the
    # requested parity (parity None matches the very next hour = legacy).
    candidate = hour_floor + 3600
    while not _hour_parity_ok(candidate, hour_parity):
        candidate += 3600
    return float(candidate + random.randint(0, win_min * 60))


def stop_runtime_device_for_sleep(device_obj, ip: str, backend_kind: str, logger_obj) -> None:
    """Stop the current runtime device immediately for force-sleep handling."""
    if device_obj is None:
        logger_obj.warning(f"[{ip}] 強制休眠時找不到裝置實例，略過停止動作")
        return

    try:
        if backend_kind == "web_h5":
            close_fn = getattr(device_obj, "close", None)
            if callable(close_fn):
                close_fn()
                logger_obj.info(f"[{ip}] 強制休眠已關閉 web_h5 瀏覽器")
            else:
                device_obj.app_stop("com.mxdzz.tw.and")
                logger_obj.info(f"[{ip}] 強制休眠已停止 web_h5 會話")
        else:
            device_obj.app_stop("com.mxdzz.tw.and")
            logger_obj.info(f"[{ip}] 強制休眠已關閉 adb 應用")
    except Exception as stop_err:
        logger_obj.warning(f"[{ip}] 強制休眠停止裝置失敗: backend={backend_kind}, err={stop_err}")


def run_sleep_cycle(
    ip: str,
    logger_obj,
    *,
    forced_wake_ts: Optional[float] = None,
    force_sleep_now: bool = False,
    sleep_policy: str = "aligned_window",
    sleep_reason: str = "常規對齊喚醒",
    enable_dungeon_manager: bool = False,
):
    cur_ts = time.time()

    _cfg = config_manager.get_device_config(ip)
    _sleep_min = float(_cfg.get("sleep_min_hours", 1.0))
    _sleep_max = float(_cfg.get("sleep_max_hours", 1.0))
    min_sleep_sec = int(random.uniform(_sleep_min, max(_sleep_min, _sleep_max)) * 3600)

    if forced_wake_ts is not None:
        wake_ts = forced_wake_ts
    else:
        wake_ts = calc_aligned_wake_ts(
            cur_ts,
            min_sleep_sec,
            win_min=20,
            hour_parity=_parse_hour_parity(_cfg.get("wake_hour_parity")),
            wake_minute_offset=_parse_minute_offset(_cfg.get("wake_minute_offset")),
        )

    wake_ts = adjust_wake_time_for_cars(wake_ts)

    if ip == "emulator-5556" and enable_dungeon_manager:
        today_wday = time.localtime().tm_wday
        if today_wday in (5, 6):
            biweek_record = return_time(ip, name="雙週副本")
            should_execute_biweek = False

            if biweek_record is None:
                should_execute_biweek = True
            else:
                should_execute_biweek = biweek_record.get("is_next_biweek", False)

            if should_execute_biweek:
                now = time.time()
                today_midnight = now - (now % 86400) + 86400
                biweek_wake_ts = today_midnight - 86400 + 19 * 3600 + 57 * 60

                if biweek_wake_ts > now and biweek_wake_ts < wake_ts:
                    wake_ts = biweek_wake_ts
                    logger_obj.info(f"[{ip}] 雙週副本喚醒條件觸發，設定喚醒時間為 19:57")

    sleep_duration = max(0, int(wake_ts - cur_ts))
    wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
    bot_state.update_state(
        ip,
        task="休眠中",
        step=f"{sleep_reason} | policy={sleep_policy} | 預計休眠 {sleep_duration/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
        next_wake_at=wake_ts,
    )

    if forced_wake_ts is not None:
        logger_obj.info(
            f"[{ip}] 套用強制休眠策略: reason={sleep_reason}, policy={sleep_policy}, "
            f"預計休眠 {sleep_duration/60:.1f} 分鐘"
        )
    elif force_sleep_now:
        logger_obj.info(f"[{ip}] 已強制中斷當前任務，將直接進入休眠流程，預計休眠 {sleep_duration/60:.1f} 分鐘")
    elif "7fe98fc6" in ip:
        logger_obj.info(f"[{ip}] 裝置為 7fe98fc6，設定為每小時喚醒一次，預計休眠 {sleep_duration/60:.1f} 分鐘")
    else:
        logger_obj.info(f"[{ip}] 本次喚醒將落在每小時 00~20 分，預計休眠 {sleep_duration/60:.1f} 分鐘")

    interrupted = sleep_until_wake_or_interrupt(ip, wake_ts, logger_obj)
    return wake_ts, interrupted, time.time()


def _maybe_resume_sleep(
    ip: str,
    Cnn_model,
    resume_sleep_until_ts: Optional[float],
    resume_sleep_reason: str,
    logger_obj,
) -> tuple:
    """Handle resume-sleep gate at top of main while loop.

    Returns (updated_ts, updated_reason, should_continue).
    Caller does `if should_continue: continue`.
    """
    if bot_state.is_online_check_checker(ip):
        has_req = bot_state.has_pending_online_check_request(ip)
        has_priority = bot_state.is_online_check_priority_active(ip)
        if has_req or has_priority:
            process_online_check_requests(ip, Cnn_model, logger_obj, check_on_line)
            time.sleep(0.2)
            return resume_sleep_until_ts, resume_sleep_reason, True
        if resume_sleep_until_ts is not None and time.time() < resume_sleep_until_ts:
            wake_time_str = time.strftime("%H:%M", time.localtime(resume_sleep_until_ts))
            remain_sec = max(0, int(resume_sleep_until_ts - time.time()))
            detail = resume_sleep_reason or "返回休眠"
            bot_state.update_state(
                ip,
                task="休眠中",
                step=f"{detail} | 預計休眠 {remain_sec/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
                next_wake_at=resume_sleep_until_ts,
            )
            logger_obj.info(f"[{ip}] {detail}，返回休眠至 {wake_time_str}")
            interrupted = sleep_until_wake_or_interrupt(ip, resume_sleep_until_ts, logger_obj)
            if interrupted:
                return None, "", True
            return None, "", False
    elif resume_sleep_until_ts is not None and time.time() < resume_sleep_until_ts:
        wake_time_str = time.strftime("%H:%M", time.localtime(resume_sleep_until_ts))
        remain_sec = max(0, int(resume_sleep_until_ts - time.time()))
        detail = resume_sleep_reason or "返回休眠"
        bot_state.update_state(
            ip,
            task="休眠中",
            step=f"{detail} | 預計休眠 {remain_sec/60:.1f} 分鐘 (預計 {wake_time_str} 喚醒)",
            next_wake_at=resume_sleep_until_ts,
        )
        logger_obj.info(f"[{ip}] {detail}，返回休眠至 {wake_time_str}")
        interrupted = sleep_until_wake_or_interrupt(ip, resume_sleep_until_ts, logger_obj)
        if interrupted:
            return None, "", True
        return None, "", False
    return resume_sleep_until_ts, resume_sleep_reason, False

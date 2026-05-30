"""神燈（lamp）排程：從 new_main_v2.py 抽離的 _run_lamp + _run_lamp_if_due。

保持原本三分支邏輯：
  - phone OCR 模式（`use_phone_ocr_lamp_mode`）→ 永遠呼叫，is_compare=True
  - `emulator-5560` → 永遠呼叫，is_compare=False
  - 其他裝置（排除 5558）→ 依 `lamp_check_interval` 間隔判斷是否觸發
  - `emulator-5558` → 完全跳過
"""
from __future__ import annotations

import datetime
import random
import time

import bot_state
import config_manager
from json_manager import return_time, time_recording
from opengold_v2.lamp_service import LampService as _LampServiceV2
from runtime_services.device_scan_service import use_phone_ocr_lamp_mode
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch


def _run_lamp(d, ip: str, lamp_dur: int, is_compare: bool = True):
    """開神燈執行入口。V1(Open_gold_paddle_ocr)已廢棄，一律走 V2 LampService。

    （use_opengold_v2 旗標保留於 config 僅供文件/相容，路由已不再讀取它。）
    """
    duration = lamp_dur + random.randint(-10, 10)
    svc = _LampServiceV2(d, device_ip=ip)
    svc.run(times=duration, is_compare=is_compare)


def _run_phone_ocr_lamp(d, ip: str, stage: str) -> None:
    if stage != "主頁面":
        log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "手機 OCR 開神燈前不在主頁面")
        return
    lamp_dur = config_manager.get_device_config(ip).get("lamp_duration_sec", 300)
    bot_state.update_state(ip, task="開神燈 (OCR)", step=f"執行中 ({lamp_dur}s)")
    logger.info(f"[{ip}] 使用手機 OCR 開神燈模式，持續 {lamp_dur}s")
    _run_lamp(d, ip, lamp_dur, is_compare=True)


def _run_5560_lamp(d, ip: str, stage: str) -> None:
    if stage != "主頁面":
        log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "5560 OCR 開神燈前不在主頁面")
        return
    lamp_dur = int(config_manager.get_device_config(ip).get("lamp_duration_sec", 300))
    bot_state.update_state(ip, task="開神燈 (OCR)", step=f"5560 執行中 ({lamp_dur}s)")
    logger.info(f"[{ip}] 使用 5560 OCR 開神燈模式，持續 {lamp_dur}s")
    _run_lamp(d, ip, lamp_dur, is_compare=False)


def _evaluate_lamp_interval(ip: str, lamp_interval: float, lamp_record):
    """Decide whether the interval-gated general lamp should run now.

    Returns (should_run: bool, reason: str, last_dt_str: str, elapsed_sec: float|None).
    """
    last_ts = None
    if lamp_record and lamp_record.get("timestamp"):
        try:
            last_ts = float(lamp_record.get("timestamp"))
        except (TypeError, ValueError):
            last_ts = None

    if not (last_ts and last_ts > 0):
        return True, "no_valid_last_record", "None", None

    threshold_sec = lamp_interval * 3600.0
    elapsed_sec = max(0.0, time.time() - last_ts)
    last_dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
    if elapsed_sec >= threshold_sec:
        return True, "elapsed>=threshold", last_dt_str, elapsed_sec
    remaining_sec = max(0.0, threshold_sec - elapsed_sec)
    return False, f"elapsed<threshold, remaining={remaining_sec/60:.1f}m", last_dt_str, elapsed_sec


def _run_general_lamp(d, ip: str, stage: str) -> None:
    if stage != "主頁面":
        logger.info(f"[{ip}] 本輪跳過一般開神燈: stage={stage} (需主頁面)")
        log_main_page_mismatch(d, ip, stage, "開神燈", "一般開神燈前不在主頁面")
        return

    device_cfg = config_manager.get_device_config(ip)
    lamp_interval = float(device_cfg.get("lamp_check_interval", 2))
    lamp_dur = int(device_cfg.get("lamp_duration_sec", 300))
    lamp_record_name = "general_lamp_last_execution"

    if lamp_interval <= 0:
        logger.warning(f"[{ip}] lamp_check_interval={lamp_interval}h 非法，改用 2h")
        lamp_interval = 2.0

    should_run, reason, last_dt_str, elapsed_sec = _evaluate_lamp_interval(
        ip, lamp_interval, return_time(ip, name=lamp_record_name)
    )

    elapsed_h_text = "N/A" if elapsed_sec is None else f"{elapsed_sec/3600.0:.2f}"
    logger.info(
        f"[{ip}] 開神燈排程檢查: last={last_dt_str}, elapsed_h={elapsed_h_text}, "
        f"threshold_h={lamp_interval:.2f}, should_run={should_run}, reason={reason}"
    )

    if not should_run:
        logger.info(f"[{ip}] 本輪跳過一般開神燈: {reason}")
        return

    bot_state.update_state(ip, task="開神燈", step=f"執行中 ({lamp_dur}s)")
    logger.info(
        f"[{ip}] 觸發一般開神燈: duration={lamp_dur}s, interval_h={lamp_interval:.2f}, record={lamp_record_name}"
    )
    _run_lamp(d, ip, lamp_dur)
    time_recording(ip, name=lamp_record_name)
    logger.info(f"[{ip}] 一般開神燈完成，已更新執行時間記錄: {lamp_record_name}")


def _run_lamp_if_due(d, ip: str, stage: str) -> None:
    """Dispatch lamp run to exactly one of three mutually exclusive branches.

    Bug fix: previously the phone-OCR branch did not return, so phone-OCR
    devices fell through to the general-interval branch and ran the lamp
    twice per cycle.
    """
    if use_phone_ocr_lamp_mode(ip):
        _run_phone_ocr_lamp(d, ip, stage)
    elif 'emulator-5560' in ip:
        _run_5560_lamp(d, ip, stage)
    elif ip != "emulator-5558":
        _run_general_lamp(d, ip, stage)
    # else: emulator-5558 → no-op

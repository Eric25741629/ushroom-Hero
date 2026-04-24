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
import Open_gold_paddle_ocr
from json_manager import return_time, time_recording
from opengold_v2.lamp_service import LampService as _LampServiceV2
from runtime_services.device_scan_service import use_phone_ocr_lamp_mode
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch


def _run_lamp(d, ip: str, lamp_dur: int, is_compare: bool = True):
    """開神燈執行入口；use_opengold_v2=true 時走 LampService，否則走舊版。"""
    device_cfg = config_manager.get_device_config(ip)
    duration = lamp_dur + random.randint(-10, 10)
    if device_cfg.get("use_opengold_v2", False):
        svc = _LampServiceV2(d, device_ip=ip)
        svc.run(times=duration, is_compare=is_compare)
    else:
        Open_gold_paddle_ocr.open_the_gold(d, times=duration, is_compare=is_compare, device_ip=ip)


def _run_lamp_if_due(d, ip: str, stage: str) -> None:
    """Run lamp (神燈) for the appropriate device mode using the given stage."""
    if use_phone_ocr_lamp_mode(ip):
        if stage == "主頁面":
            lamp_dur = config_manager.get_device_config(ip).get("lamp_duration_sec", 300)
            bot_state.update_state(ip, task="開神燈 (OCR)", step=f"執行中 ({lamp_dur}s)")
            logger.info(f"[{ip}] 使用手機 OCR 開神燈模式，持續 {lamp_dur}s")
            _run_lamp(d, ip, lamp_dur, is_compare=True)
        else:
            log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "手機 OCR 開神燈前不在主頁面")
    if 'emulator-5560' in ip:
        if stage == "主頁面":
            lamp_dur = int(config_manager.get_device_config(ip).get("lamp_duration_sec", 300))
            bot_state.update_state(ip, task="開神燈 (OCR)", step=f"5560 執行中 ({lamp_dur}s)")
            logger.info(f"[{ip}] 使用 5560 OCR 開神燈模式，持續 {lamp_dur}s")
            _run_lamp(d, ip, lamp_dur, is_compare=False)
        else:
            log_main_page_mismatch(d, ip, stage, "開神燈 (OCR)", "5560 OCR 開神燈前不在主頁面")
    elif ip != "emulator-5558":
        if stage == "主頁面":
            device_cfg = config_manager.get_device_config(ip)
            lamp_interval = float(device_cfg.get("lamp_check_interval", 2))
            lamp_dur = int(device_cfg.get("lamp_duration_sec", 300))
            lamp_record_name = "general_lamp_last_execution"
            lamp_record = return_time(ip, name=lamp_record_name)
            now_ts = time.time()

            if lamp_interval <= 0:
                logger.warning(f"[{ip}] lamp_check_interval={lamp_interval}h 非法，改用 2h")
                lamp_interval = 2.0

            threshold_sec = lamp_interval * 3600.0
            last_ts = None
            last_dt_str = "None"
            elapsed_sec = None
            should_run_lamp = False
            reason = ""

            if lamp_record and lamp_record.get("timestamp"):
                try:
                    last_ts = float(lamp_record.get("timestamp"))
                except (TypeError, ValueError):
                    last_ts = None

            if last_ts and last_ts > 0:
                elapsed_sec = max(0.0, now_ts - last_ts)
                last_dt_str = datetime.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
                should_run_lamp = elapsed_sec >= threshold_sec
                remaining_sec = max(0.0, threshold_sec - elapsed_sec)
                reason = (
                    "elapsed>=threshold"
                    if should_run_lamp
                    else f"elapsed<threshold, remaining={remaining_sec/60:.1f}m"
                )
            else:
                should_run_lamp = True
                reason = "no_valid_last_record"

            elapsed_h_text = "N/A" if elapsed_sec is None else f"{elapsed_sec/3600.0:.2f}"
            logger.info(
                f"[{ip}] 開神燈排程檢查: last={last_dt_str}, elapsed_h={elapsed_h_text}, "
                f"threshold_h={lamp_interval:.2f}, should_run={should_run_lamp}, reason={reason}"
            )

            if should_run_lamp:
                bot_state.update_state(ip, task="開神燈", step=f"執行中 ({lamp_dur}s)")
                logger.info(
                    f"[{ip}] 觸發一般開神燈: duration={lamp_dur}s, interval_h={lamp_interval:.2f}, record={lamp_record_name}"
                )
                _run_lamp(d, ip, lamp_dur)
                time_recording(ip, name=lamp_record_name)
                logger.info(f"[{ip}] 一般開神燈完成，已更新執行時間記錄: {lamp_record_name}")
            else:
                logger.info(f"[{ip}] 本輪跳過一般開神燈: {reason}")
        else:
            logger.info(f"[{ip}] 本輪跳過一般開神燈: stage={stage} (需主頁面)")
            log_main_page_mismatch(d, ip, stage, "開神燈", "一般開神燈前不在主頁面")

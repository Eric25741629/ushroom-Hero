"""雙週副本 — 賞金之路 / 大盜來襲。

Sat/Sun 20:00 window MVP runner with slot dedupe, guarded steps,
safe exit, and recovery.
"""

import datetime
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import img_tools
from json_manager import return_time

from ._helpers import (
    _TPE,
    _append_biweekly_log,
    _compute_biweekly_slot_key,
    _recover_to_home,
    _record_biweekly_slot,
    _safe_click_step,
    logger,
)


def run_biweekly_bounty_road_single(
    d,
    ip: str,
    logger_obj: Optional[logging.Logger] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """MVP biweekly instance runner with slot dedupe, guarded steps, safe exit and recovery."""
    lg = logger_obj or logger
    now = datetime.datetime.now(_TPE)
    slot_key = _compute_biweekly_slot_key(now)
    if not slot_key:
        return False

    rec = return_time(ip, name="bounty_road_biweekly_slot") or {}
    if (
        isinstance(rec, dict)
        and rec.get("slot_key") == slot_key
        and rec.get("result") == "success"
    ):
        lg.info(f"[{ip}] biweekly slot already executed: {slot_key}")
        return False

    run_id = f"{ip}-{int(time.time())}"
    _record_biweekly_slot(ip, slot_key, "started", "enter", detail=run_id)
    _append_biweekly_log(ip, {"event": "started", "slot_key": slot_key, "run_id": run_id, "ts": datetime.datetime.now(_TPE).isoformat()})

    log_ctx: Dict[str, Any] = {
        "ts": datetime.datetime.now(_TPE).isoformat(),
        "device_id": ip,
        "run_id": run_id,
        "trigger_slot": slot_key,
        "phase": "10",
    }

    try:
        if not _safe_click_step(d, "賞金之路", retry=4, step_timeout_s=12, x_range=(0, 68), logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:賞金之路(入口)")
        if not _safe_click_step(d, "賞金之路", retry=4, step_timeout_s=12, logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:賞金之路")
        if not _safe_click_step(d, "大盜來襲", retry=4, step_timeout_s=12, logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:大盜來襲")
        _safe_click_step(d, "恭喜獲得", retry=2, step_timeout_s=4, logger_obj=lg)

        for _ in range(2):
            d.click(7, 167)
            time.sleep(0.5)

        for label in ("道具收集處", "高級", "10次", "挑戰"):
            if not _safe_click_step(d, label, retry=3, step_timeout_s=10, logger_obj=lg):
                raise RuntimeError(f"STEP_TIMEOUT:{label}")
        if not _safe_click_step(d, "開啟自動戰鬥", retry=3, step_timeout_s=10, x_range=(397, 502), logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:開啟自動戰鬥")

        started = time.time()
        max_duration_s = 10 * 60
        idle_cycles = 0
        max_idle_cycles = 18
        while True:
            if should_stop and should_stop():
                lg.info(f"[{ip}] biweekly interrupted by external stop")
                break
            elapsed = time.time() - started
            if elapsed > max_duration_s:
                lg.info(f"[{ip}] biweekly loop exit by max_duration_s={max_duration_s}")
                break
            if idle_cycles >= max_idle_cycles:
                lg.info(f"[{ip}] biweekly loop exit by idle_cycles={idle_cycles}")
                break

            progressed = False
            if img_tools.check_str_by_server(d, "高級"):
                time.sleep(10)
                progressed = True

            for label, shift_x in (("回復", 300), ("減少", 300), ("熄火", 300)):
                _safe_click_step(d, "餵食", retry=2, step_timeout_s=5, logger_obj=lg)
                if _safe_click_step(d, label, retry=2, step_timeout_s=5, shift_x=shift_x, logger_obj=lg):
                    time.sleep(1)
                    d.click(286, 500)
                    d.send_keys(text="999999999", clear=True)
                    d.click(100, 200)
                    time.sleep(0.2)
                    _safe_click_step(d, "使用", retry=2, step_timeout_s=5, logger_obj=lg)
                    progressed = True

            idle_cycles = 0 if progressed else (idle_cycles + 1)

        _record_biweekly_slot(ip, slot_key, "success", "done", detail=run_id)
        _append_biweekly_log(ip, {"event": "success", "slot_key": slot_key, "run_id": run_id, "ts": datetime.datetime.now(_TPE).isoformat()})
        lg.info(json.dumps({**log_ctx, "step": "done", "result": "success"}, ensure_ascii=False))
        return True
    except Exception as exc:
        recovered = _recover_to_home(d, logger_obj=lg)
        _record_biweekly_slot(ip, slot_key, "failed", "recover", detail=f"{run_id}|recovered={recovered}|err={exc}")
        _append_biweekly_log(ip, {"event": "failed", "slot_key": slot_key, "run_id": run_id, "recovered": recovered, "error": str(exc), "ts": datetime.datetime.now(_TPE).isoformat()})
        lg.error(json.dumps({**log_ctx, "step": "recover", "result": "failed", "error_code": "FLOW_EXCEPTION", "error_detail": str(exc), "recovery_result": recovered}, ensure_ascii=False))
        return False

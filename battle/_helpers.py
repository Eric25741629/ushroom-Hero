"""Battle-internal helpers shared across submodules.

Underscore prefix signals these are package-internal — callers should
import the public API from ``battle`` (the package ``__init__``).
"""

import datetime
import logging
import json
import os
import time
from typing import Any, Callable, Dict, Optional

import img_tools
from json_manager import JsonDataManager
from utils.log_paths import LogPaths

logger = logging.getLogger(__name__)

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _resolve_device_id(d: Any) -> str:
    """Resolve device id across ADB and Playwright backends."""
    try:
        adb_dev = getattr(d, "adb_device", None)
        if adb_dev is not None:
            info = getattr(adb_dev, "info", {}) or {}
            serial = info.get("serialno") or info.get("serial")
            if serial:
                return str(serial)
    except Exception:
        pass

    for attr in ("device_id", "serial", "device_serial"):
        value = getattr(d, attr, None)
        if value:
            return str(value)

    try:
        info = getattr(d, "device_info", {}) or {}
        serial = info.get("serial") or info.get("serialno")
        if serial:
            return str(serial)
    except Exception:
        pass

    try:
        info = getattr(d, "info", {}) or {}
        serial = info.get("serial") or info.get("serialno")
        if serial:
            return str(serial)
    except Exception:
        pass

    return "unknown"


def _compute_biweekly_slot_key(now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Return a slot key for Sat/Sun 20:00~20:04 (Asia/Taipei), else None."""
    if now is None:
        now = datetime.datetime.now(_TPE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_TPE)
    else:
        now = now.astimezone(_TPE)

    if now.weekday() not in (5, 6):
        return None
    if now.hour != 20 or now.minute > 4:
        return None
    return f"{now.strftime('%Y-%m-%d')}-20"


def _record_biweekly_slot(ip: str, slot_key: str, result: str, step: str, detail: str = "") -> None:
    manager = JsonDataManager(ip)
    manager.record_timestamp(
        "bounty_road_biweekly_slot",
        {
            "slot_key": slot_key,
            "result": result,
            "step": step,
            "detail": detail,
        },
    )


def _safe_click_step(
    d,
    label: str,
    retry: int = 3,
    step_timeout_s: int = 8,
    logger_obj: Optional[logging.Logger] = None,
    **kwargs,
) -> bool:
    lg = logger_obj or logger
    start = time.time()
    for attempt in range(1, retry + 1):
        if time.time() - start > step_timeout_s:
            break
        ok = False
        try:
            ok = bool(img_tools.click_str_by_server(d, label, **kwargs))
        except Exception as exc:
            lg.warning(f"safe_click exception label={label} attempt={attempt}: {exc}")
        if ok:
            return True
        time.sleep(min(2.0, 0.4 * attempt))
    return False


def _recover_to_home(d, logger_obj: Optional[logging.Logger] = None) -> bool:
    lg = logger_obj or logger
    try:
        for _ in range(3):
            d.click(509, 56)
            time.sleep(0.2)
        img_tools.click_str_by_server(d, "關閉")
        img_tools.click_str_by_server(d, "確定")
        d.click(274, 875)
        time.sleep(0.5)
        return True
    except Exception as exc:
        lg.warning(f"recover_to_home failed: {exc}")
        return False


def _append_biweekly_log(ip: str, payload: Dict[str, Any]) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        safe_ip = LogPaths.safe_device_id(ip)
        path = os.path.join("logs", f"biweekly_{safe_ip}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"append biweekly log failed: {exc}")

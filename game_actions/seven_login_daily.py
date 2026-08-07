"""每日七日登入獎勵入口（web_h5 page WS）。"""
from __future__ import annotations

from typing import Any

from utils.logging_utils import logger
from ws_token import seven_login


def _record_done(ip: str) -> None:
    """回寫 dashboard「七日登入」徽章（best-effort，失敗不阻塞每日流程）。"""
    try:
        from json_manager import time_recording
        time_recording(ip, name="七日登入")
    except Exception as exc:  # noqa: BLE001 - 紀錄失敗不能影響每日流程
        logger.warning("[%s] 回寫七日登入紀錄失敗: %s", ip, exc)


def run_seven_login_if_due(d: Any, ip: str) -> dict:
    """查詢 server 狀態並最多領一個獎勵；ADB/no-page 安全略過。

    領取成功（``ok``）或「今天已領/活動全領完」（``skipped=not_claimable``
    且 day>0）都等同今日完成 → 回寫「七日登入」徽章；活動未開始（day=0）
    不算完成，不回寫。
    """
    page = getattr(d, "_page", None)
    if page is None:
        return {"skipped": "no_page"}
    try:
        result = seven_login.apply_via_page(page, device=ip)
    except Exception as exc:  # noqa: BLE001 - 不讓單一獎勵阻塞每日流程
        logger.warning("[%s] 七日登入獎勵異常: %s", ip, exc)
        return {"error": str(exc)}
    if result.get("ok"):
        logger.info("[%s] 七日登入獎勵已領取第 %s 天", ip, result.get("claimed"))
        _record_done(ip)
    elif result.get("skipped") == "not_claimable":
        try:
            if int(result.get("day") or 0) > 0:
                logger.info("[%s] 七日登入今日已領/全領完，視為完成", ip)
                _record_done(ip)
        except (TypeError, ValueError):
            pass
    elif result.get("skipped") not in (None,):
        logger.info("[%s] 七日登入獎勵略過: %s", ip, result)
    return result

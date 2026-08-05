"""每日七日登入獎勵入口（web_h5 page WS）。"""
from __future__ import annotations

from typing import Any

from utils.logging_utils import logger
from ws_token import seven_login


def run_seven_login_if_due(d: Any, ip: str) -> dict:
    """查詢 server 狀態並最多領一個獎勵；ADB/no-page 安全略過。"""
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
    elif result.get("skipped") not in (None, "not_claimable"):
        logger.info("[%s] 七日登入獎勵略過: %s", ip, result)
    return result

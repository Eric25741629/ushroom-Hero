"""技能衝刺的唯讀活動進度查詢。

技能衝刺與遺物衝刺共用 ``act_cross_limited_rank_info`` 協議；活動類型會
輪替，因此不能只把本週的 270 寫死。這個模組只查詢伺服器進度，不會抽卡、
領獎或改變遊戲狀態。
"""
from __future__ import annotations

import logging
from typing import Optional

from ws_token import relic_sprint
from ws_token.client import WSError, WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# ActivityType.RankRush_3 / RankRush_New_8：兩個都是「技能衝刺」。
ACT_TYPES: tuple[int, ...] = (8, 270)


def read_progress(client: WSGameClient, *, timeout: Optional[float] = None) -> dict:
    """讀取目前開啟的技能衝刺進度。

    回傳 ``{"open": True, "act_type": ..., "draws": ...}``；兩個技能活動
    類型都未開啟時回傳 ``{"open": False}``。查詢逾時/協議錯誤會視為該
    類型未開啟，讓呼叫端採取不花券的保守行為。
    """
    for act_type in ACT_TYPES:
        try:
            snapshot = relic_sprint.read_sprint(client, act_type, timeout=timeout)
        except (WSTimeoutError, WSError) as exc:
            logger.info(
                "ws_token skill_sprint: act_type=%s 探測逾時/錯誤，視為關閉 (%s)",
                act_type,
                exc,
            )
            continue
        if snapshot.get("open"):
            return {
                "open": True,
                "act_type": int(snapshot.get("act_type") or act_type),
                "draws": max(0, int(snapshot.get("accrued", 0) or 0)),
            }

    logger.info("ws_token skill_sprint: no active skill sprint (probed %s)", list(ACT_TYPES))
    return {"open": False}


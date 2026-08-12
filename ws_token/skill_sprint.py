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
ROUND_IDS: tuple[int, ...] = tuple(range(1, relic_sprint.NUM_ROUNDS + 1))


def _claimable_round_ids(snapshot: dict) -> tuple[int, ...]:
    """從四個活動頁面逐一找出可領取的 group id。

    伺服器的領取命令只有 ``small_group_id``，同一輪的普通任務與 stage
    任務共用該 id；因此只要該輪任一任務是 CanGet，就送一次領取命令。
    """
    if len(snapshot.get("rounds") or ()) != relic_sprint.NUM_ROUNDS:
        return ()

    claimable = {
        int(group_id)
        for group_id in (snapshot.get("claimable_rounds") or ())
        if str(group_id).isdigit()
    }
    tasks = sorted(
        (task for task in (snapshot.get("tasks") or ()) if isinstance(task, dict)),
        key=lambda task: int(task.get("task_id", 0) or 0),
    )
    non_stage_count = relic_sprint.NUM_ROUNDS * relic_sprint.SUBTASKS_PER_ROUND
    if len(tasks) != non_stage_count + relic_sprint.NUM_ROUNDS:
        return ()
    for index, task in enumerate(tasks[:non_stage_count]):
        if int(task.get("status", 0) or 0) == relic_sprint.STATUS_CAN_GET:
            claimable.add(index // relic_sprint.SUBTASKS_PER_ROUND + 1)
    return tuple(group_id for group_id in ROUND_IDS if group_id in claimable)


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
            rounds = list(snapshot.get("rounds") or ())
            return {
                "open": True,
                "act_type": int(snapshot.get("act_type") or act_type),
                "draws": max(0, int(snapshot.get("accrued", 0) or 0)),
                "rounds": rounds,
                "claimable_rounds": list(snapshot.get("claimable_rounds") or ()),
                "tasks": list(snapshot.get("tasks") or ()),
            }

    logger.info("ws_token skill_sprint: no active skill sprint (probed %s)", list(ACT_TYPES))
    return {"open": False}


def claim_completed_rounds(
    client: WSGameClient,
    progress: dict,
    *,
    timeout: Optional[float] = None,
) -> list[int]:
    """逐一檢查四輪，為每個可領取輪次送一次 6575 領獎命令。"""
    if not progress.get("open"):
        return []

    claimable = set(_claimable_round_ids(progress))
    claimed: list[int] = []
    for group_id in ROUND_IDS:
        ready = group_id in claimable
        logger.info(
            "ws_token skill_sprint: 檢查第 %s/%s 輪: %s",
            group_id,
            len(ROUND_IDS),
            "可領取" if ready else "未完成或已領取",
        )
        if not ready:
            continue
        try:
            result = relic_sprint.claim_round(
                client, int(progress["act_type"]), group_id, timeout=timeout
            )
        except (WSTimeoutError, WSError) as exc:
            logger.warning(
                "ws_token skill_sprint: 第 %s/%s 輪領取逾時/錯誤 (%s)",
                group_id,
                len(ROUND_IDS),
                exc,
            )
            continue
        if result.success:
            claimed.append(group_id)
    return claimed


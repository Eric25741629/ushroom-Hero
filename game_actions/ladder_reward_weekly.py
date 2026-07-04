"""天梯每週獎勵 — 每週二自動套用已記錄的選擇 (web_h5, 走 H5 頁面 WS / CDP).

User policy (2026-06-21): 雙章節天梯「每週獎勵」每週二重送一次,讓選擇照記錄套用。
協議與記錄存檔見 ``ws_token.ladder_reward`` (cmd 0x4001, ``data/ladder_reward.json``)。

被 ``daily_pipeline`` 尾段呼叫,與 ``statue_weekly`` / ``dragon_realm_scheduler``
對齊:只在有 ``_page`` 的 web_h5 裝置上跑;閘控(週二 + 每週一次 + 該裝置有記錄)
全在 ``ladder_reward.apply_if_due`` 內,本檔只負責拿 page + today 並吞掉例外。
"""
from __future__ import annotations

import datetime
from typing import Any

from utils.logging_utils import logger
from ws_token import ladder_reward

_TZ = datetime.timezone(datetime.timedelta(hours=8))


def run_ladder_reward_if_due(d: Any, ip: str, *, today: datetime.date | None = None) -> dict:
    """Entry called by daily_pipeline. web_h5 only; safe no-op otherwise."""
    page = getattr(d, "_page", None)
    if page is None:
        return {"skipped": "no_page"}
    today = today or datetime.datetime.now(_TZ).date()
    try:
        result = ladder_reward.apply_if_due(ip, today, page=page)
    except Exception as exc:  # noqa: BLE001 - never break the pipeline
        logger.warning(f"[{ip}] 天梯每週獎勵套用失敗: {exc}")
        return {"error": str(exc)}
    if result.get("ok"):
        logger.info(f"[{ip}] 天梯每週獎勵已套用 {result['picks']} 項 ({result['week']})")
    return result

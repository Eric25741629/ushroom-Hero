"""看廣告補初級種子 — claim free 初級種子 via the watch-ad button.

User has the no-ad card, so tapping the ad button grants seeds instantly (no
video). 打工 then auto-plants them, so we only need to: open 一鍵種植 → tap the
ad button while it's available → close the 恭喜獲得 popup → close the dialog.

State detection ("看過廣告就不要再看了"): the daily watch count is persisted in
``farm_ad_seed`` (reset each day). We never exceed the daily limit nor re-watch
once today's quota is spent, even across several 8h farm visits. We also respect
the game's own (N/2) counter and only count a watch once GoodsGetView confirms a
real grant.

H5 is fully verified (7fe98fc6, 2026-05-31). ADB is an honest stub until it can
be verified on a real device — it logs a TODO and does nothing (never a fake
success).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from farm_v2.config import DAILY_AD_SEED_LIMIT, FARM_AD_SEED_MIN_HOUR
from farm_v2 import web_farm

if TYPE_CHECKING:  # pragma: no cover
    import uiautomator2 as uiauto

logger = logging.getLogger("farm_v2.ad_seed")

AD_RECORD = "farm_ad_seed"


def _today_count(time_manager) -> int:
    """Watches already done today (0 if the record is from a previous day)."""
    if not time_manager.is_same_day(AD_RECORD):
        return 0
    return int(time_manager.get_numeric_value(AD_RECORD, "count", 0))


def claim_ad_seeds(d: "uiauto.Device", device_ip: str, time_manager) -> int:
    """Top up 初級種子 by watching the (free, no-ad-card) seed ads.

    Returns the number of watches performed this call (0 if gated out / none
    available). Idempotent per day via the persisted ``farm_ad_seed`` count.
    """
    # Gate 1: time window. User says 8am is a safety margin; the game also only
    # surfaces seed actions after 8am, so keep it.
    if time.localtime().tm_hour < FARM_AD_SEED_MIN_HOUR:
        logger.info("[ad_seed] 未到 %d 點，跳過看廣告補種", FARM_AD_SEED_MIN_HOUR)
        return 0

    # Gate 2: persisted daily quota — "看過廣告就不要再看了".
    used = _today_count(time_manager)
    if used >= DAILY_AD_SEED_LIMIT:
        logger.info("[ad_seed] 今日已看 %d/%d 次廣告，跳過", used, DAILY_AD_SEED_LIMIT)
        return 0

    page = getattr(d, "_page", None)
    if page is None:
        # ADB not yet verified on a real device — honest stub, never fake success.
        logger.info("[ad_seed] ADB 後端尚未實作看廣告補種（TODO live 驗證），跳過")
        return 0

    done = _claim_h5(page, DAILY_AD_SEED_LIMIT - used)
    if done > 0:
        time_manager.record_timestamp(AD_RECORD, {"count": used + done})
        logger.info(
            "[ad_seed] 看廣告補種 %d 次（今日累計 %d/%d）",
            done, used + done, DAILY_AD_SEED_LIMIT,
        )
    return done


def _claim_h5(page, allowance: int) -> int:
    """H5 watch-ad loop. ``allowance`` = how many watches we may still do today."""
    if not web_farm.open_seed_select(page):
        logger.info("[ad_seed] 開啟種植選擇窗失敗，跳過")
        return 0

    done = 0
    try:
        for _ in range(max(0, allowance)):
            status = web_farm.seed_ad_status(page)
            ad = (status or {}).get("ad")
            if not ad or not ad.get("active"):
                break  # 初級種子 有貨 / 沒有廣告鈕
            remaining = ad.get("remaining")
            if remaining is not None and remaining <= 0:
                break  # (0/2) 用完 — active 仍為 True，靠 remaining 判斷
            if not web_farm.tap_seed_ad(page, ad):
                break
            time.sleep(2.5)
            if not web_farm.reward_open(page):
                logger.info("[ad_seed] 點廣告後無獎勵窗，停止（可能額度已滿）")
                break
            web_farm.close_reward(page)
            done += 1
            time.sleep(0.5)
    finally:
        web_farm.close_seed_select(page)
    return done

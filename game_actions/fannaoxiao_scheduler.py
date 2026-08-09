"""煩惱消 (act_type 224 / 左右消除) 排程：flag 閘控 + web_h5 backend 閘 + 每日一次。

在 daily_pipeline 尾段呼叫。設計與 dragon_realm_scheduler 對齊（最小可加性整合）：

  * 旗標 ``enable_fannaoxiao``（device cfg，預設 False）— 關閉時完全 no-op。
  * 只在 ``backend == web_h5`` 跑（需要 live Playwright page 讀 cocos 盤面）；
    adb backend 無盤面讀取路徑 → 直接 return。
  * 每日一次（``return_time`` / ``is_record_expired`` 20h 冷卻 + 跨日視為過期），
    成功才 ``time_recording`` 記日期 → 失敗下輪可重試。

進入活動 + 整局遊戲是 FREE（btnStart + 拖曳到 game over，零道具消耗；
可選的 btnColor/btnHammer power-ups 本任務一律不用）。``play_bounded`` 的
``max_moves`` 確保單局有界、絕不無限磨。
"""
from __future__ import annotations

from typing import Any, Optional

import config_manager
from json_manager import is_record_expired, return_time, time_recording
from game_actions.scheduler_policy import SchedulerPolicy
from utils.logging_utils import logger

_RECORD_KEY = "fannaoxiao_last_run"
_COOLDOWN_SECONDS = 20 * 3600  # 20h：確保每日一次且不重入

# 單局移動數上限（有界護欄）。recon 實測 ~79 步把分數推過 100；80 步足夠達標
# 又不會把一輪喚醒拖太久。move_pause>=0.6 讓 settle tween 在下次讀盤前完成
# （避免 pause=0.5 時偶發的 not_operable 早停，見 FANNAOXIAO_RECON §7.7）。
_MAX_MOVES = 80
# 0.75（>0.65）讓 settle tween 在下次讀盤前確實跑完；live 實測 0.6 仍偶發
# not_operable 早停（壓低分數），0.75 給足餘裕又不會把一輪喚醒拖太久。
_MOVE_PAUSE = 0.75


_POLICY = SchedulerPolicy(
    enabled_key="enable_fannaoxiao",
    backend="web_h5",
    record_key=_RECORD_KEY,
    cooldown_seconds=_COOLDOWN_SECONDS,
)


def _is_enabled(ip: str) -> bool:
    return _POLICY.is_enabled(ip, get_device_config=config_manager.get_device_config)


def _is_due(ip: str) -> bool:
    return _POLICY.is_due(
        ip,
        return_time=return_time,
        is_record_expired=is_record_expired,
    )


def _mark_done(ip: str) -> None:
    _POLICY.mark_done(ip, time_recording=time_recording)


def run_fannaoxiao_if_due(d: Any, ip: str, driver: Optional[Any] = None) -> None:
    """頂層守衛：flag? web_h5? 今日未跑? → 進活動跑一局有界遊戲並記錄。

    ``driver`` 可注入（測試用 fake）；預設用真實 ``game_actions.fannaoxiao_driver``。
    任何例外都被吞下（記 log），絕不讓本加性任務把 pipeline 拆掉。
    """
    if not _is_enabled(ip):
        return
    if not _is_due(ip):
        logger.info("[fannaoxiao] %s — 今日已跑過，跳過", ip)
        return

    page = getattr(d, "_page", None)
    if page is None:
        logger.warning("[fannaoxiao] %s — web_h5 backend 無 live page，跳過", ip)
        return

    if driver is None:
        from game_actions import fannaoxiao_driver as driver

    from utils import pause_guard
    pause_guard.bind(ip=ip, page=page)
    try:
        if not driver.enter_game(page):
            logger.warning("[fannaoxiao] %s — 無法進入遊戲（活動未開/banner 收合?），跳過", ip)
            return
        summary = driver.play_bounded(page, max_moves=_MAX_MOVES, move_pause=_MOVE_PAUSE)
        logger.info(
            "[fannaoxiao] %s — 結束：moves=%s score=%s level=%s rows=%s stopped=%s",
            ip,
            summary.get("moves"),
            summary.get("score"),
            summary.get("level"),
            summary.get("rows"),
            summary.get("stopped"),
        )
        # 只要實際開局並跑了一局（不論 game_over / max_moves / stuck），就算今日完成；
        # 把每日獎勵的「參與」算進去，避免同一天重複進活動。
        if summary.get("moves", 0) > 0:
            _mark_done(ip)
            logger.info("[fannaoxiao] %s — 已記錄今日執行", ip)
        else:
            logger.warning("[fannaoxiao] %s — 本局 0 移動，未記錄，下輪重試", ip)
    except pause_guard.TaskAborted as exc:
        logger.info("[fannaoxiao] %s — 任務被中斷：%s", ip, exc)
    except Exception:
        logger.exception("[fannaoxiao] %s — 任務異常", ip)
    finally:
        pause_guard.unbind()

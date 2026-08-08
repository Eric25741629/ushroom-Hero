"""賞金之路 (Escort) 排程：flag 閘 + web_h5 閘 + 週末/時段閘 + daily 冷卻。

在 daily_pipeline 尾段呼叫。設計對齊 fannaoxiao_scheduler（最小可加性整合），
差異在**閘門本質同跨服戰**（雙周末活動、與跨服戰交錯、平常關閉）：

  * 旗標 ``enable_escort``（device cfg，預設 False）— 關閉時完全 no-op。
  * 本檔只在 ``backend == web_h5`` 跑（H5 fallback；純 WS 由 ws_token runner 執行）。
  * 週末/時段閘（使用者指定）：僅**週六/週日**且本地時間 **>= 11:00** 才動作，
    否則直接 return（不讀 page、不導航）→ 週一~五「無須每日尋找」。
  * 開放偵測交給 driver（選擇器 item date=="當前開啟"，處理與跨服戰交錯的關週末）。
  * daily 冷卻（20h + 跨日過期）：NPC 每日刷新，故六日**各打一次**（週六記錄後
    週日跨日→過期→再打）= 一個週末共兩次；同日重複喚醒則跳過。成功才記錄，失敗下輪重試。

NPC 挑戰 FREE（不耗挑戰次數）；打贏變灰、打輸跳過（driver 每隻一輪最多一次）。
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

import config_manager
from json_manager import is_record_expired, return_time, time_recording
from utils.logging_utils import logger

_RECORD_KEY = "escort_last_run"
_COOLDOWN_SECONDS = 20 * 3600  # 20h：確保六日每天最多一輪且不重入
_START_HOUR = 11               # 使用者指定：11:00 後才開打
_WEEKEND = (5, 6)             # datetime.weekday(): 5=Sat, 6=Sun


def _local_now() -> datetime.datetime:
    """本地時間（測試可 monkeypatch 此函式模擬星期/時段）。"""
    return datetime.datetime.now()


def _is_enabled(ip: str) -> bool:
    cfg = config_manager.get_device_config(ip)
    if not bool(cfg.get("enable_escort", False)):
        return False
    return str(cfg.get("backend", "")).lower() == "web_h5"


def _in_window() -> bool:
    now = _local_now()
    return now.weekday() in _WEEKEND and now.hour >= _START_HOUR


def _is_due(ip: str) -> bool:
    record = return_time(ip, name=_RECORD_KEY)
    return is_record_expired(record, _COOLDOWN_SECONDS)


def _mark_done(ip: str) -> None:
    time_recording(ip, name=_RECORD_KEY)


def run_escort_if_due(d: Any, ip: str, driver: Optional[Any] = None) -> None:
    """頂層守衛：flag? web_h5? 六日&>=11點? 今日未跑? → 進賞金之路打一輪 NPC。

    ``driver`` 可注入（測試用 fake）；預設用真實 ``game_actions.escort_driver``。
    任何例外都被吞下（記 log），絕不讓本加性任務把 pipeline 拆掉。
    """
    if not _is_enabled(ip):
        return
    if not _in_window():
        return  # 非六日 / 未到 11 點：安靜 no-op（無須每日尋找）
    if not _is_due(ip):
        logger.info("[escort] %s — 今日已跑過，跳過", ip)
        return

    page = getattr(d, "_page", None)
    if page is None:
        logger.warning("[escort] %s — web_h5 backend 無 live page，跳過", ip)
        return

    if driver is None:
        from game_actions import escort_driver as driver

    from utils import pause_guard
    pause_guard.bind(ip=ip, page=page)
    try:
        if not driver.enter_escort(page):
            logger.info("[escort] %s — 未進入活動（非賞金之路週末/入口不存在），跳過", ip)
            driver.close_to_home(page)
            return
        summary = driver.fight_npc_round(page)
        logger.info(
            "[escort] %s — 結束：fought=%s win=%s lose=%s",
            ip, summary.get("count"), summary.get("win"), summary.get("lose"),
        )
        driver.close_to_home(page)
        # 只要實際打了 >=1 隻（不論勝負）就算今日完成，避免同日重進活動。
        if summary.get("count", 0) > 0:
            _mark_done(ip)
            logger.info("[escort] %s — 已記錄今日執行", ip)
        else:
            logger.info("[escort] %s — 本輪 0 NPC（可能已清空），不記錄，下輪重試", ip)
    except pause_guard.TaskAborted as exc:
        logger.info("[escort] %s — 任務被中斷：%s", ip, exc)
    except Exception:
        logger.exception("[escort] %s — 任務異常", ip)
    finally:
        pause_guard.unbind()

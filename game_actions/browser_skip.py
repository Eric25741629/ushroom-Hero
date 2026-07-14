"""Phase D1 — runtime skip-browser 純函式決策。

「戰鬥/每日任務都做完就不喚醒瀏覽器」的判斷抽成純函式，主迴圈只呼叫它、好單元測。

**行為安全優先**：任一條件不滿足或讀 config raise → 回 False（照開瀏覽器）。絕不能
誤跳過而漏做任務；opt-in（``skip_browser_when_all_done`` 預設 False）→ 預設行為與現況
完全相同、零風險。WS 失敗（``ws_login_ok`` False）時 WS-skippable 任務沒做 → 必須開
瀏覽器（開瀏覽器會刷新 ticket 自癒）。
"""
from __future__ import annotations

import datetime
from typing import Optional


def should_skip_browser(
    ip: str,
    *,
    ws_login_ok: bool,
    now: Optional[datetime.datetime] = None,
) -> bool:
    """回 True 當且僅當本輪可跳過喚醒瀏覽器：

    ``backend == "web_h5"`` AND ``skip_browser_when_all_done`` (預設 False) AND
    ``ws_token.enabled`` AND ``ws_login_ok`` AND ``not any_client_due(ip, now)``。

    任一不滿足 → False。讀 config / any_client_due 例外 → False（fail-safe）。
    """
    try:
        import config_manager

        cfg = config_manager.get_device_config(ip)
        if bool(cfg.get("special_wanshen_account", False)):
            return False
        if str(cfg.get("backend", "")).strip().lower() != "web_h5":
            return False
        if not bool(cfg.get("skip_browser_when_all_done", False)):
            return False
        if not bool((cfg.get("ws_token") or {}).get("enabled", False)):
            return False
    except Exception:
        return False  # fail-safe：讀 config 失敗 → 照開瀏覽器
    if not ws_login_ok:
        return False
    try:
        from game_actions.task_due import any_client_due

        if any_client_due(ip, now):
            return False
    except Exception:
        return False  # fail-safe：判斷失敗 → 照開瀏覽器
    return True

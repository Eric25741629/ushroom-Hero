"""Shared handling for manual wake-time overrides."""
from __future__ import annotations

import time

import bot_state


def apply_manual_wake_override(ip: str, current_wake_ts: float, logger_obj, *, task: str):
    """Consume one manual wake override and return ``(wake_ts, should_wake_now)``.

    Both startup staggering and normal sleep use this same one-shot path so the
    dashboard command has identical semantics in either countdown.
    """
    override_ts = bot_state.consume_wake_override(ip)
    if override_ts is None:
        return current_wake_ts, False

    try:
        wake_ts = float(override_ts)
    except Exception:
        wake_ts = time.time()

    now = time.time()
    remain_sec = max(0, int(wake_ts - now))
    step = "手動調整喚醒：立即開始" if remain_sec <= 0 else f"手動調整喚醒：{remain_sec} 秒後開始"
    bot_state.update_state(ip, task=task, step=step, next_wake_at=wake_ts)

    if remain_sec <= 0:
        logger_obj.info(f"[{ip}] 收到手動喚醒調整，立即開始")
        return wake_ts, True

    wake_str = time.strftime("%H:%M:%S", time.localtime(wake_ts))
    logger_obj.info(f"[{ip}] 收到手動喚醒調整，改為 {remain_sec} 秒後開始 ({wake_str})")
    return wake_ts, False

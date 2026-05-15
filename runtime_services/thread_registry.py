"""Shared synchronization primitives for the per-device thread registry.

H6 修法：`_running_threads`（在 ``new_main_v2.py``）會同時被 main thread
（scan_and_start_devices）與 device threads（handle_connect_failure
→ refresh_adb_server 內迭代）讀寫，必須有鎖保護以避免
``RuntimeError: dictionary changed size during iteration``、漏 join、
duplicate spawn 等資料競爭。

把鎖放在獨立模組可避免 ``new_main_v2`` 與 ``runtime_services``
之間的循環匯入（runtime_services 已被 bootstrap 在 import-time 使用）。
"""
from __future__ import annotations

import threading


# RLock 因為 refresh_adb_server 在持鎖期間會 callout 到 bot_state.set_pause，
# 後續若有任何路徑也想取此鎖（例如 logging callback）不會 self-deadlock。
running_threads_lock: threading.RLock = threading.RLock()


__all__ = ["running_threads_lock"]

"""開神燈的 registry executor adapter。

W8 只提供給後續 registry runner 使用的薄轉接層：client 路徑仍委派既有
``lamp_scheduler``，WS 路徑仍由現有 ``ws_token.runner`` 負責。這裡不複製
開神燈的 due policy、stage 判斷、批次迴圈或掉落處理。
"""
from __future__ import annotations

from typing import Any


def run_client(device: Any, ip: str, stage: str) -> Any:
    """以既有 client lamp scheduler 執行一次開神燈任務。

    延遲 import 是為了讓 registry 讀取與契約測試不載入 cv2、ADB 或其他
    client runtime 依賴；Task 18 傳入的 ``stage`` 原樣交給既有 scheduler。
    """
    from game_actions.lamp_scheduler import _run_lamp_if_due

    return _run_lamp_if_due(device, ip, stage)


__all__ = ["run_client"]

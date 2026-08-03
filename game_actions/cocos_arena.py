"""競技場 web_h5 UI driver；正常路徑不截圖、不呼叫 OCR。"""
from __future__ import annotations

from typing import Any, Optional

from utils.cocos_ui import CocosUI


class CocosArena:
    def __init__(self, page: Any) -> None:
        self.ui = CocosUI(page)

    def enter(self) -> bool:
        if not self.ui.click_text("競技場"):
            return False
        if not self.ui.wait_for_text(("挑戰",), timeout=8):
            return False
        if not self.ui.click_text("挑戰"):
            return False
        return bool(self.ui.wait_for_text(("刷新", "記錄", "挑戰"), timeout=8))

    def challenge(self) -> bool:
        if not self.ui.click_text("挑戰"):
            return False
        return bool(self.ui.wait_for_text(("跳過", "勝利", "對決"), timeout=12))

    def wait_result(self, timeout: float = 60.0) -> Optional[str]:
        result = self.ui.wait_for_text(("勝利", "對決", "失敗", "跳過"), timeout=timeout)
        if result == "跳過":
            self.ui.click_text("跳過")
            return self.ui.wait_for_text(("勝利", "對決", "失敗"), timeout=timeout)
        return result

    def finish(self) -> None:
        if self.ui.has_text("刷新"):
            self.ui.click_text("刷新")
        if self.ui.has_text("記錄"):
            self.ui.click_text("記錄")

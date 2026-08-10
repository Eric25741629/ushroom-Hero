"""競技場 web_h5 UI driver；正常路徑不截圖、不呼叫 OCR。"""
from __future__ import annotations

import time
from typing import Any, Optional

from utils.cocos_ui import CocosUI


_ARENA_RESULT_MASK_PATH = "/UIRoot/NormalView/PvpResultView/imgMask"


class CocosArena:
    def __init__(self, page: Any) -> None:
        self.ui = CocosUI(page)

    def enter(self) -> bool:
        if not self.ui.click_text("競技場"):
            return False
        if not self.ui.wait_for_text(("挑戰",), timeout=8):
            return False
        # PvpMainView 只有一個入口挑戰按鈕；occurrence=1 會把它誤判成
        # 不存在，讓已成功進場的 Cocos 流程錯誤退回 OCR fallback。
        if not self.ui.click_text("挑戰", occurrence=0):
            return False
        return bool(self.ui.wait_for_text(("刷新", "記錄", "挑戰"), timeout=8))

    def challenge(self, occurrence: int = 1) -> bool:
        if not self.ui.click_text("挑戰", occurrence=occurrence):
            return False
        return bool(self.ui.wait_for_text(("跳過", "勝利", "對決", "失敗"), timeout=12))

    def wait_result(self, timeout: float = 60.0) -> Optional[str]:
        result = self.ui.wait_for_text(("勝利", "對決", "失敗", "跳過"), timeout=timeout)
        if result == "跳過":
            self.ui.click_text("跳過")
            return self.ui.wait_for_text(("勝利", "對決", "失敗"), timeout=timeout)
        return result

    def finish(self) -> bool:
        """關閉結算/競技場 overlay，並確認最後回到主頁。

        「記錄」是競技場內的功能按鈕，不是離場按鈕；直接點擊會把
        對戰記錄彈窗留給下一個任務。共用 navigator 會依 active view 的
        ``btnClose``/``btnBack`` 節點逐層關閉，再驗證主頁狀態。
        """
        try:
            from utils.cocos_navigator import CocosNavigator

            navigator = CocosNavigator(self.ui.page)
            navigator.dismiss_blocking_popups()
            # PvpResultView 的「點擊任意位置關閉」不是 btnClose/btnBack，
            # 共用 navigator 的泛用 close 掃描找不到它；先點掉遮罩再離場。
            if navigator.current_view() == "unknown":
                navigator._click_path(_ARENA_RESULT_MASK_PATH)
                time.sleep(0.5)
            return bool(navigator.goto_main())
        except Exception:
            return False

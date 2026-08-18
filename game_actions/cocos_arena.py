"""競技場 web_h5 UI driver；正常路徑不截圖、不呼叫 OCR。"""
from __future__ import annotations

import time
from typing import Any, Optional

from utils.cocos_ui import CocosUI


_ARENA_RESULT_MASK_PATH = "/UIRoot/NormalView/PvpResultView/imgMask"
_ARENA_RESULT_TEXTS = (
    "勝利",
    "战斗胜利",
    "胜利",
    "對決",
    "对决",
    "失敗",
    "战斗失败",
    "失败",
)
_ARENA_SKIP_TEXTS = ("跳過", "跳过")


def _normalise_result(text: Optional[str]) -> Optional[str]:
    """將遊戲簡繁結果文案統一成流程使用的繁體值。"""
    if not text:
        return None
    if "勝利" in text or "胜利" in text:
        return "勝利"
    if "對決" in text or "对决" in text:
        return "對決"
    if "失敗" in text or "失败" in text:
        return "失敗"
    return text


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
        return bool(
            self.ui.wait_for_text(_ARENA_SKIP_TEXTS + _ARENA_RESULT_TEXTS, timeout=12)
        )

    def wait_result(self, timeout: float = 60.0) -> Optional[str]:
        result = self.ui.wait_for_text(
            _ARENA_RESULT_TEXTS + _ARENA_SKIP_TEXTS, timeout=timeout
        )
        if result in _ARENA_SKIP_TEXTS:
            self.ui.click_text(result)
            result = self.ui.wait_for_text(_ARENA_RESULT_TEXTS, timeout=timeout)
        return _normalise_result(result)

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

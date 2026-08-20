"""競技場 web_h5 UI driver；正常路徑不截圖、不呼叫 OCR。"""
from __future__ import annotations

import time
from typing import Any, Optional

from utils.cocos_ui import CocosUI


_ARENA_RESULT_MASK_PATH = "/UIRoot/NormalView/PvpResultView/imgMask"
_PARKING_REWARD_MASK_PATH = "/UIRoot/NormalView/ParkingWareHouseView/root/imgMask"
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
_ARENA_CHALLENGE_CLOSE_PATH = "/UIRoot/NormalView/PvpChalleneView/content/btnClose"
_ARENA_MAIN_CLOSE_PATH = "/UIRoot/NormalView/PvpMainView/content/btnClose"


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
        self._completed_without_result_popup = False

    def enter(self) -> bool:
        if not self.ui.click_text("競技場", root="MainView"):
            return False
        if not self.ui.wait_for_text(("挑戰",), root="PvpMainView", timeout=8):
            return False
        # PvpMainView 只有一個入口挑戰按鈕；限定 root 並使用 occurrence=0。
        if not self.ui.click_text("挑戰", root="PvpMainView", occurrence=0):
            return False
        # 刷新按鈕先於對手列建立；必須等到清單內的挑戰按鈕真的掛載，
        # 才能進入下一步，避免 live race 把尚未完成的 view 當成可操作。
        return bool(
            self.ui.wait_for_text(("挑戰",), root="PvpChalleneView", timeout=8)
        )

    def challenge(self, occurrence: int = 0) -> bool:
        # 挑戰清單是 PvpChalleneView；限定 root 後 occurrence=0 才是第一個
        # 對手，避免把 PvpMainView 的入口按鈕或其他清單項目算進來。
        before = self.ui.snapshot(root="PvpChalleneView")
        if not self.ui.click_text(
            "挑戰", root="PvpChalleneView", occurrence=occurrence
        ):
            return False
        result = self.ui.wait_for_text(
            _ARENA_SKIP_TEXTS + _ARENA_RESULT_TEXTS, timeout=12
        )
        if result:
            return True

        # 部分帳號的競技場不顯示結果 Label，而是直接回到挑戰清單並刷新
        # 對手／分數；清單內容變化就是這條 H5 流程的完成訊號。
        after = self.ui.snapshot(root="PvpChalleneView")
        if after != before and after.get("texts"):
            self._completed_without_result_popup = True
            return True
        return False

    def wait_result(self, timeout: float = 60.0) -> Optional[str]:
        if self._completed_without_result_popup:
            self._completed_without_result_popup = False
            return "對決"
        result = self.ui.wait_for_text(
            _ARENA_RESULT_TEXTS + _ARENA_SKIP_TEXTS, timeout=timeout
        )
        if result in _ARENA_SKIP_TEXTS:
            # BattleHubView 的跳過不是普通文字按鈕；用 live 確認的 node
            # path 觸發，文字 click 僅作 Cocos 層 fallback（仍非 OCR）。
            if not self.ui.click_node("btnExit", root="BattleHubView"):
                self.ui.click_text(result)
            result = self.ui.wait_for_text(_ARENA_RESULT_TEXTS, timeout=timeout)
        return _normalise_result(result)

    def battle_in_progress(self) -> bool:
        """以 BattleHubView Cocos Label 判斷競技場動畫是否仍在進行。"""
        for text in ("正在挑戰", "與對手激烈搏鬥！", "与对手激烈搏斗！"):
            if self.ui.has_text(text, root="BattleHubView"):
                return True
        return False

    def finish(self) -> bool:
        """關閉結算/競技場 overlay，並確認最後回到主頁。

        「記錄」是競技場內的功能按鈕，不是離場按鈕；直接點擊會把
        對戰記錄彈窗留給下一個任務。共用 navigator 會依 active view 的
        ``btnClose``/``btnBack`` 節點逐層關閉，再驗證主頁狀態。
        """
        try:
            from utils.cocos_navigator import CocosNavigator

            if self.battle_in_progress():
                # 結果未回來時不能關掉競技場 overlay 假裝已收尾；讓上層
                # 收到 False，保留現場供下一輪診斷／遊戲端自行完成。
                return False

            navigator = CocosNavigator(self.ui.page)
            # 定時自動收車可能在戰鬥期間彈出車位獎勵；它沒有 btnClose，
            # 必須先點外層 imgMask，否則會擋住競技場專屬收尾。
            navigator._click_path(_PARKING_REWARD_MASK_PATH)
            time.sleep(0.3)
            navigator.dismiss_blocking_popups()
            # PvpResultView 的「點擊任意位置關閉」不是 btnClose/btnBack，
            # 共用 navigator 的泛用 close 掃描找不到它；先點掉遮罩再離場。
            if navigator.current_view() == "unknown":
                navigator._click_path(_ARENA_RESULT_MASK_PATH)
                time.sleep(0.5)
                # 競技場本身不是共用 page detector 的 overlay，需用其
                # 已確認的專屬關閉路徑逐層退出。
                navigator._click_path(_ARENA_CHALLENGE_CLOSE_PATH)
                time.sleep(0.5)
                navigator._click_path(_ARENA_MAIN_CLOSE_PATH)
                time.sleep(0.5)
            return bool(navigator.goto_main())
        except Exception:
            return False

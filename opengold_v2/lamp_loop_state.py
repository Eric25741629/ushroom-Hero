"""神燈批次開啟迴圈的純邏輯狀態機。

負責根據每次讀到的剩餘數量 + 是否偵測到裝備彈窗，決定下一個動作。
不做任何 UI / 截圖操作，方便 unit test。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class LampLoopAction(Enum):
    """主迴圈下一步動作。"""

    WAIT = "wait"                  # 還在動畫中或未到穩定門檻，繼續觀察
    HANDLE_POPUP = "handle_popup"  # 穩定 + 有裝備彈窗 → 點擊處理
    REENGAGE_AUTO = "reengage_auto"  # 穩定但無彈窗 → 重新啟用自動模式
    RENAVIGATE = "renavigate"      # OCR 長時間讀不到 → 重新導航到神燈頁


class LampLoopState:
    """剩餘數量穩定偵測 + 動作決策。

    參數說明：
    - stable_threshold: 連續多少次相同數值才視為「真的停下」
    - reengage_after_stable: 穩定且無彈窗達此次數時 → 重啟自動模式
    - unreadable_renav_threshold: 連續 OCR 讀不到達此次數 → 重新導航
    """

    def __init__(
        self,
        stable_threshold: int = 2,
        reengage_after_stable: int = 5,
        unreadable_renav_threshold: int = 8,
    ) -> None:
        self.stable_threshold = stable_threshold
        self.reengage_after_stable = reengage_after_stable
        self.unreadable_renav_threshold = unreadable_renav_threshold

        self.last_lamp_count: Optional[int] = None
        self.stable_streak: int = 0
        self.unreadable_streak: int = 0

    def tick(self, lamp_count: Optional[int], has_popup: bool) -> LampLoopAction:
        """送入新的觀察值，回傳此輪該採取的動作。"""
        # OCR 讀不到（被動畫遮住）→ 仍在動畫中，不計穩定
        if lamp_count is None:
            self.stable_streak = 0
            self.unreadable_streak += 1
            if self.unreadable_streak >= self.unreadable_renav_threshold:
                self.unreadable_streak = 0
                return LampLoopAction.RENAVIGATE
            return LampLoopAction.WAIT

        # 有讀到值，重置 unreadable 計數
        self.unreadable_streak = 0

        # 第一次讀到值 → 只記 baseline
        if self.last_lamp_count is None:
            self.last_lamp_count = lamp_count
            return LampLoopAction.WAIT

        # 數字下降 → 開神燈動畫中（每批 -20）
        if lamp_count < self.last_lamp_count:
            self.last_lamp_count = lamp_count
            self.stable_streak = 0
            return LampLoopAction.WAIT

        # 數字上升 → 魔法熔爐被動生成；非動畫，更新 baseline 但不重置 streak
        if lamp_count > self.last_lamp_count:
            self.last_lamp_count = lamp_count
            self.stable_streak += 1
            if self.stable_streak < self.stable_threshold:
                return LampLoopAction.WAIT
            if has_popup:
                self.stable_streak = 0
                return LampLoopAction.HANDLE_POPUP
            if self.stable_streak >= self.reengage_after_stable:
                self.stable_streak = 0
                return LampLoopAction.REENGAGE_AUTO
            return LampLoopAction.WAIT

        # 數字相同
        self.stable_streak += 1

        # 還沒達到穩定門檻
        if self.stable_streak < self.stable_threshold:
            return LampLoopAction.WAIT

        # 穩定 + 有彈窗 → 處理裝備
        if has_popup:
            self.stable_streak = 0
            return LampLoopAction.HANDLE_POPUP

        # 穩定但沒有彈窗，達到重啟門檻 → 重啟自動
        if self.stable_streak >= self.reengage_after_stable:
            self.stable_streak = 0
            return LampLoopAction.REENGAGE_AUTO

        return LampLoopAction.WAIT

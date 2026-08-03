"""雲纏天梯 web_h5 driver；正常路徑只讀 Cocos node/Label。"""
from __future__ import annotations

import time
from typing import Any, Optional

from utils.cocos_ui import CocosUI
from utils.cocos_view import open_view


VIEW = "DoubleChapterMainView"
PASS_TEXTS = ("已通過最高難度", "已通过最高难度")


class CocosCloudBattle:
    def __init__(self, page: Any) -> None:
        self.page = page
        self.ui = CocosUI(page)

    def enter(self) -> bool:
        if not open_view(self.page, VIEW):
            return False
        # 有未領的上週結算時先領；沒有此文字不是錯誤。
        if self.ui.has_text("結算獎勵", root=VIEW):
            self.ui.click_text("結算獎勵", root=VIEW)
            self.ui.wait_until_text_gone("結算獎勵", root=VIEW, timeout=5)
        return VIEW in (self.ui.snapshot(VIEW).get("views") or [])

    def is_passed(self) -> Optional[bool]:
        snapshot = self.ui.snapshot(VIEW)
        if snapshot.get("err"):
            return None
        values = snapshot.get("texts") or []
        return any(key in value for key in PASS_TEXTS for value in values)

    def friend_help(self, name: str = "大車輪") -> bool:
        if not self.enter() or not self.ui.click_text("戰友設置", root=VIEW):
            return False
        if not self.ui.wait_for_text(("戰友招募",), root=VIEW, timeout=5):
            return False
        if not self.ui.click_text("戰友招募", root=VIEW):
            return False
        if not self.ui.wait_for_text((name,), root=VIEW, timeout=5):
            return False
        if not self.ui.click_text(name, root=VIEW, exact=True):
            return False
        if not self.ui.click_text("發送", root=VIEW):
            return False
        self._close_visible()
        return True

    def help_friend(self) -> bool:
        if not self.enter() or not self.ui.click_text("助戰設置", root=VIEW):
            return False
        if not self.ui.wait_for_text(("新申請", "關閉"), root=VIEW, timeout=5):
            return False
        for _ in range(20):
            if not self.ui.has_text("新申請", root=VIEW):
                break
            if not self.ui.click_text("新申請", root=VIEW):
                return False
            if not self.ui.wait_for_text(("同意",), root=VIEW, timeout=3):
                return False
            if not self.ui.click_text("同意", root=VIEW):
                return False
            time.sleep(0.2)
        self._close_visible()
        return True

    def cloud_fighting(self, max_entries: int = 5, timeout: float = 300.0) -> bool:
        if not self.enter():
            return False
        passed = self.is_passed()
        if passed is True:
            self._close_visible()
            return True
        if passed is None:
            return False

        if not self.ui.click_text("戰友設置", root=VIEW):
            return False
        if not self.ui.wait_for_text(("選擇",), root=VIEW, timeout=5):
            return False
        if not self.ui.click_text("選擇", root=VIEW):
            return False
        if not self.ui.click_text("副本入場", root=VIEW):
            return False

        for _ in range(max_entries):
            passed = self.is_passed()
            if passed is True:
                self._close_visible()
                return True
            if passed is None or not self.ui.click_text("入場", root=VIEW):
                return False
            if not self.ui.wait_for_text(("前往挑戰", "已通過最高難度"), root=VIEW, timeout=8):
                return False

            while self.ui.has_text("前往挑戰", root=VIEW):
                if not self.ui.click_text("前往挑戰", root=VIEW):
                    return False
                if not self.ui.wait_for_text(("開始挑戰",), root=VIEW, timeout=5):
                    return False
                if not self.ui.click_text("開始挑戰", root=VIEW):
                    return False
                result = self.ui.wait_for_text(
                    ("挑戰成功", "挑戰失敗", "恭喜獲得"), root=VIEW, timeout=timeout
                )
                if result is None:
                    return False
                self.ui.click_text(result, root=VIEW)
                if result == "挑戰失敗":
                    self._close_visible()
                    return False

        passed = self.is_passed()
        self._close_visible()
        return passed is True

    def _close_visible(self) -> None:
        for text in ("關閉", "返回", "確定"):
            if self.ui.has_text(text, root=VIEW):
                self.ui.click_text(text, root=VIEW)

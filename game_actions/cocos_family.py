"""家族與雪國危機的 web_h5 Cocos 流程。"""
from __future__ import annotations

import time
from typing import Any

from utils.cocos_ui import CocosUI


class CocosFamily:
    def __init__(self, page: Any) -> None:
        self.ui = CocosUI(page)

    def donate_and_claim(self) -> bool:
        if not self.ui.click_text("家族大廳"):
            return False
        if not self.ui.wait_for_text(("捐獻", "家族商店"), timeout=8):
            return False
        if self.ui.has_text("捐獻"):
            self.ui.click_text("捐獻")
            # 優先使用遊戲提供的一鍵按鈕，沒有時再重複點擊可捐獻項目。
            if self.ui.has_text("一鍵捐獻"):
                self.ui.click_text("一鍵捐獻")
            else:
                for _ in range(10):
                    if not self.ui.click_text("捐獻"):
                        break
                    time.sleep(0.15)
        for _ in range(10):
            if self.ui.has_text("一鍵領取") and self.ui.click_text("一鍵領取"):
                continue
            if self.ui.has_text("領取") and self.ui.click_text("領取"):
                continue
            break
        self.close()
        return True

    def snow_country(self, max_wait: float = 180.0) -> bool:
        """以 Cocos label 驅動雪國危機；無法辨識時回 False 讓 ADB fallback。"""
        if not self.ui.click_text("雪國危機"):
            return False
        if not self.ui.wait_for_text(("入場",), timeout=8):
            return False
        if not self.ui.click_text("入場"):
            return False
        if not self.ui.wait_for_text(("前往組隊",), timeout=8):
            return False
        if not self.ui.click_text("前往組隊"):
            return False
        if not self.ui.wait_for_text(("速戰",), timeout=8):
            return False
        if not self.ui.click_text("速戰"):
            return False
        result = self.ui.wait_for_text(
            ("挑戰成功", "領取討伐獎勵", "恭喜獲得", "失敗"), timeout=max_wait
        )
        if result in ("挑戰成功", "領取討伐獎勵", "恭喜獲得"):
            self.ui.click_text(result)
            self.close()
            return True
        return False

    def close(self) -> None:
        for text in ("關閉", "返回", "確定"):
            if self.ui.has_text(text):
                self.ui.click_text(text)


def run_family_h5(page: Any, *, include_snow: bool = True) -> bool:
    driver = CocosFamily(page)
    if not driver.donate_and_claim():
        return False
    if include_snow:
        # 雪國不是每個活動週都開；沒有入口視為本輪成功，不退回 OCR。
        if driver.ui.has_text("雪國危機") and not driver.snow_country():
            return False
    return True

"""家族與雪國危機的 web_h5 Cocos 流程。

家族頁面的舊版流程是固定座標／OCR：先找「家族大廳」，再點固定位置。
H5 正式流程改用遊戲自己的 Cocos 節點，並以「剩餘次數下降」及官方
``GoodsGetView`` 獎勵視圖作為成功證據。ADB 呼叫端不會經過本模組。
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from utils.cocos_navigator import CocosNavigator
from utils.cocos_ui import CocosUI
from utils.cocos_view import close_view, is_open


_GUILD_TAB_PATH = "/UIRoot/NormalView/MainView/tab/scrollTab/view/content/6"
_GUILD_MAP_READY_VIEW = "GuildMapSceneView"
_GUILD_VIEW = "GuildView"
_GUILD_DONATE_VIEW = "GuildDonateView"
_REMAINING_RE = re.compile(r"^今日剩餘次數：\s*(\d+)$")


class CocosFamily:
    def __init__(self, page: Any) -> None:
        self.ui = CocosUI(page)
        self.nav = CocosNavigator(page)

    @property
    def page(self) -> Any:
        return self.ui.page

    def _wait_view(self, view_name: str, *, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if is_open(self.page, view_name):
                return True
            time.sleep(0.25)
        return False

    def _wait_view_closed(self, view_name: str, *, timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_open(self.page, view_name):
                return True
            time.sleep(0.2)
        return False

    def _open_guild_view(self) -> bool:
        """主頁 → 家族地圖 → 家族資訊，不使用文字或座標導航。"""
        if is_open(self.page, _GUILD_VIEW) or is_open(self.page, _GUILD_DONATE_VIEW):
            return True

        if not is_open(self.page, _GUILD_MAP_READY_VIEW):
            if not self.nav._click_path(_GUILD_TAB_PATH):
                return False
            if not self._wait_view(_GUILD_MAP_READY_VIEW, timeout=18.0):
                return False

        # GuildMapScene 的 btnInfo 才是官方開啟 GuildView 的入口；
        # GuildView 裡的「家族捐獻」按鈕再開 GuildDonateView。
        if not self.ui.click_node("btnInfo", root="GuildMapScene"):
            return False
        return self._wait_view(_GUILD_VIEW, timeout=12.0)

    def _open_donate_view(self) -> bool:
        if is_open(self.page, _GUILD_DONATE_VIEW):
            return True
        if not self._open_guild_view():
            return False
        if not self.ui.click_node("btnDonate", root=_GUILD_VIEW):
            return False
        return self._wait_donate_view_or_capped(timeout=12.0)

    def _wait_donate_view_or_capped(self, *, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if is_open(self.page, _GUILD_DONATE_VIEW):
                return True
            # 次數已用完時，官方只顯示提示，不建立 GuildDonateView。
            if self.ui.has_text("本日捐獻次數已滿"):
                return True
            time.sleep(0.25)
        return False

    def _remaining_donations(self) -> Optional[int]:
        for value in self.ui.texts(_GUILD_DONATE_VIEW):
            match = _REMAINING_RE.fullmatch(value.strip())
            if match:
                return int(match.group(1))
        return None

    def _donation_button_available(self) -> bool:
        # 「免費」是跨日後第一次捐獻；後續官方按鈕文字會變成「捐獻」。
        # 兩者都必須在 GuildDonateView 內出現，避免誤點其他頁面的同名文字。
        return bool(self.ui.has_any_text(("免費", "捐獻"), root=_GUILD_DONATE_VIEW))

    def _wait_donation_update(
        self, previous: int, *, timeout: float = 12.0
    ) -> tuple[Optional[int], bool]:
        """等待剩餘次數下降，並記錄是否看到了官方獎勵視圖。"""
        deadline = time.monotonic() + timeout
        reward_seen = False
        current: Optional[int] = None
        while time.monotonic() < deadline:
            reward_seen = reward_seen or is_open(self.page, "GoodsGetView")
            # 最後一次捐獻可能直接把 GuildDonateView 關掉，只留下官方
            # 的滿次數提示；這仍是明確的 Cocos 完成狀態。
            if self.ui.has_text("本日捐獻次數已滿"):
                return 0, reward_seen
            current = self._remaining_donations()
            if current is not None and current < previous:
                # 獎勵視圖可能比計數更新晚一小段時間，留一點時間讓它出現。
                reward_deadline = time.monotonic() + 4.0
                while time.monotonic() < reward_deadline:
                    reward_seen = reward_seen or is_open(self.page, "GoodsGetView")
                    if reward_seen:
                        break
                    time.sleep(0.2)
                return current, reward_seen
            time.sleep(0.25)
        return current, reward_seen

    def _claim_reward_popup(self) -> bool:
        """關閉官方獎勵視圖，確認獎勵流程已收尾。"""
        # 捐獻成功後，獎勵 view 與成功提示可能不同步建立；最多收三次，
        # 避免上一筆延遲的 GoodsGetView 把下一筆流程卡住。
        for _ in range(3):
            if not is_open(self.page, "GoodsGetView"):
                return True
            clicked = self.ui.click_node("Block", root="GoodsGetView")
            if not clicked:
                close_view(self.page, "GoodsGetView")
            if self._wait_view_closed("GoodsGetView", timeout=1.5):
                return True
            # 官方 UI manager 是第二個收尾途徑；仍然不使用座標或 OCR。
            close_view(self.page, "GoodsGetView")
            if self._wait_view_closed("GoodsGetView", timeout=1.5):
                return True
        return not is_open(self.page, "GoodsGetView")

    def donate_and_claim(self, max_donations: int = 10) -> bool:
        """完成今日可用的家族捐獻並收掉官方獎勵視圖。

        ``今日剩餘次數`` 是 server/UI 的完成訊號，因此不靠固定點擊次數
        判斷成功。若 Cocos 無法讀到該狀態，直接回 False，交由 H5 上層
        回報 unavailable；絕不退回 OCR。
        """
        if not self._open_donate_view():
            return False

        remaining = self._remaining_donations()
        if remaining is None and self.ui.has_text("本日捐獻次數已滿"):
            self.close()
            return True
        if remaining is None:
            return False
        if remaining <= 0:
            self.close()
            return True

        completed = 0
        for _ in range(max(1, int(max_donations))):
            if remaining <= 0:
                break
            if not self._donation_button_available():
                return False
            if not self.ui.click_node("btnDonate", root=_GUILD_DONATE_VIEW):
                return False

            updated, reward_seen = self._wait_donation_update(remaining)
            if reward_seen and not self._claim_reward_popup():
                return False
            if updated is None or updated >= remaining:
                return False
            remaining = updated
            completed += 1

        self.close()
        return completed > 0 or remaining <= 0

    def snow_country(self, max_wait: float = 180.0) -> bool:
        """以 Cocos label 驅動雪國危機；無法辨識時回 False。"""
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
        """只關閉家族流程自己開啟的 view，不掃描全域文字。"""
        if is_open(self.page, "GoodsGetView"):
            self._claim_reward_popup()
        for view_name in (_GUILD_DONATE_VIEW, _GUILD_VIEW, "GuildMapView", "GuildMainView"):
            if is_open(self.page, view_name):
                close_view(self.page, view_name)
                self._wait_view_closed(view_name, timeout=3.0)


def run_family_h5(page: Any, *, include_snow: bool = True) -> bool:
    driver = CocosFamily(page)
    if not driver.donate_and_claim():
        return False
    if include_snow:
        # 雪國不是每個活動週都開；沒有入口視為本輪成功，不退回 OCR。
        if driver.ui.has_text("雪國危機") and not driver.snow_country():
            return False
    return True

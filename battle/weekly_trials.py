"""萬神試煉 — 每週 7 場過關後購買秘寶閣物品。"""

import random
import time

import img_tools

from .store import buy_god_everyweek


def fight_test(d):
    img_tools.click_str_by_server(d, '副本')
    time.sleep(2)
    for _ in range(3):
        d.swipe(239, 752, 239, 352, 0.2)
    d.click(239, 752)
    time.sleep(2)
    if not img_tools.click_str_by_server(d, '萬神試煉', (426 - 149), (410 - 335)):
        # 進不了萬神試煉：此時遊戲停在副本列表頁（非主頁面）。若直接 return，
        # 後續任務會連鎖判為「不在主頁面」而中止整條 pipeline，故先導回主頁面。
        _return_home(d)
        return
    time.sleep(2)
    for i in range(7):
        img_tools.click_str_by_server(d, '開始')
        time.sleep(1.5 + random.uniform(0, 0.5))
        img_tools.click_str_by_server(d, '開始')
        time.sleep(1 + random.uniform(0, 0.5))
        img_tools.click_str_by_server(d, '確定')
        time.sleep(1.5)
        d.click(446, 81)
        img_tools.click_str_by_server(d, '開始挑戰')
        time.sleep(7)
        img_tools.click_str_by_server(d, '跳過')
        time.sleep(2)
        d.click(446, 81)
        time.sleep(2)
        d.click(490, 919)  # 點擊退出
        time.sleep(1)
        img_tools.click_str_by_server(d, '結束本局', 170 - 339, 521 - 433)
        time.sleep(1)
        img_tools.click_str_by_server(d, '確定')
        time.sleep(2)
        d.click(446, 81)
        time.sleep(0.5)
        d.click(274, 875)
        time.sleep(1)
    buy_god_everyweek(d)
    d.click(45, 262)
    time.sleep(1)
    img_tools.click_str_by_server(d, '本周積分', shift_y=90)
    time.sleep(0.2)
    for i in range(3):
        d.click(509, 56)
    time.sleep(2)
    d.click(490, 919)  # 點擊退出
    time.sleep(1)
    img_tools.click_str_by_server(d, '關閉')


def _return_home(d):
    """失敗/中止路徑統一導回主頁面（沿用 game_actions 既有導航 helper）。"""
    from game_actions.navigation import navigate_to_main_page
    navigate_to_main_page(d, label="weekly_trials")

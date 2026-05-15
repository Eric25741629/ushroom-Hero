import os
import sys
import time
import cv2
import random
import logging
import uiautomator2 as u2
import numpy as np

# 添加父目錄到系統路徑，以便導入上層模組
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import img_tools
import new_cnn.cnn_model as _cnn_module
from tools import click_white
from json_manager import create_time_manager
from utils.screenshot_helpers import save_error_screenshot
from game_state.detector import get_stage

logger = logging.getLogger(__name__)


def _predict_stage(Cnn_model, pil_img):
    """Wrap CNN module call so callers don't have to remember the awkward
    `module.predict_image(instance, image)` form. Returns class name or None
    on failure."""
    try:
        return _cnn_module.predict_image(Cnn_model, pil_img)
    except Exception as e:
        logger.debug(f"CNN predict failed: {e}")
        return None


def _exit_farm_to_main(d, ip, Cnn_model, save_time, *, timeout=60):
    """Drive 農場 → 家園 → 主頁面.

    OCR-based — CNN 對「農場」/「家園」沒有可靠類別，且呼叫慣例脆弱。
    每輪：OCR get_stage → 主頁面就結束；否則
      1) 點右下 (480, 929) — 純農場頁的「返回」鈕
      2) OCR 找「關閉」處理彈窗（公告 / 商店 / 確認框）
      3) 點 home (321, 920) — 在家園可回主頁面
    Returns updated save_time.
    """
    exit_start = time.time()
    last_stage = "__init__"
    attempt = 0
    reached_main = False

    while time.time() - exit_start < timeout:
        attempt += 1
        try:
            stage = get_stage(d, Cnn_model)
        except Exception as e:
            logger.debug(f"[farm] OCR get_stage 失敗 #{attempt}: {e}")
            stage = None

        if stage != last_stage:
            elapsed = time.time() - exit_start
            logger.info(f"[farm] 退出嘗試 #{attempt}, OCR={stage}, elapsed={elapsed:.1f}s")
            try:
                save_error_screenshot(d, ip, str(stage), f"farm_exit_attempt_{attempt}")
            except Exception as e:
                logger.debug(f"[farm] 截圖保存失敗: {e}")
            last_stage = stage

        if stage == "主頁面":
            elapsed = time.time() - exit_start
            save_time += max(0, 3 - elapsed)
            logger.info(f"[farm] 已回到主頁面 (OCR)，耗時 {elapsed:.1f}s")
            reached_main = True
            break

        # 放置獎勵 (offline reward) popup has no 「關閉」 — its dismiss button is
        # 「領取」 at (~330, 725). Captured on 7fe98fc6 2026-05-02 14:32: the
        # generic three-step exit clicked (480, 929) which is the bottom-right
        # 商店 tab and never hit 領取, looping until 60s timeout. Use the
        # existing reward handler which knows the (162,725)+(330,725) ritual.
        if stage == "放置獎勵":
            try:
                from game_actions.reward_manager import reward as _reward
                logger.info(f"[farm] 偵測到「放置獎勵」popup，呼叫 reward() 處理")
                _reward(d)
                time.sleep(2)
                continue
            except Exception as e:
                logger.warning(f"[farm] reward() 失敗，退回通用步驟: {e}")

        d.click(480 + random.randint(-5, 5), 929 + random.randint(-3, 3))
        time.sleep(1.5)

        if img_tools.click_str_by_server(d, "關閉", wait_timeout=0):
            logger.info(f"[farm] OCR 找到「關閉」並點擊 (stage={stage})")
            time.sleep(1.5)
            continue

        d.click(321 + random.randint(-5, 5), 920 + random.randint(-3, 3))
        time.sleep(2)

    if not reached_main:
        logger.warning(f"[farm] 退出超時 {timeout}s，最後 stage={last_stage}，啟動 fallback resolver")
        try:
            save_error_screenshot(d, ip, str(last_stage), "farm_exit_timeout")
        except Exception as e:
            logger.debug(f"[farm] 超時截圖保存失敗: {e}")
        try:
            from game_initialization import resolve_stage_until_stable
            from game_actions.reward_manager import reward as _reward_fn
            final = resolve_stage_until_stable(
                d, ip, Cnn_model=Cnn_model, reward_fn=_reward_fn, logger=logger
            )
            logger.warning(f"[farm] fallback resolver 後 stage={final}")
        except Exception as e:
            logger.error(f"[farm] fallback resolver 失敗: {e}，改用 click_white")
            for _ in range(3):
                click_white(d)
                time.sleep(1)

    return save_time


def _wait_for_homeplace(d, Cnn_model, *, timeout=60, max_save=5.0):
    """Click 家園 tab 後等 CNN 確認到達 homeplace。返回節省的時間（最多 max_save 秒）。"""
    start = time.time()
    while time.time() - start < timeout:
        if _predict_stage(Cnn_model, d.screenshot(format='pillow')) == "homeplace":
            elapsed = time.time() - start
            saved = max(0.0, max_save - elapsed)
            logger.info(f"save time {saved:.2f}")
            return saved
    return 0.0
def check_if_partime(d):
    try:
        img = d.screenshot(format='opencv')
        print((np.sum(img[710,211]) -np.sum([57, 65, 196])) )

        if np.sum(img[713,339]) -np.sum([52, 64, 200]) <10  and (abs(np.sum(img[710,211]) -np.sum([57, 65, 196])) <10) :
            print("打工")
            return True
        else:
            print('未打工')
            return False
    except Exception as e:
        print(e)
def buy_seed(d: u2.Device):
    """購買種子"""
    d.click(375, 915)
    time.sleep(2)
    d.click(160, 428)
    time.sleep(0.5)
    for i in range(3):
        d.click(335+random.randint(-5, 5), 465+random.randint(-5, 5))
    time.sleep(0.5)
    d.click(273, 558)
    time.sleep(0.5)
    d.click(475, 800)
    time.sleep(0.5)
    d.click(384, 428)
    time.sleep(0.5)
    for i in range(3):
        d.click(335+random.randint(-5, 5), 465+random.randint(-5, 5))
    time.sleep(0.5)
    d.click(273, 558)
    time.sleep(0.5)
    d.click(475, 800)
    click_white(d)
    click_white(d)
    d.click(379, 917)
    time.sleep(3)

def farm_card(d: u2.Device):
    d.click(479,207) #點擊打工按鈕
    time.sleep(2)
    parttime = check_if_partime(d)
    if parttime:
        d.click(252,707) #點擊取消打工
        time.sleep(random.random()+2)
    d.click(272,868) #點擊關閉
    time.sleep(2)
    if img_tools.find_and_click(d, r'fertilize.jpg'): #施肥
        time.sleep(2)
        d.click(362,430)
        time.sleep(2)
        d.click(270,600)
        time.sleep(3)
    """農場卡片相關操作 - 當星期一、三、五的時候執行"""
    if img_tools.find_and_click(d, r'getting.jpg'):
        time.sleep(7)
    if img_tools.find_and_click(d, r'get_all.jpg'):
        time.sleep(3)
    d.click(480, 929)
    time.sleep(3)
    # 前往車廠 進行購買
    d.click(470, 446)
    time.sleep(3)
    d.click(380, 910)
    time.sleep(2)
    start_time = time.time()
    want_to_buy = cv2.imread("farm_card.jpg")
    err = 0
    while (time.time() - start_time < 300):  # 限時 5 分鐘
        img = d.screenshot(format='opencv')
        res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        h, w = want_to_buy.shape[:-1]
        top_left = max_loc
        if max_val > 0.7 and top_left[1] < 552:
            d.click(
                top_left[0] + w//2, top_left[1] + h//2 + 100)
            time.sleep(2)
            d.click(398,466)
            time.sleep(1.5)
            d.click(277, 551)
            time.sleep(2)
            d.click(514, 16)  # 空白处
            time.sleep(2)
            break
        else:
            d.swipe(0.5, 0.8, 0.5, 0.6, 1)
            d.click(273, 773)
            time.sleep(1)
            err += 1
            if err > 10:
                break
    d.click(380, 926)
    time.sleep(1.5)
    d.click(471, 915)
    time.sleep(3)
    d.click(210, 690)
    time.sleep(3)
    # 前往農場連續種植30次
    current = 0
    start = time.time()
    while (current < 15 and time.time()-start < 600):
        if not img_tools.find_and_click(d, r'plants.jpg'):
            continue
        time.sleep(1.2)
        d.click(357, 442)
        time.sleep(1.2)
        d.click(267, 564)
        time.sleep(6)
        d.click(73+random.randint(-5, 5), 823+random.randint(-5, 5))  # 施肥
        time.sleep(2)
        d.click(365+random.randint(-5, 5), 440+random.randint(-5, 5))  # 確認肥料
        time.sleep(2)
        d.click(276+random.randint(-5, 5), 606+random.randint(-5, 5))  # 使用
        time.sleep(2.2)
        d.click(73, 823)  # getting
        time.sleep(8)
        d.click(73, 823)  # get all
        time.sleep(2.2)
        current += 1
    if parttime:
        d.click(479,207)    #點擊打工按鈕
        time.sleep(1)
        d.click(252,707)  #點擊打工
        time.sleep(random.random())
        d.click(272,868)  #點擊關閉
        time.sleep(1)
PLANT_DAILY_LIMIT = 2
PLANT_LOOP_TIMEOUT = 25
FERTILIZER_PIXEL_TARGET = sum([173, 112, 68])
FERTILIZER_PIXEL_TOLERANCE = 10
FERTILIZER_PIXEL_AT = (231, 437)  # (x, y) — img[y, x] sample point


def _seed_purchase_due(time_manager):
    record = time_manager.get_time_record("farm_seed_purchase")
    return not record or record.get("is_next_day", True)


def _plant_count_today(time_manager):
    if not time_manager.is_same_day("farm_plant_click"):
        return 0
    return time_manager.get_numeric_value("farm_plant_click", "count", 0)


def _maybe_collect_rewards(d) -> bool:
    """Try the three reward-collection templates. Sleeps appropriately and
    returns True if any matched."""
    if img_tools.find_and_click(d, r'getting.jpg'):
        time.sleep(7); return True
    if img_tools.find_and_click(d, r'get_all.jpg'):
        time.sleep(3); return True
    if img_tools.find_and_click(d, "new_get.jpg", threshold=0.6, x=10, y=100):
        time.sleep(7); return True
    return False


def _maybe_apply_fertilizer(d) -> None:
    """If the fertilizer indicator pixel is the expected colour, run the
    3-click fertilizer sequence."""
    img = d.screenshot(format='opencv')
    sx, sy = FERTILIZER_PIXEL_AT
    pixel_sum = sum(int(c) for c in img[sy, sx])
    if abs(pixel_sum - FERTILIZER_PIXEL_TARGET) > FERTILIZER_PIXEL_TOLERANCE:
        return
    d.click(199, 437); time.sleep(2)
    d.click(126, 588); time.sleep(1)
    d.click(165, 460); time.sleep(1)


def _run_plant_cycle(d, time_manager, *, hour, deadline) -> None:
    """Loop until deadline: each iter tries reward collection AND, if eligible,
    a plant click. Behaviour matches the original `elif` chain + fall-through."""
    while time.time() < deadline:
        _maybe_collect_rewards(d)

        if hour < 8 or _plant_count_today(time_manager) >= PLANT_DAILY_LIMIT:
            continue
        if not img_tools.find_and_click(d, r'plants.jpg'):
            continue

        new_count = _plant_count_today(time_manager) + 1
        time_manager.record_timestamp("farm_plant_click", {"count": new_count})
        time.sleep(2)
        _maybe_apply_fertilizer(d)
        if img_tools.find_and_click(d, r'put.jpg'):
            time.sleep(5)


def farm(d, ip, Cnn_model):
    """主程式：點家園 → 進農場 → 買種子 / 週卡 / 種植循環 → 退回主頁面。"""
    time_manager = create_time_manager(ip)

    if not _seed_purchase_due(time_manager) and _plant_count_today(time_manager) >= PLANT_DAILY_LIMIT:
        return 60

    d.click(321, 920)
    save_time = _wait_for_homeplace(d, Cnn_model)
    d.click(208, 584)
    time.sleep(5)

    if _seed_purchase_due(time_manager):
        buy_seed(d)
        time_manager.record_time("farm_seed_purchase")

    if not time_manager.is_same_week("farm_card_weekly"):
        farm_card(d)
        time_manager.record_time("farm_card_weekly")

    _run_plant_cycle(
        d, time_manager,
        hour=time.localtime().tm_hour,
        deadline=time.time() + PLANT_LOOP_TIMEOUT,
    )

    return _exit_farm_to_main(d, ip, Cnn_model, save_time)
if __name__ == "__main__":
    import  new_cnn.cnn_model as cnn_model
    # Cnn_model = cnn_model.load_cnn_model("cnn_model.pth")
    d = u2.connect('emulator-5554')  # 連接到指定設備
    farm_card(d)
    # save_time = farm_manager.farm(d, ip, Cnn_model)

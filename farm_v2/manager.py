"""農場自動化 v2 - 主管理器"""

from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import uiautomator2 as uiauto

import img_tools
import new_cnn.cnn_model as _cnn_module
from farm_v2.config import COORD, TIMING, MAX_PLANT_PER_DAY, FARM_VISIT_INTERVAL_HOURS
from farm_v2.operations import (
    click_with_jitter,
    wait_jitter,
    buy_seed,
    check_if_parttime,
    run_harvest_card,
    claim_ad_seeds,
)
from game_actions.navigation import navigate_to_main_page
from game_state.detector import get_stage
from utils.cocos_navigator import try_cocos_navigate
from utils.screenshot_helpers import save_error_screenshot

logger = logging.getLogger("farm_v2.manager")


def _h5_work_is_active(d: "uiauto.Device") -> Optional[bool]:
    page = getattr(d, "_page", None)
    if page is None or getattr(d, "backend_kind", None) != "web_h5":
        return None
    from utils.cocos_ui import CocosUI
    ui = CocosUI(page)
    if not ui.click_text("打工", root="PlantMainView"):
        return None
    state = ui.wait_for_text(("開始打工", "取消打工"), timeout=5)
    for text in ("關閉", "返回"):
        if ui.has_text(text):
            ui.click_text(text)
            break
    if state == "取消打工":
        return True
    if state == "開始打工":
        return False
    return None


def _ensure_work_active(d: "uiauto.Device") -> None:
    """Open work panel, check if 打工 is running. If not, start it."""
    page = getattr(d, "_page", None)
    if page is not None and getattr(d, "backend_kind", None) == "web_h5":
        from utils.cocos_ui import CocosUI
        ui = CocosUI(page)
        if not ui.click_text("打工", root="PlantMainView"):
            logger.warning("H5 找不到打工入口")
            return
        state = ui.wait_for_text(("開始打工", "取消打工"), timeout=5)
        if state == "開始打工":
            ui.click_text("開始打工")
            logger.info("H5 打工未啟動，已用 Cocos 開始打工")
        elif state == "取消打工":
            logger.info("H5 打工已在執行中")
        else:
            logger.warning("H5 無法確認打工狀態")
        for text in ("關閉", "返回"):
            if ui.has_text(text):
                ui.click_text(text)
                break
        return

    from farm_v2.config import COORD, TIMING
    from farm_v2.operations.base import click_with_jitter, wait_jitter

    click_with_jitter(d, COORD["work_button"][0], COORD["work_button"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))

    started = img_tools.wait_for_any_text(
        d, ["開始打工", "开始打工"],
        timeout=3, click_if_found=True,
    )
    if started:
        logger.info("打工未啟動，已自動開始打工")
        time.sleep(wait_jitter(TIMING["long"]))
        return

    cancel_visible = img_tools.wait_for_any_text(
        d, ["取消打工"],
        timeout=2, click_if_found=False,
    )
    if cancel_visible:
        logger.info("打工已在執行中")
    else:
        logger.warning("無法確認打工狀態")

    # close work panel via X button
    click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["medium"]))


def _predict_stage(cnn_model, pil_img):
    """Wrap the awkward `module.predict_image(instance, image)` call so callers
    don't trip on the fact that predict_image is a module-level function, not a
    method on SimpleCNN. Returns class name or None on failure."""
    try:
        return _cnn_module.predict_image(cnn_model, pil_img)
    except Exception as e:
        logger.debug(f"[farm_v2] CNN predict failed: {e}")
        return None


def navigate_to_farm(d: "uiauto.Device", cnn_model=None, device_ip: Optional[str] = None) -> float:
    """導航到農場頁面並返回節省的時間"""
    save_time = 0.0

    # Experimental fast-path: cocos emit-click bypasses screenshot+OCR loop.
    # Only fires when the device has experimental_cocos_navigation=true in
    # bot_config.json. None means "not applicable" (flag off / not web_h5) —
    # caller MUST fall back to the click-based logic below.
    if getattr(d, "backend_kind", None) == "web_h5" and getattr(d, "_page", None) is not None:
        try:
            from utils.cocos_navigator import CocosNavigator
            cocos_result = CocosNavigator(d._page).goto_farm()
        except Exception as exc:
            logger.warning(f"[farm_v2] H5 Cocos 導航例外: {exc}")
            cocos_result = False
    else:
        cocos_result = try_cocos_navigate(d, device_ip, "farm")
    if cocos_result is True:
        logger.info(f"[farm_v2] cocos fast-path succeeded for {device_ip}")
        # Saved roughly the full OCR wait + two animations (≈8s) vs blind clicks.
        return 6.0
    if cocos_result is False:
        logger.warning(f"[farm_v2] cocos fast-path failed for {device_ip}, falling back")

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    if cnn_model is not None:
        cnn_s = time.time()
        while time.time() - cnn_s <= 60:
            if _predict_stage(cnn_model, d.screenshot(format="pillow")) == "homeplace":
                saved = max(0.0, 5.0 - (time.time() - cnn_s))
                save_time += saved
                logger.info(f"節省時間: {saved:.2f}秒")
                break
        else:
            time.sleep(5)

    click_with_jitter(d, COORD["farm_entry"][0], COORD["farm_entry"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["farm_wait"]))

    return save_time


def navigate_to_home(
    d: "uiauto.Device",
    cnn_model=None,
    device_ip: Optional[str] = None,
) -> float:
    """從農場返回首頁。委託給 game_actions.navigation.navigate_to_main_page。

    Returns:
        節省時間秒數（若導航在 3 秒內完成則補回差額；否則 0.0）。
    """
    nav_start = time.time()
    navigate_to_main_page(d, cnn_model, device_ip, label="farm_v2")
    elapsed = time.time() - nav_start
    return max(0.0, 3.0 - elapsed)


def farm(
    d: "uiauto.Device",
    device_ip: str,
    cnn_model=None,
    time_manager=None,
) -> float:
    """執行完整農場流程

    Args:
        d: uiautomator2 Device 實例
        device_ip: 設備 IP
        cnn_model: CNN 模型（可選）
        time_manager: 時間記錄管理器（可選）

    Returns:
        節省的時間（秒）
    """
    from json_manager import create_time_manager
    import config_manager

    dev_cfg = config_manager.get_device_config(device_ip)
    # Config gate 1: 農場停用 → 連切頁都不做。farm() 是所有呼叫端的單一入口
    # (daily_pipeline / quick_farm)，在這裡擋掉比在 pipeline 擋更穩。
    if not dev_cfg.get("enable_farm", True):
        logger.info(f"[farm_v2] enable_farm=false，跳過農場 - {device_ip}")
        return 0.0

    if time_manager is None:
        time_manager = create_time_manager(device_ip)

    # Config gate 2: 進場節流(滑動視窗)。距上次進場未滿 8h 就不切頁。打工會自動
    # 用免費種子持續種+收，所以農場不必每小時進；每 8h 進一次就夠保活打工、補
    # 種子、收散落。買種子(每日)/豐收卡(每週) 由各自子任務內部判斷，8h<每日<每週
    # 所以該做的會在某次 8h 進場時自然輪到。farm_visit 在本輪結束前記一筆。
    if not time_manager.is_expired("farm_visit", FARM_VISIT_INTERVAL_HOURS * 3600):
        logger.info(f"[farm_v2] 距上次進場未滿 {FARM_VISIT_INTERVAL_HOURS}h，略過 - {device_ip}")
        return 0.0

    logger.info(f"開始農場流程 - 設備: {device_ip}")
    save_time = 0.0

    save_time += navigate_to_farm(d, cnn_model, device_ip=device_ip)

    # Read the *current* work state before changing anything. 打工 auto-plants
    # AND auto-harvests but never buys seeds, so we run the manual steps (seed
    # restock, harvest card, leftover collection) first and only (re-)enable 打工
    # at the very end. Enabling it up front (the old order) forced the harvest
    # card flow to cancel it again, and made `is_working` always True — which
    # silently gated out seed buying so 打工 eventually ran out of seeds.
    h5_working = _h5_work_is_active(d)
    is_working = check_if_parttime(d) if h5_working is None else h5_working
    if is_working:
        logger.info("打工中，種植交給打工，本輪只補種子/收散落獎勵")

    # Sub-task cadence is independent of the 8h visit throttle: seeds are bought
    # at most once per day, the harvest card at most once per week.
    seed_record = time_manager.get_time_record("farm_seed_purchase")
    should_buy_seed = not seed_record or seed_record.get("is_next_day", True)
    # 每台豐收卡開關：本週次數用完的帳號(手機/小寶)關掉,避免每次 8h 進場都白跑
    # 一輪買卡(達上限會失敗、不記錄、下次又重試)。關掉只停豐收卡,農場其餘照跑。
    harvest_card_enabled = dev_cfg.get("enable_harvest_card", True)
    should_run_card = harvest_card_enabled and not time_manager.is_same_week("farm_harvest_card")

    # Restock seeds regardless of work state — 打工 consumes them but won't buy
    # them, so the old `and not is_working` guard meant seeds were never bought
    # while 打工 ran and planting eventually stalled.
    if should_buy_seed:
        logger.info("需要購買種子")
        buy_seed(d)
        time_manager.record_time("farm_seed_purchase")

    # 看廣告補初級種子（免廣告卡=直接發、8 點後、每日上限 2 次、看過不再看）。
    # 打工會自動把補到的種子種掉，所以這裡只領+關窗，不手動種。
    # remove-after-ws-farm-verify(2026-06-19): 由 ws_token ad_reward(15) 取代,WS 驗證後刪此呼叫。
    claim_ad_seeds(d, device_ip, time_manager)

    # Weekly harvest card flow (replaces old Mon/Wed/Fri weekly_card)
    # remove-after-ws-farm-verify(2026-06-19): 由 ws_token.farm.run_harvest_card_cycle 取代,
    # WS 驗證後刪此整段 should_run_card 區塊(H5 設備);ADB 設備若無 WS farm 設定則保留。
    # 每台一行追蹤 log:方便跨裝置 grep 豐收卡每週決策(執行/已執行略過/停用略過)。
    if should_run_card:
        logger.info(f"[harvest_card] 本週尚未執行 → 執行每週豐收卡流程 - {device_ip}")
        if run_harvest_card(d, device_ip=device_ip, cnn_model=cnn_model):
            time_manager.record_time("farm_harvest_card")
            logger.info(f"[harvest_card] 本週豐收卡完成,已記錄 - {device_ip}")
        else:
            logger.warning(f"[harvest_card] 豐收卡流程失敗,下次醒來重試 - {device_ip}")
    elif not harvest_card_enabled:
        logger.info(f"[harvest_card] enable_harvest_card=false,本週豐收卡略過 - {device_ip}")
    else:
        logger.info(f"[harvest_card] 本週已執行豐收卡,略過 - {device_ip}")

    page = getattr(d, "_page", None)
    if page is not None and getattr(d, "backend_kind", None) == "web_h5":
        from farm_v2 import web_farm
        # H5 直接讀 PlantMainView 的按鈕 active 狀態；不跑模板/CNN/OCR。
        for _ in range(8):
            state = web_farm.read_farm_state(page)
            acted = False
            for name in ("btnOneKeyPick", "btnOneKeyFetch"):
                if web_farm.tap_onekey(page, name, state=state):
                    acted = True
                    time.sleep(1.0)
                    break
            if not acted:
                break
    else:
        start = time.time()
        while time.time() - start < 25:
            from img_tools import find_and_click

            if find_and_click(d, r"getting.jpg"):
                time.sleep(7)
            elif find_and_click(d, r"get_all.jpg"):
                time.sleep(3)
            elif find_and_click(d, "new_get.jpg", threshold=0.6, x=10, y=100):
                time.sleep(7)

            current_hour = time.localtime().tm_hour
            if current_hour >= 8 and not is_working:
                is_same_day = time_manager.is_same_day("farm_plant_click")
                daily_count = (
                    time_manager.get_numeric_value("farm_plant_click", "count", 0)
                    if is_same_day
                    else 0
                )

                if daily_count < MAX_PLANT_PER_DAY:
                    if find_and_click(d, r"plants.jpg"):
                        daily_count += 1
                        time_manager.record_timestamp(
                            "farm_plant_click", {"count": daily_count}
                        )
                        time.sleep(2)

                        from farm_v2.operations import check_slot_color

                        if check_slot_color(d):
                            d.click(199, 437)
                            time.sleep(2)
                            d.click(126, 588)
                            time.sleep(1)
                            d.click(165, 460)
                            time.sleep(1)

                        if find_and_click(d, r"put.jpg"):
                            time.sleep(5)

    # (Re-)enable 打工 last, so it keeps the auto plant/harvest cycle running
    # while the device sleeps. Done after seed restock + harvest card + leftover
    # collection so none of those steps fight an already-running worker (the
    # harvest card flow in particular needs 打工 off while it plants 特級種子).
    _ensure_work_active(d)

    save_time += navigate_to_home(d, cnn_model, device_ip=device_ip)

    # Stamp the visit so the 8h throttle trips for the next ~8h. Recorded on a
    # completed run only — a mid-run failure leaves no stamp so the next wake
    # retries instead of waiting a full window.
    time_manager.record_time("farm_visit")

    logger.info(f"農場流程完成，節省時間: {save_time:.2f}秒")
    return save_time


def quick_farm(device_ip: str) -> float:
    """快速執行農場（從 game_api 調用）"""
    import uiautomator2 as u2

    d = u2.connect(device_ip)
    return farm(d, device_ip)

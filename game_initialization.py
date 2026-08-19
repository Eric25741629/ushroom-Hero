"""
遊戲啟動後的頁面判斷與處理模組

功能：
- 處理遊戲啟動後的各種頁面（隱藏、獎勵、公告、購物管家等）
- 自動處理異常頁面（未知、異地登錄等）
- 等待遊戲進入主頁面或可操作的頁面
"""

import time
import random
import logging

import img_tools
from tools import click_white
from game_state.detector import get_stage
from game_actions.reward_manager import reward
from utils.logging_utils import logger
import bot_state
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    WakeLoopInterrupted,
)


class StartupLoginConflictError(Exception):
    """啟動流程中偵測到異地登錄。"""
    pass


def _honor_startup_controls(ip: str) -> None:
    """啟動迴圈中honor儀表板控制，讓使用者能中斷「啟動」階段。

    鏡像 utils.wake_up_handler._honor_dashboard_controls：
    - 暫停：check_pause() 阻塞直到恢復（迴圈凍結，bot 不再碰手機）。
    - 強制休眠：raise ForceSleepRequested，由主迴圈停止任務並進入對齊休眠。
    - 待處理的手動開網頁：raise WakeLoopInterrupted，由主迴圈回頂端處理（不休眠）。

    沒有這個檢查時，啟動迴圈會無視這些訊號、持續強制重開遊戲。
    """
    bot_state.check_pause(ip)
    if bot_state.check_force_sleep(ip):
        raise ForceSleepRequested(
            f"[{ip}] force sleep requested during game startup"
        )
    if bot_state.has_pending_web_launch_request(ip):
        raise WakeLoopInterrupted(
            f"[{ip}] web-launch request received during game startup"
        )


def _handle_known_stage_popup(d, ip: str, stage: str, reward_fn=None, logger: logging.Logger = None) -> bool:
    """Shared popup cleaner used by startup and runtime state checks."""
    if logger is None:
        logger = logging.getLogger(__name__)
    if reward_fn is None:
        reward_fn = reward

    if stage == "前往活動":
        logger.info(f"[{ip}] 偵測到前往活動彈窗，點擊空白關閉")
        click_white(d)
        time.sleep(1)
        return True

    if stage == "公告":
        logger.info(f"[{ip}] 偵測到公告彈窗，嘗試自動關閉")
        try:
            d.tap(248, 812)
        except Exception as e:
            logger.debug(f"[{ip}] 公告關閉點擊失敗，改用空白點擊: {e}")
        click_white(d)
        time.sleep(1)
        return True

    if stage in ("離線獎勵", "放置獎勵", "獎勵"):
        page = getattr(d, "_page", None)
        if getattr(d, "backend_kind", None) == "web_h5" and page is not None:
            logger.info(f"[{ip}] Cocos 偵測到 {stage}，直接領取離線獎勵")
            from game_actions.reward_manager import claim_open_reward

            if not claim_open_reward(page):
                logger.warning(f"[{ip}] Cocos 離線獎勵領取失敗，保留 stage 供上層有限重試")
                return False
        else:
            logger.info(f"[{ip}] 偵測到 {stage}，執行既有 reward()")
            reward_fn(d)
        time.sleep(1)
        return True

    if stage == "車位倉庫":
        logger.info(f"[{ip}] 偵測到車位倉庫，嘗試領取並關閉彈窗返回主頁")
        page = getattr(d, "_page", None)
        if getattr(d, "backend_kind", None) == "web_h5" and page is not None:
            # 已由 Cocos stage fingerprint 確認是倉庫；直接點同一個
            # rewardBtn，不再用 OCR 找「領取」。失敗時不偷偷回 OCR。
            from utils.carpark_auto import claim_open_warehouse

            if not claim_open_warehouse(page):
                logger.warning(f"[{ip}] Cocos 車位倉庫領取失敗，保留 stage 供上層有限重試")
                return False
        else:
            # ADB 維持既有 OCR 路徑。
            img_tools.click_str_by_server(d, '領取', y_range=(697, 737))
        time.sleep(2)
        closed_by_js = False
        if page is not None:
            try:
                from utils.carpark_auto import _close_carpark_transient_views, _return_parking_to_main
                _close_carpark_transient_views(page)
                time.sleep(0.5)
                closed_by_js = _return_parking_to_main(page)
            except Exception as e:
                logger.debug(f"[{ip}] 車位倉庫 JS 關閉失敗，改用空白點擊: {e}")
        if not closed_by_js:
            click_white(d)
            time.sleep(1)
        return True

    if stage in ("購物管家", "神秘商人"):
        logger.info(f"[{ip}] 偵測到 {stage}，點擊空白返回主頁")
        click_white(d)
        time.sleep(1)
        return True

    if stage == "家族":
        # Detector returns "家族" whenever OCR sees "家族商店" or "家族亂鬥" in
        # the captured frame (game_state/detector.py:54-55). This fires not
        # only when 5554 is genuinely inside the guild tab, but also when
        # cocos node residue from a just-closed popup leaves those strings
        # visible during the transition. Without recovery the task loop
        # walks every daily task and logs "不在主頁面" for each — 16+ ERRORs
        # per round, ~180/day on 5554. See logs/emulator-5554/main.log
        # 2026-05-22 04:14 for the pattern that motivated this case.
        #
        # Best-effort: returns True so resolve_stage_until_stable re-probes
        # stage on the next chain iteration. If recovery genuinely fails
        # for max_chain rounds, the resolver returns "家族" and the outer
        # task loop logs the mismatch as before — no worse than before
        # this patch.
        logger.info(f"[{ip}] 偵測到 家族 殘留 stage，嘗試返回主頁")
        cocos_ok = None
        try:
            from utils.cocos_navigator import try_cocos_navigate
            cocos_ok = try_cocos_navigate(d, ip, "main")
        except Exception as e:
            logger.debug(f"[{ip}] 家族 cocos goto_main raised: {e}")
        if cocos_ok is True:
            time.sleep(1)
            return True
        # Fallback (cocos disabled, no _page, or sweep failed): tap the home
        # tab via the same coordinate navigate_to_main_page uses for the
        # 家園 → 主頁面 transition. Safe no-op when already on main (the tap
        # toggles 家園 off and lands on main).
        try:
            from game_actions.navigation import _HOME_BTN
            d.click(*_HOME_BTN)
        except Exception as e:
            logger.debug(f"[{ip}] 家族 fallback home tap failed: {e}")
        time.sleep(1)
        return True

    return False


def resolve_stage_until_stable(d, ip: str, Cnn_model=None, reward_fn=None, logger: logging.Logger = None, max_chain: int = 6, img=None) -> str:
    """Use one state-detection path for startup and normal loops."""
    if logger is None:
        logger = logging.getLogger(__name__)
    if reward_fn is None:
        reward_fn = reward

    stage = "未知"
    # Startup and normal loops both use the same resolver so popup combinations are handled consistently.
    current_img = img
    for _ in range(max_chain):
        stage = get_stage(d, Cnn_model, img=current_img)
        current_img = None
        if _handle_known_stage_popup(d, ip, stage, reward_fn=reward_fn, logger=logger):
            continue
        return stage
    return stage

def handle_game_startup_pages(d, ip: str,  start_game_fn, 
                               reward_fn, logger: logging.Logger = None) -> bool:
    """
    啟動後統一以狀態判斷處理首頁彈窗。
    只保留啟動專屬控制，例如主頁雙確認、未知頁重試、異地登入中止。
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    wait_time = time.time()
    unknown_count = 0
    max_unknown_attempts = 5
    wait_timeout = 60
    unknown_detection_delay = 30
    startup_restart_count = 0
    max_startup_restarts = 5
    last_stage = "未知"
    main_confirm_count = 0
    required_main_confirm = 2

    while True:
        try:
            # 每輪先honor儀表板控制：暫停凍結、強制休眠/開網頁unwind到主迴圈，
            # 取代過去「偵測不到主頁面就強制重開」的無法中斷迴圈。
            _honor_startup_controls(ip)

            # 全域重啟上限：每次重啟都會把 wait_time 重置，wait_timeout 因此永不觸發，
            # 頁面一直關閉/未知時（如同帳號異地登入頂號）會無限重啟（live log 實測 105 次）。
            # 達上限即放棄本輪啟動，return False 讓主迴圈套 30 分鐘避讓休眠（並關閉瀏覽器）。
            if startup_restart_count >= max_startup_restarts:
                logger.warning(
                    f"[{ip}] 啟動重啟次數達上限 ({startup_restart_count}/{max_startup_restarts})，"
                    f"放棄本次啟動避讓休眠 | last_stage={last_stage}"
                )
                try:
                    d.app_stop("com.mxdzz.tw.and")
                except Exception as stop_err:
                    logger.warning(f"[{ip}] 達重啟上限後停止遊戲失敗: {stop_err}")
                return False
            # H5 已知頁面直接由 Cocos navigator 收回主頁，不為 HOME/FARM/MINE 等
            # 可確定狀態做全幀 OCR。未知 overlay 與登入衝突仍交給下方舊 detector。
            try:
                from utils.page_detector import PageState, detect_known_h5_page
                known_page = detect_known_h5_page(d, ip)
                if known_page == PageState.MAIN:
                    current_stage = "主頁面"
                elif known_page in (PageState.OFFLINE_REWARD, PageState.CARPARK_WAREHOUSE):
                    # 這兩個 view 是可領取的前景 popup；不能直接 goto_main
                    # 把獎勵跳掉，必須交給 Cocos stage handler 處理。
                    current_stage = resolve_stage_until_stable(
                        d, ip, Cnn_model=None, reward_fn=reward_fn, logger=logger
                    )
                elif known_page is not None:
                    from utils.cocos_navigator import CocosNavigator
                    page = getattr(d, "_page", None)
                    if page is not None and CocosNavigator(page).goto_main():
                        logger.info(f"[{ip}] 啟動 Cocos 從 {known_page.value} 返回主頁")
                        current_stage = "主頁面"
                    else:
                        current_stage = resolve_stage_until_stable(
                            d, ip, Cnn_model=None, reward_fn=reward_fn, logger=logger
                        )
                else:
                    current_stage = resolve_stage_until_stable(
                        d, ip, Cnn_model=None, reward_fn=reward_fn, logger=logger
                    )
            except Exception as cocos_exc:
                logger.debug(f"[{ip}] 啟動 Cocos 探測失敗，退回 OCR: {cocos_exc}")
                current_stage = resolve_stage_until_stable(
                    d, ip, Cnn_model=None, reward_fn=reward_fn, logger=logger
                )
            last_stage = current_stage
            logger.info(f"[{ip}] 啟動狀態判定: {current_stage}")

            if current_stage != "主頁面":
                main_confirm_count = 0

            if current_stage == "主頁面":
                main_confirm_count += 1
                logger.info(f"[{ip}] 主頁面確認 ({main_confirm_count}/{required_main_confirm})")
                if main_confirm_count >= required_main_confirm:
                    logger.info(f"[{ip}] 啟動完成，已穩定進入主頁面")
                    return True
                time.sleep(1)
                continue

            if current_stage == "異地登錄":
                elapsed = time.time() - wait_time
                logger.warning(
                    f"[{ip}] 啟動時偵測到異地登錄，停止本次啟動 | elapsed={elapsed:.1f}s"
                )
                d.app_stop("com.mxdzz.tw.and")
                # 異地登入時直接結束本輪啟動，避讓 30 分鐘後再由外層喚醒。
                wake_ts = time.time() + 1800
                wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
                bot_state.update_state(ip, task="休眠中", step=f"偵測到異地登錄 (預計 {wake_time_str} 喚醒)", next_wake_at=wake_ts)
                raise StartupLoginConflictError("啟動時偵測到異地登錄")

            if current_stage == "未知":
                time_elapsed = time.time() - wait_time
                if time_elapsed < unknown_detection_delay:
                    logger.info(f"[{ip}] 啟動初期仍為未知頁面，先等待穩定 ({time_elapsed:.1f}/{unknown_detection_delay}s)")
                    time.sleep(2)
                else:
                    unknown_count += 1
                    logger.info(f"[{ip}] 未知頁面，嘗試返回與重判 ({unknown_count}/{max_unknown_attempts})")
                    d.press("back")
                    time.sleep(5)
                    if unknown_count >= max_unknown_attempts:
                        logger.warning(f"[{ip}] 未知頁面連續出現 {unknown_count} 次，重啟遊戲")
                        try:
                            d.app_stop("com.mxdzz.tw.and")
                        except Exception as e:
                            logger.error(f"[{ip}] 停止遊戲失敗: {e}")
                        time.sleep(1)
                        start_game_fn(d, ip)
                        time.sleep(30 + random.randint(0, 5))
                        wait_time = time.time()
                        unknown_count = 0
                        startup_restart_count += 1
                continue

            unknown_count = 0
            time.sleep(1)

            if time.time() - wait_time > wait_timeout:
                elapsed = time.time() - wait_time
                logger.warning(
                    f"[{ip}] 啟動等待超時 ({wait_timeout}s) | elapsed={elapsed:.1f}s, last_stage={last_stage}, "
                    f"unknown_count={unknown_count}, restart_count={startup_restart_count}"
                )
                d.app_stop("com.mxdzz.tw.and")
                return False

        except Exception as e:
            # 控制訊號例外（強制休眠 / 開網頁 / 異地登錄）不可被當成「啟動失敗」
            # 吞掉去重開遊戲——必須往上拋，交給主迴圈處理（休眠 / 回頂端）。
            if isinstance(
                e,
                (StartupLoginConflictError, ForceSleepRequested, WakeLoopInterrupted),
            ):
                raise
            logger.error(f"[{ip}] handle_game_startup_pages 執行失敗: {e}", exc_info=True)
            try:
                d.app_stop("com.mxdzz.tw.and")
            except Exception as e2:
                logger.error(f"[{ip}] 停止遊戲失敗: {e2}")
            time.sleep(1)
            start_game_fn(d, ip)
            time.sleep(30 + random.randint(0, 5))
            wait_time = time.time()
            unknown_count = 0
            startup_restart_count += 1
            logger.warning(
                f"[{ip}] 啟動頁處理失敗後已重啟遊戲 | error={e}, restart_count={startup_restart_count}, last_stage={last_stage}"
            )

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
from datetime import datetime
from pathlib import Path
from typing import Tuple

import cv2
from PIL import Image
import img_tools
from tools import click_white
from adb_operations import connect_u2_with_retries, start_game_by_icon
from game_state.detector import get_stage
from game_actions.reward_manager import reward
from utils.logging_utils import logger, default_logger
import new_cnn.cnn_model as cnn_model_module
import bot_state
import config_manager
from device_wrapper import create_web_device_if_enabled


def _save_online_check_debug_images(ip: str, full_img, roi_img, attempt: int, logger_obj: logging.Logger) -> None:
    debug_dir = Path("logs") / "online_check_debug" / ip
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def _save(path: Path, img) -> bool:
        try:
            if cv2.imwrite(str(path), img):
                return True
        except Exception:
            pass
        ok, buf = cv2.imencode(path.suffix, img)
        if not ok:
            return False
        buf.tofile(str(path))
        return path.exists() and path.stat().st_size > 0

    full_path = debug_dir / f"{timestamp}_attempt{attempt}_full.png"
    roi_path = debug_dir / f"{timestamp}_attempt{attempt}_roi.png"
    saved_full = _save(full_path, full_img)
    saved_roi = _save(roi_path, roi_img)
    logger_obj.info(
        f"[{ip}] online-check debug images saved: full={full_path if saved_full else 'failed'}, "
        f"roi={roi_path if saved_roi else 'failed'}"
    )


class StartupLoginConflictError(Exception):
    """啟動流程中偵測到異地登錄。"""
    pass


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
        logger.info(f"[{ip}] 偵測到 {stage}，執行 reward()")
        reward_fn(d)
        time.sleep(1)
        return True

    if stage == "車位倉庫":
        logger.info(f"[{ip}] 偵測到車位倉庫，嘗試領取後返回主頁")
        img_tools.click_str_by_server(d, '領取', y_range=(697, 737))
        time.sleep(2)
        click_white(d)
        time.sleep(1)
        return True

    if stage in ("購物管家", "神秘商人"):
        logger.info(f"[{ip}] 偵測到 {stage}，點擊空白返回主頁")
        click_white(d)
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
    last_stage = "未知"
    main_confirm_count = 0
    required_main_confirm = 2

    while True:
        try:
            # Startup loop only decides startup-specific things; popup cleanup is delegated to the shared resolver.
            current_stage = resolve_stage_until_stable(
                d,
                ip,
                Cnn_model=None,
                reward_fn=reward_fn,
                logger=logger,
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
            if isinstance(e, StartupLoginConflictError):
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

def check_on_line(Cnn_model):
    """
    用 5554 設備去檢查 5558 帳號是否在線上。
    透過 5554 登入，檢查 5558 的人物線上狀態。
    """
    ip = 'emulator-5554'
    device_cfg = config_manager.get_device_config(ip)
    backend_kind = str(device_cfg.get("backend", "adb")).strip().lower()
    d = None

    is_web_backend = (backend_kind == "web_h5")

    try:
        if backend_kind == "web_h5":
            # 5554 已是 web_h5 時，直接使用同一套網頁裝置，不走 ADB 重連/重啟。
            d = create_web_device_if_enabled(ip, cfg=device_cfg, logger_obj=default_logger)
            if d is None:
                raise RuntimeError(f"[{ip}] web_h5 backend is enabled but web device is unavailable")
        else:
            d = connect_u2_with_retries(ip, logger=default_logger)
            if not d.info.get('screenOn'):
                logger.info("螢幕未開啟，嘗試解鎖")
                d.unlock()
                time.sleep(1)
    except Exception as e:
        logger.error(f"連線失敗: {e}")
        if backend_kind == "web_h5":
            raise
        try:
            d = connect_u2_with_retries('3a8d31f2', logger=default_logger)
        except Exception:
            raise
    
    # 啟動遊戲：web_h5 不使用桌面圖示流程，直接打開遊戲頁避免跳到 about:blank。
    if is_web_backend:
        d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
    else:
        start_game_by_icon(d, ip)

    time.sleep(20+random.randint(0, 5))  # 增加隨機延遲
    start_time = time.time()
    while (time.time() - start_time) < 60:
        try:
            current_img = d.screenshot(format='opencv')
            current_pil = Image.fromarray(current_img[..., ::-1])
            screen_stage = cnn_model_module.predict_image(Cnn_model, current_pil)
            logger.info(f"目前頁面: {screen_stage}")
            if screen_stage == "main":
                logger.info("in game")
                time.sleep(5)
                break
            elif screen_stage == "reward":
                reward(d)
            else:
                logger.info("not in game")
                time.sleep(7)
                d.click(0.99, 0.01)
        except Exception as e:
            logger.error(f"連線失敗: {e}")
            # web_h5 在線上檢查失敗時避免 app_stop 造成 about:blank/關頁副作用。
            if not is_web_backend:
                d.app_stop("com.mxdzz.tw.and")
            return True
    current_img = d.screenshot(format='opencv')
    current_pil = Image.fromarray(current_img[..., ::-1])
    if cnn_model_module.predict_image(Cnn_model, current_pil) == "main":
        d.click(0.05, 0.01)
        time.sleep(1)
        d.click(54, 364)
        time.sleep(3)
        
        # 使用 PaddleOCR 進行判定
        # 區域調整為包含文字可能出現的範圍
        current_img = d.screenshot(format='opencv')
        
        stage_ocr_img = current_img[220:270, 180:380]
        results = img_tools.get_all_text(stage_ocr_img)
        logger.info(f"線上狀態辨識結果: {results}")
        
        full_text = "".join(results)
        # 只要出現代表時間的關鍵字，就判定為不在線上
        if any(k in full_text for k in ["前", "離線", "小時", "分鐘", "天", "剛剛"]):
            logger.info("確認帳號目前不在線上 (pass)")
            d.app_stop("com.mxdzz.tw.and")
            return False
        # 如果明確偵測到「在線」，則判定為忙碌
        elif "線上" in full_text:
            logger.info("偵測到帳號正在線上 (busy)")
            d.app_stop("com.mxdzz.tw.and")
            return True
        else:
            # 其他情況 (如辨識不清) 預設判定為在線 (busy)
            logger.info("未偵測到明確狀態，預設判定為在線 (busy)")
            d.app_stop("com.mxdzz.tw.and")
            return True
    elif get_stage(d, Cnn_model, img=current_img) == "異地登錄":
        d.app_stop("com.mxdzz.tw.and")
        time.sleep(5*60)
        return False
    d.app_stop("com.mxdzz.tw.and")
    return False


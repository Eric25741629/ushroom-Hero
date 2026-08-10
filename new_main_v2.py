import sys
import os

# 方案四：優化 SMB/NAS 執行效率
# 關閉 .pyc 檔案寫入，避免在網路路徑產生大量 I/O 導致卡頓
sys.dont_write_bytecode = True

from adb_operations import (
    connect_u2_with_retries, unlock_screen,
    start_game_by_icon, check_in_game, set_screen_for_game, reset_screen_settings,
)
import time
from device import open_notification
from adb_devices import launch_clone
from Skill import *
from park import *
from family import Family_manager
import random
from Spin_Wheel import spin_wheel
from Mission import mission
from State import state
import atexit
#引入log 通知 不使用print
import threading

from utils.logging_utils import (
    setup_logger_for_device,
    set_thread_logger,
    logger,
    rotate_existing_logs_once,
)
from game_actions.reward_manager import reward
from game_initialization import (
    handle_game_startup_pages,
    StartupLoginConflictError,
)
import new_cnn.cnn_model as cnn_model
# 導入新的JSON管理器，保持向後兼容
from miner.models.classifier import ClassifierCNN, load_cnn_model as load_miner_cnn_model
from miner.rl.rl_recorder import RLRecorder
import urllib3
import warnings
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)
warnings.filterwarnings('ignore', category=InsecureRequestWarning)
import requests
requests.packages.urllib3.disable_warnings()
from utils.wake_up_handler import handle_device_wakeup
from utils.log_paths import LogPaths
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR
import config_manager

import bot_state
from device_wrapper import MonitoredDevice
from runtime_services.device_scan_service import (
    refresh_adb_server,
    scan_and_start_devices,
)
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    PhoneUnreachableError,
    WakeLoopInterrupted,
    handle_connect_failure,
    is_emulator_serial,
    is_recoverable_connect_error,
    reset_connect_failure,
)
from runtime_services.web_session_service import (
    LOGIN_CONFLICT_SLEEP_SEC,
    handle_pending_web_launch,
    initialize_runtime_device,
    resolve_skip_online_check_once,
    shutdown_web_devices,
)
from game_actions.stage_guard import (
    LoginConflictError,
    get_stage_with_check,
)
from runtime_services.sleep_service import (
    StartupBypassError,
    _maybe_resume_sleep,
    run_sleep_cycle,
    should_stop_runtime_device_for_sleep,
    stop_runtime_device_for_sleep,
)
from runtime_services.startup_sleep import _handle_startup_sleep
from runtime_services.ws_fallback_service import (
    run_ws_fallback_wait_round,
    should_ws_fallback,
)
from game_actions import daily_pipeline, special_wanshen
from game_actions.ws_phase import run_ws_phase, acquire_scheduler_lease
from game_actions.browser_skip import should_skip_browser


atexit.register(lambda: shutdown_web_devices(logger))


def _run_ws_phase_for_wake(ip, logger_obj):
    """執行單輪 WS-first 階段，並寫入裝置專屬 log。"""
    def _shadow(event_name, phase_name):
        """載入旁路 adapter；測試 stub 或 telemetry 失敗都安全忽略。"""
        try:
            from runtime_services.runtime_fsm import (
                ControlMode, RuntimeEvent, RuntimePhase,
            )
            from runtime_services.runtime_fsm_shadow import observe_runtime_event
            paused_fn = getattr(bot_state, "is_paused", None)
            paused = bool(paused_fn(ip)) if callable(paused_fn) else False
            mode = ControlMode.PAUSED if paused else ControlMode.RUNNING
            return observe_runtime_event(
                ip,
                RuntimeEvent(event_name),
                RuntimePhase(phase_name),
                control_mode=mode,
            )
        except Exception:  # noqa: BLE001 — shadow mode is best-effort
            return None

    # 初始化 callback / offline fallback 也可能直接進入 WS；補記 wake edge
    # 讓 shadow context 不會把第一個 WS_COMPLETED 誤判成非法起點。
    try:
        from runtime_services.runtime_fsm_shadow import runtime_shadow_snapshot
        shadow = runtime_shadow_snapshot(ip)
        if not shadow or shadow.get("phase") == "SLEEPING":
            _shadow("WAKE_DUE", "WS_PHASE")
    except Exception:  # noqa: BLE001 — shadow only
        pass
    # 先取得 SCHEDULER lease：搶回背景借用者、等待 dashboard 工具釋放。
    # 放在 enabled 檢查前：未啟用 WS 的裝置接著開 H5/APP 一樣需要登記。
    acquire_scheduler_lease(ip, logger_obj)
    device_cfg = config_manager.get_device_config(ip)
    if (device_cfg.get("special_wanshen_account", False)
            and device_cfg.get("special_wanshen_enabled", False)):
        _shadow("WS_COMPLETED", "WAKING_CLIENT")
        return frozenset()
    ws_cfg = device_cfg.get("ws_token") or {}
    if not ws_cfg.get("enabled", False):
        _shadow("WS_COMPLETED", "WAKING_CLIENT")
        return frozenset()
    ws_completed = False
    try:
        bot_state.update_state(ip, task="WS 階段", step="純 WS 任務執行中")
        logger_obj.info(f"[{ip}] WS 階段開始（純 WS，開 H5/APP 前）")
        result = run_ws_phase(ip, logger_obj=logger_obj)
        ws_completed = True
    except LoginConflictError:
        # 這是控制訊號，不是 WS 的一般降級錯誤；必須交給 main 的既有
        # runtime_login_conflict_30m handler，避免繼續開 H5/ADB pipeline。
        raise
    except Exception as ws_exc:
        logger_obj.warning(f"[{ip}] WS 階段未預期錯誤（降級，全跑 Playwright）: {ws_exc}")
        result = frozenset()
    if ws_completed:
        # WS 已返回後再檢查控制訊號；即使隨即要求休眠，也要保留完成事件。
        _shadow("WS_COMPLETED", "WAKING_CLIENT")
    # 強制休眠在 WS 階段到達 → run_ws_phase 已在任務邊界 abort 並寫 ledger
    # （下輪續做）。這裡消費信號並 raise，三個呼叫端（init 迴圈 / 主迴圈 /
    # ws_fallback）各自把它轉成休眠，絕不能繼續開瀏覽器跑 pipeline。
    if bot_state.check_force_sleep(ip):
        _shadow("FORCE_SLEEP", "SLEEPING")
        raise ForceSleepRequested(f"[{ip}] force sleep requested during ws phase")
    return result


def _run_offline_scheduled_ws_tasks(ip, logger_obj):
    from game_actions.dungeon_scheduler import run_offline_wanshen_if_due

    return run_offline_wanshen_if_due(ip, logger_obj)


def _refresh_h5_ws_credentials(d, ip, logger_obj):
    """H5 已可操作時，被動回寫同一頁面的 WS credentials。"""
    try:
        from utils.ws_ticket_refresh import refresh_from_device
        return bool(refresh_from_device(d, ip))
    except Exception as exc:  # noqa: BLE001 — 憑證回寫失敗不可阻斷 Playwright
        logger_obj.warning("[%s] H5 WS 憑證回寫失敗，保留 Playwright fallback: %s",
                           ip, exc)
        return False




def main(ip, Cnn_model, oracle_cnn_model, oracle_classes, ocr):
    def _shadow(event_name, phase_name):
        """只記錄旁路 FSM 觀察，不執行 effect 或改 live state。"""
        try:
            from runtime_services.runtime_fsm import (
                ControlMode, RuntimeEvent, RuntimePhase,
            )
            from runtime_services.runtime_fsm_shadow import observe_runtime_event
            paused_fn = getattr(bot_state, "is_paused", None)
            paused = bool(paused_fn(ip)) if callable(paused_fn) else False
            mode = ControlMode.PAUSED if paused else ControlMode.RUNNING
            return observe_runtime_event(
                ip,
                RuntimeEvent(event_name),
                RuntimePhase(phase_name),
                control_mode=mode,
            )
        except Exception:  # noqa: BLE001 — shadow mode is best-effort
            return None

    # ws_token 純 WS 後端分支：設了 use_ws_runner 的裝置不連 ADB/Playwright、
    # 不啟動遊戲、不走 daily_pipeline，改由 ws_token.runner.run_device 每次喚醒跑
    # 一輪純 WS 任務，沿用既有睡眠/喚醒/暫停/強制休眠機制 (schedule parity 同樣適用)。
    # 預設 use_ws_runner=False → 一般裝置一行邏輯都不走這裡。
    if bool(config_manager.get_device_config(ip).get("use_ws_runner", False)):
        from runtime_services.ws_runner_service import run_ws_device_loop
        ws_logger = setup_logger_for_device(ip)
        set_thread_logger(ws_logger)
        ws_logger.info(f"[{ip}] 使用 ws_token 純 WS 後端 (use_ws_runner=True)，跳過 ADB/Playwright 初始化")
        run_ws_device_loop(ip, ws_logger)
        return

    # 初始化狀態監控
    bot_state.init_device(ip)
    device_logger = logger
    backend_kind = "adb"
    enable_dungeon_manager = bool(
        config_manager.get_device_config(ip).get(
            "enable_dungeon_manager",
            config_manager.get_device_config(ip).get("enable_dungeon", True),
        )
    )
    # Granular 每日任務開關。向後相容 fallback（舊 config 只有 enable_dungeon /
    # enable_dungeon_manager 時的 legacy 推導）在 config_manager._get_raw_device_config
    # 的 merge 點完成——這裡讀到的值已是推導後結果，直接取用即可。
    # enable_dungeon_manager 本身保留原樣，繼續餵給 sleep/ws-fallback 路徑。
    cfg = config_manager.get_device_config(ip)
    special_wanshen_one_shot = (
        bool(cfg.get("special_wanshen_account", False))
        and bool(cfg.get("special_wanshen_enabled", False))
    )
    special_wanshen_claimed = False
    enable_hellgate = bool(cfg.get("enable_hellgate", True))
    enable_wanshen = bool(cfg.get("enable_wanshen", True))
    enable_cloud_battle = bool(cfg.get("enable_cloud_battle", True))
    enable_biweekly = bool(cfg.get("enable_biweekly", True))
    enable_arena = bool(cfg.get("enable_arena", True))
    enable_mining = bool(cfg.get("enable_mining", True))
    d_orig = None
    resume_sleep_until_ts = None
    resume_sleep_reason = ""
    force_sleep_now = False
    pre_runtime_ws_done = None
    
    try:
        bot_state.set_web_launch_consumer_active(
            ip, str(cfg.get("backend", "adb")).strip().lower() == "web_h5"
        )
        # 為該設備設定獨立的 logger（按 IP 分檔），先建立 logger 以便連線階段可記錄
        device_logger = setup_logger_for_device(ip)
        # 設定當前線程的 logger
        set_thread_logger(device_logger)
        backend_kind = str(config_manager.get_device_config(ip).get("backend", "adb")).strip().lower()

        # 萬神專屬模式是一次性排程，不共用一般裝置的常駐喚醒迴圈。
        # 先登記今日已嘗試，再初始化瀏覽器；即使登入或啟動失敗，也不會當天重跑。
        if special_wanshen_one_shot:
            special_wanshen_claimed = special_wanshen.claim_if_due(ip, cfg=cfg)
            if not special_wanshen_claimed:
                device_logger.info(f"[{ip}] 萬神一次性排程目前不需執行，結束執行緒")
                return
            if special_wanshen.uses_pure_ws(cfg):
                device_logger.info(f"[{ip}] 萬神一次性排程使用 pure WS，跳過 H5/runtime 初始化")
                special_wanshen.run_claimed_pure_ws(ip, cfg=cfg)
                return

        _handle_startup_sleep(ip, device_logger)

        def _run_initial_ws_phase_before_web_start():
            nonlocal pre_runtime_ws_done
            if pre_runtime_ws_done is None:
                result = _run_ws_phase_for_wake(ip, device_logger)
                # 若這輪 WS 被「開啟瀏覽器」或「暫停」中斷，別快取半套結果：留 None，讓
                # 主迴圈重跑 WS 階段（讀 ledger 續做未完成），而非沿用部分 skip-set。
                if (not bot_state.has_pending_web_launch_request(ip)
                        and not bot_state.is_paused(ip)):
                    pre_runtime_ws_done = result

        while True:
            try:
                d_orig, d, backend_kind, skip_online_check_once = initialize_runtime_device(
                    ip,
                    device_logger,
                    connect_u2_with_retries,
                    before_web_device_start=_run_initial_ws_phase_before_web_start,
                )
                reset_connect_failure(ip)
                break
            except ForceSleepRequested as e:
                force_sleep_now = True
                _shadow("FORCE_SLEEP", "SLEEPING")
                device_logger.warning(f"[{ip}] 初始化期間收到強制休眠，暫停啟動並進入休眠: {e}")
                stop_runtime_device_for_sleep(d_orig, ip, backend_kind, device_logger)
                if special_wanshen_one_shot:
                    device_logger.info(f"[{ip}] 萬神一次性排程不進入通用休眠")
                    if backend_kind == "web_h5":
                        bot_state.set_web_browser_open(ip, False)
                    return
                _, _, wake_up_time = run_sleep_cycle(
                    ip,
                    device_logger,
                    force_sleep_now=True,
                    sleep_policy="force_sleep",
                    sleep_reason="強制休眠",
                    enable_dungeon_manager=enable_dungeon_manager,
                )
                force_sleep_now = False
                continue
            except LoginConflictError as e:
                # WS-first 在初始化 callback 內明確收到 cmd 259 時，瀏覽器尚未
                # 啟動，直接使用同一個 30 分鐘 runtime policy 後重試初始化。
                forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                logger.warning(
                    f"[{ip}] WS 初始化前偵測到異地登錄: {e} | "
                    f"policy=runtime_login_conflict_30m, "
                    f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                )
                run_sleep_cycle(
                    ip,
                    device_logger,
                    forced_wake_ts=forced_wake_ts,
                    sleep_policy="runtime_login_conflict_30m",
                    sleep_reason="WS 初始化前偵測異地登錄",
                    enable_dungeon_manager=enable_dungeon_manager,
                )
                continue
            except Exception as e:
                if backend_kind == "web_h5":
                    device_logger.error(f"[{ip}] web_h5 backend init failed: {e}")
                    device_logger.warning(f"[{ip}] web_h5 init backoff 30s to avoid relaunch storm")
                    backoff_deadline = time.time() + 30
                    while time.time() < backoff_deadline:
                        if bot_state.has_pending_web_launch_request(ip):
                            device_logger.info(f"[{ip}] 收到手動開啟瀏覽器請求，提前結束 init backoff")
                            break
                        time.sleep(0.5)
                    bot_state.set_offline(ip, reason=f"init failed: {e}")
                    return
                handle_connect_failure(ip, e, device_logger, _running_threads, logger, refresh_adb_server)
                device_logger.error(f"[{ip}] connect init failed: {e}")
                # offline_fallback ADB 裝置：手機不在 ADB 上時不放棄、不判離線，
                # 改跑一輪純 WS（idle reward/lamp/mining）+ 對齊休眠，下一輪 continue
                # 重試連線。手機回線 → init 成功 → break 進正常主迴圈，行為與今日相同。
                # 旗標關閉時（預設）走原 set_offline + return，零行為差異。
                if should_ws_fallback(ip, backend_kind):
                    device_logger.warning(
                        f"[{ip}] 手機 ADB 不可達，啟用離線純 WS 備援，跑一輪 WS 後等待回線重試"
                    )
                    try:
                        run_ws_fallback_wait_round(
                            ip,
                            device_logger,
                            run_ws_phase_fn=_run_ws_phase_for_wake,
                            enable_dungeon_manager=enable_dungeon_manager,
                            run_offline_tasks_fn=_run_offline_scheduled_ws_tasks,
                        )
                    except LoginConflictError as conflict:
                        # fallback helper 不能吞掉控制訊號；此時仍未完成 ADB
                        # 初始化，直接套同一個 runtime 30 分鐘 cooldown，再重試。
                        forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                        logger.warning(
                            f"[{ip}] 離線 WS 備援偵測到異地登錄: {conflict} | "
                            f"policy=runtime_login_conflict_30m, "
                            f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                        )
                        run_sleep_cycle(
                            ip,
                            device_logger,
                            forced_wake_ts=forced_wake_ts,
                            sleep_policy="runtime_login_conflict_30m",
                            sleep_reason="離線 WS 備援偵測異地登錄",
                            enable_dungeon_manager=enable_dungeon_manager,
                        )
                    continue
                bot_state.set_offline(ip, reason=f"init failed: {e}")
                return
        
        wake_up_time = time.time()
        
        # 為每個設備生成隨機的喚醒分鐘偏移 (0 到 2 分鐘)
        wake_random_offset = random.randint(0, 2)
        logger.info(f"[{ip}] 設定隨機喚醒偏移: {wake_random_offset} 分鐘")
        wheel_manager = spin_wheel(device=d, cnn_model=Cnn_model,devices_serial=ip)
        mission_manager = mission(device=d, ip=ip)
        family_manager = Family_manager(device=d, ip=ip, cnn_model=Cnn_model)
        state_manager = state(device=d, cnn_model=Cnn_model)
        # assistant_manager = assistant(d=d, cnn_model=Cnn_model)
        clf = ClassifierCNN(model=oracle_cnn_model, classes=oracle_classes, dataset_root=DATASET_LOW_CONFIDENCE_DIR_STR)

        # 建立 RL 記錄器（記錄但不自動訓練）
        rl_logs_dir = os.path.join("miner", "rl_logs", LogPaths.safe_device_id(ip))
        os.makedirs(rl_logs_dir, exist_ok=True)
        rl_recorder = RLRecorder(
            log_dir=rl_logs_dir,
            auto_train=False,  # 不自動訓練
            flush_interval=1,
        )

        while (1):
            force_sleep_now = False
            forced_wake_ts = None
            sleep_policy = "aligned_window"
            sleep_reason = "常規對齊喚醒"
            skip_phone_cleanup = False
            try:
                if bot_state.check_force_sleep(ip):
                    raise ForceSleepRequested("force sleep requested from dashboard")
                # 關閉瀏覽器請求（web_h5）：在自己的 thread 上關閉 Playwright 瀏覽器
                # （Playwright 物件 thread-affine，不可從 Flask thread 關），裝置續跑，
                # 下一輪 handle_device_wakeup 會自動冷啟動重開。
                if backend_kind == "web_h5" and bot_state.check_web_close(ip):
                    close_fn = getattr(d, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                            bot_state.set_web_browser_open(ip, False)
                            logger.info(f"[{ip}] 收到關閉瀏覽器請求，已關閉無頭瀏覽器（裝置續跑，下次喚醒自動重開）")
                        except Exception as close_err:
                            logger.warning(f"[{ip}] 關閉瀏覽器失敗: {close_err}")
                    if special_wanshen_one_shot:
                        logger.info(f"[{ip}] 萬神一次性排程收到關閉瀏覽器請求，結束執行緒")
                        return
                    continue
                if handle_pending_web_launch(ip, d, backend_kind, logger):
                    continue
                resume_sleep_until_ts, resume_sleep_reason, _skip = _maybe_resume_sleep(
                    ip, Cnn_model, resume_sleep_until_ts, resume_sleep_reason, logger
                )
                
                if _skip:
                    continue

                # --- WS 階段：純 WS 先跑（瀏覽器啟動前；WS 登入會踢頁面，順序不可反）---
                # ws_token.enabled=False 時 run_ws_phase 直接回空集合，零影響。
                if pre_runtime_ws_done is None:
                    _shadow("WAKE_DUE", "WS_PHASE")
                if pre_runtime_ws_done is not None:
                    ws_done = pre_runtime_ws_done
                    pre_runtime_ws_done = None
                else:
                    ws_done = _run_ws_phase_for_wake(ip, device_logger)

                # WS 階段可能因「開啟瀏覽器」請求被中斷（已記錄進度到 ledger）：
                # 立即回頂端讓 handle_pending_web_launch 開瀏覽器，使用者用完重新上線
                # 後下一輪 WS 階段會讀 ledger 續做未完成（ws_done 此時作廢丟棄）。
                # 僅 web_h5：adb 沒有瀏覽器可開，handle_pending_web_launch 不消費請求，
                # 無條件 continue 會變成緊迴圈。
                if backend_kind == "web_h5" and \
                        bot_state.has_pending_web_launch_request(ip):
                    device_logger.info(
                        f"[{ip}] WS 階段偵測到開啟瀏覽器請求，回頂端處理開瀏覽器")
                    continue

                # 暫停閘：WS 階段後、喚醒/開瀏覽器（或 Phase D1 跳過瀏覽器對齊休眠）前。
                # 使用者暫停時「不喚醒瀏覽器」而非「開了再暫停」→ block 於 check_pause 到
                # 恢復，恢復後 continue 回頂端重跑 WS 階段（讀 ledger 續做）。force_sleep
                # 優先（頂端 check_force_sleep 已處理，這裡讓路）。
                if (bot_state.is_paused(ip)
                        and not bot_state.has_pending_force_sleep(ip)):
                    device_logger.info(
                        f"[{ip}] WS 階段後偵測到暫停，開瀏覽器前先等待恢復")
                    bot_state.check_pause(ip)
                    continue

                # --- Phase D1：今日客戶端任務全做完 → 跳過喚醒瀏覽器，直接對齊休眠 ---
                # opt-in（skip_browser_when_all_done 預設 False；預設行為零變化）。
                # 只在 web_h5 + ws_token.enabled + 本輪 WS 登入成功 + 無任何 client 任務
                # due 時成立；任一不確定/讀 config 失敗 → should_skip_browser 回 False（照
                # 開瀏覽器，fail-safe，絕不誤跳過漏做任務）。開瀏覽器請求最高優先：有
                # pending 時不 skip（前面已 continue 去開，這裡再保險一次）。
                if (not bot_state.has_pending_web_launch_request(ip)
                        and should_skip_browser(
                            ip, ws_login_ok=bot_state.get_ws_login_ok(ip))):
                    device_logger.info(
                        f"[{ip}] 今日客戶端任務已全部完成，跳過喚醒瀏覽器，直接對齊休眠")
                    bot_state.update_state(
                        ip, task="純WS掛機",
                        step="今日客戶端任務已完成，跳過喚醒瀏覽器")
                    _shadow("TASKS_COMPLETED", "SLEEPING")
                    # 沿用既有結尾休眠機制：瀏覽器此時通常未開（喚醒被跳過），若仍開著
                    # 則用既有停止邏輯關閉（相容 web_stop_mode=close_browser；本來就沒開
                    # 則 should_stop_runtime_device_for_sleep 為真時 stop 也是安全 no-op）。
                    sleep_device_config = config_manager.get_device_config(ip)
                    if should_stop_runtime_device_for_sleep(
                            sleep_device_config, backend_kind):
                        stop_runtime_device_for_sleep(d, ip, backend_kind, logger)
                        bot_state.set_web_browser_open(ip, False)
                    wake_ts, interrupted, wake_up_time = run_sleep_cycle(
                        ip,
                        logger,
                        sleep_policy="aligned_window",
                        sleep_reason="今日客戶端任務已完成(純WS掛機)",
                        enable_dungeon_manager=enable_dungeon_manager,
                    )
                    if (interrupted
                            and bot_state.has_pending_web_launch_request(ip)
                            and time.time() < wake_ts):
                        resume_sleep_until_ts = wake_ts
                        resume_sleep_reason = "手動操作結束後返回休眠"
                    continue

                # --- 喚醒與解鎖手機 ---
                bot_state.update_state(ip, task="喚醒檢查", step="正在檢查螢幕狀態")
                skip_online_check_for_wakeup = resolve_skip_online_check_once(
                    ip,
                    backend_kind,
                    initial_skip=skip_online_check_once,
                )
                d = handle_device_wakeup(
                    d,
                    ip,
                    logger,
                    Cnn_model,
                    skip_online_check_once=skip_online_check_for_wakeup,
                )
                # wake_up_handler may reconnect and return raw uiautomator2 device.
                # Re-wrap to keep a consistent interface (tap/click/swipe/pause guard).
                if not isinstance(d, MonitoredDevice):
                    d = MonitoredDevice(d, ip)
                skip_online_check_once = False

                start = time.time()

                # web_h5 主動關閉瀏覽器時，screenshot() 會在 _ensure_browser_session
                # 內冷啟瀏覽器（2-3 秒）並被標成「slow screenshot」warning，但這是
                # web_stop_mode=close_browser 下我們自己關的、不是異常。先用 is_alive
                # 判斷，跳過喚醒截圖直接走啟動分支，讓 cold-start 算在「啟動遊戲」
                # step 上才符合語義。
                skip_wakeup_screenshot = False
                if backend_kind == "web_h5":
                    alive_fn = getattr(d, "is_alive", None)
                    browser_alive = bool(alive_fn()) if callable(alive_fn) else False
                    # Publish authoritative browser-open truth for the dashboard
                    # toggle (is_alive is thread-affine → must be read here).
                    bot_state.set_web_browser_open(ip, browser_alive)
                    if not browser_alive:
                        skip_wakeup_screenshot = True
                        logger.info(f"[{ip}] web_h5 瀏覽器已關閉，跳過喚醒截圖，直接啟動")

                if skip_wakeup_screenshot:
                    img = None
                    in_game = False
                else:
                    img = d.screenshot(format='opencv')
                    # 進行ocr
                    if state_manager.get_state() == "滑動解除節電模式":
                        unlock_screen(d)
                    in_game = check_in_game(d)

                client_ready = False
                if in_game:
                    logger.debug(f"[{ip}] 已確認在遊戲中")
                    # 即使在遊戲中，也要檢查是否有「放置獎勵」或「領取」彈窗阻擋
                    stage_check = get_stage_with_check(d, ip, Cnn_model, img=img)
                    if stage_check in ["放置獎勵", "離線獎勵", "領取"]:
                        logger.info(f"[{ip}] 偵測到 {stage_check} 彈窗，執行自動領取...")
                        reward(d)
                        time.sleep(2)
                    if backend_kind == "web_h5":
                        _refresh_h5_ws_credentials(d, ip, device_logger)
                    client_ready = True
                else:
                    logger.debug(f"[{ip}] 未確認在遊戲中，準備啟動")
                    bot_state.update_state(ip, task="啟動遊戲", step="正在啟動 APP")
                    if backend_kind == "web_h5":
                        logger.info(f"[{ip}] web_h5 backend，呼叫 app_start 開啟遊戲頁面")
                        try:
                            d.app_start("com.mxdzz.tw.and")
                        except Exception as e:
                            logger.exception(f"[{ip}] web_h5 app_start 失敗: {e}")
                            raise StartupBypassError("web_h5 app_start 失敗")
                    elif 'fc65396d' in ip or '192.168' in ip:
                        
                        time.sleep(1)
                        try:
                            launched_by_icon = start_game_by_icon(d, ip, logger=device_logger)
                            if not launched_by_icon and not check_in_game(d):
                                raise Exception("圖示/預設 app_start 未進入遊戲")
                            set_screen_for_game(ip, logger=logger)
                        except Exception as e:
                            logger.exception(f"[{ip}] 共用桌面啟動失敗，改用 clone launch. error={e}")
                            output = launch_clone("com.mxdzz.tw.and", 2,device_serial=ip)
                            set_screen_for_game(ip, logger=logger)
                        time.sleep(1)
                        
                    else:
                        # 使用圖示啟動遊戲 (模擬真人操作)
                        logger.info(f"[{ip}] 透過桌面圖示啟動遊戲")
                        start_game_by_icon(d, ip)

                    result = handle_game_startup_pages(
                        d=d,
                        ip=ip,
                        start_game_fn=start_game_by_icon,
                        reward_fn=reward,
                        logger=device_logger
                    )
                    if result:
                        logger.info(f"[{ip}] 遊戲已進入可操作狀態")
                        client_ready = True
                        if backend_kind == "web_h5":
                            _refresh_h5_ws_credentials(d, ip, device_logger)
                        elif backend_kind == "adb":
                            # 即使本裝置 ws_token 未啟用，也順手被動撈一份 ws login
                            # ticket，方便日後切 adb+ws 時已有可用 token。best-effort，
                            # 自身不拋例外（不重啟 App、不做 WS verify）。
                            from utils.adb_token_scrape import refresh_from_adb_device
                            refresh_from_adb_device(d, ip)
                    else:
                        logger.warning(f"[{ip}] 遊戲啟動失敗，避讓休眠 30 分鐘...")
                        # 計算 30 分鐘後的喚醒時間
                        wake_ts = time.time() + 1800
                        wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
                        bot_state.update_state(ip, task="休眠中", step=f"啟動失敗避讓 (預計 {wake_time_str} 喚醒)", next_wake_at=wake_ts)
                        
                        # 拋出啟動避讓例外，交給外層套用固定休眠策略
                        raise StartupBypassError("啟動失敗避讓")
                if client_ready:
                    _shadow("CLIENT_READY", "CLIENT_TASKS")
                                    # img = d.screenshot(format='opencv')
                # if red_envelope.check_red_in_pic(img):
                # red_envelope.open_red_envelope(d)

                daily_pipeline.run(daily_pipeline.DailyContext(
                    d=d,
                    ip=ip,
                    Cnn_model=Cnn_model,
                    clf=clf,
                    rl_recorder=rl_recorder,
                    current_time=time.localtime(),
                    enable_dungeon_manager=enable_dungeon_manager,
                    enable_hellgate=enable_hellgate,
                    enable_arena=enable_arena,
                    enable_mining=enable_mining,
                    enable_wanshen=enable_wanshen,
                    enable_cloud_battle=enable_cloud_battle,
                    enable_biweekly=enable_biweekly,
                    wheel_manager=wheel_manager,
                    mission_manager=mission_manager,
                    family_manager=family_manager,
                    ws_done=ws_done,
                    special_wanshen_claimed=special_wanshen_claimed,
                ))
                _shadow("TASKS_COMPLETED", "SLEEPING")
                if special_wanshen_one_shot:
                    if handle_pending_web_launch(ip, d, backend_kind, device_logger):
                        continue
                    device_logger.info(f"[{ip}] 萬神一次性排程結束，關閉執行緒")
                    stop_runtime_device_for_sleep(d, ip, backend_kind, device_logger)
                    if backend_kind == "web_h5":
                        bot_state.set_web_browser_open(ip, False)
                    return
            except WakeLoopInterrupted as e:
                # A manual web-launch request arrived mid-flow (typically a
                # live-view takeover, which pauses the device then requests the
                # browser). Don't sleep: jump back to the top of the loop where
                # handle_pending_web_launch() consumes the request and opens the
                # page. The device stays paused afterwards (set_pause by the
                # caller), so automation is held — not silently resumed.
                logger.info(f"[{ip}] 偵測到手動開網頁請求，返回迴圈頂端處理: {e}")
                continue

            except ForceSleepRequested as e:
                force_sleep_now = True
                sleep_policy = "force_sleep"
                sleep_reason = "強制休眠"
                _shadow("FORCE_SLEEP", "SLEEPING")
                logger.warning(f"[{ip}] 收到強制休眠請求，終止當前任務並進入休眠: {e}")
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)

            except PhoneUnreachableError as e:
                # 直連手機（fc65396d / 192.168）喚醒時 ADB 連線逾時 → 跳過本輪
                # ADB 任務與喚醒後清理（清理會嘗試重連，手機不在時會慢慢卡死），
                # 照常進入對齊休眠。WS 階段在 ADB 喚醒前已跑完，本輪自然降級純 WS；
                # 手機回到 wifi 後下一輪自動恢復完整流程。
                skip_phone_cleanup = True
                sleep_policy = "phone_offline_ws_only"
                sleep_reason = "手機離線降級"
                logger.warning(
                    f"[{ip}] 手機 ADB 連線逾時，跳過本輪 ADB 任務，僅保留 WS 階段結果: {e}"
                )
                bot_state.update_state(
                    ip, task="手機離線", step="手機離線，本輪僅執行 WS，照常排程休眠"
                )

            except StartupBypassError as e:
                forced_wake_ts = time.time() + 1800
                sleep_policy = "startup_bypass_30m"
                sleep_reason = "啟動失敗避讓"
                logger.warning(
                    f"[{ip}] 啟動流程中斷: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec=1800"
                )
                # 關閉瀏覽器/應用：啟動避讓時若保留壞掉的 Chrome（被頂號/頁面已關），
                # 下次喚醒會 reuse 同一個壞掉的 session。關掉它，喚醒時開全新的。
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)

            except StartupLoginConflictError as e:
                forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                sleep_policy = "startup_login_conflict_30m"
                sleep_reason = "啟動偵測異地登錄"
                logger.warning(
                    f"[{ip}] 啟動階段異地登錄中斷本次執行: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                )
                # 異地登入冷卻期間關閉瀏覽器，下次喚醒重新開啟乾淨 session。
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)

            except LoginConflictError as e:
                forced_wake_ts = time.time() + LOGIN_CONFLICT_SLEEP_SEC
                sleep_policy = "runtime_login_conflict_30m"
                sleep_reason = "執行中偵測異地登錄"
                logger.warning(
                    f"[{ip}] 異地登錄中斷本次執行: {e} | policy={sleep_policy}, "
                    f"forced_sleep_sec={LOGIN_CONFLICT_SLEEP_SEC}"
                )
                # 不需要額外處理，後續代碼會處理釋放鎖和休眠

            end = time.time()
            if special_wanshen_one_shot:
                device_logger.info(f"[{ip}] 萬神一次性排程不進入通用休眠")
                stop_runtime_device_for_sleep(d, ip, backend_kind, device_logger)
                if backend_kind == "web_h5":
                    bot_state.set_web_browser_open(ip, False)
                return
            if (not skip_phone_cleanup) and ('fc65396d' in ip or '192.168' in ip):
                reset_screen_settings(ip, logger=logger)
                time.sleep(1)
                try:
                    d.info
                except Exception as e:
                    logger.error(f"重新連線: {e}")
                    try:
                        d_orig = connect_u2_with_retries(ip, logger=device_logger)
                        d = MonitoredDevice(d_orig, ip)
                    except Exception as e2:
                        handle_connect_failure(ip, e2, device_logger, _running_threads, logger, refresh_adb_server)
                        logger.error(f"[{ip}] 重連失敗: {e2}")
                open_notification(d)
                d.screen_off()
            sleep_device_config = config_manager.get_device_config(ip)
            if (not force_sleep_now) and should_stop_runtime_device_for_sleep(sleep_device_config, backend_kind):
                stop_runtime_device_for_sleep(d, ip, backend_kind, logger)
                if backend_kind == "web_h5":
                    bot_state.set_web_browser_open(ip, False)
            wake_ts, interrupted, wake_up_time = run_sleep_cycle(
                ip,
                logger,
                forced_wake_ts=forced_wake_ts,
                force_sleep_now=force_sleep_now,
                sleep_policy=sleep_policy,
                sleep_reason=sleep_reason,
                enable_dungeon_manager=enable_dungeon_manager,
            )
            if interrupted and bot_state.has_pending_web_launch_request(ip) and time.time() < wake_ts:
                resume_sleep_until_ts = wake_ts
                resume_sleep_reason = "手動操作結束後返回休眠"
    except Exception as e:
        if backend_kind != "web_h5" and is_emulator_serial(ip) and is_recoverable_connect_error(str(e)):
            handle_connect_failure(ip, e, device_logger, _running_threads, logger, refresh_adb_server)
        logger.error(f"[{ip}] main 執行發生未預期錯誤: {e}", exc_info=True)
        bot_state.update_state(ip, log=f"異常中斷: {e}")
    finally:
        bot_state.set_web_launch_consumer_active(ip, False)
        try:
            if d_orig is not None and hasattr(d_orig, "close"):
                d_orig.close()
        except Exception as close_err:
            device_logger.warning(f"[{ip}] device close failed on thread exit: {close_err}")
        # 確保不管發生什麼事，執行緒結束時都會標記離線
        bot_state.set_offline(ip, reason="程式執行結束 (Thread Exit)")




# 全域變數，用來管理運行中的執行緒
# H6: 由 main thread (scan_and_start_devices) 與 device threads
# (handle_connect_failure → refresh_adb_server) 同時讀寫，必須在
# `_running_threads_lock` 保護下操作。鎖實際宣告於
# ``runtime_services.thread_registry`` 以避免循環匯入。

_running_threads = {} # {ip: Thread}

if __name__ == "__main__":
    import config_manager
    # 讓外部 auto-reload wrapper 用 CTRL_BREAK 請求乾淨重啟，重用 Ctrl+C 的 shutdown 路徑。
    # ponytail: dev 便利用途；正常執行不受影響。
    import signal as _signal
    if hasattr(_signal, "SIGBREAK"):
        _signal.signal(_signal.SIGBREAK, _signal.default_int_handler)
    rotate_existing_logs_once()
    from bootstrap.api_services import start_all as start_api_services

    # 背景服務彼此獨立：單一服務啟動失敗只留下 warning/error 與狀態，
    # 不可讓整個裝置掃描主流程一起中止。
    _startup_mode = config_manager.get_global_config().get("mode", "master")
    start_api_services(
        _startup_mode,
        os.path.dirname(os.path.abspath(__file__)),
        dashboard_port=config_manager.get_dashboard_port(),
    )
    # 分流：限制 torch intra-op 執行緒 + 共用模型推論併發上限，
    # 避免多裝置同時挖礦時 GPU/CPU 擠在一起。皆可由 bot_config global.compute 覆寫。
    from utils.torch_runtime import configure_torch_runtime, set_inference_concurrency
    _compute_cfg = config_manager.get_global_config().get("compute", {}) or {}
    _resolved_threads = configure_torch_runtime(_compute_cfg.get("torch_num_threads"))
    _inference_concurrency = int(_compute_cfg.get("inference_concurrency", 1))
    set_inference_concurrency(_inference_concurrency)
    logger.info(
        f"[System] 分流設定 torch_threads={_resolved_threads} "
        f"inference_concurrency={_inference_concurrency}"
    )
    # 確保模型在本機 SSD
    from utils.model_sync import ensure_local_model
    local_pth = ensure_local_model("cnn_model.pth")
    Cnn_model = cnn_model.load_cnn_model(local_pth)
    oracle_cnn_model, oracle_classes, resolved_device = load_miner_cnn_model()
    ocr = 1
    logger.info("[System] 核心已就緒，開始循環掃描 ADB 設備... (按 Ctrl+C 可退出)")
    try:
        while True:
            scan_and_start_devices(
                main,
                _running_threads,
                Cnn_model,
                oracle_cnn_model,
                oracle_classes,
                ocr,
                logger,
            )
            for _ in range(300):  # 0.1s * 300 = 30s
                if bot_state.check_refresh_needed():
                    logger.info("[System] 收到立即掃描請求！")
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("\n[System] 收到退出信號，正在關閉所有執行緒...")
        shutdown_web_devices(logger)
        logger.info("[System] 程式已結束。")

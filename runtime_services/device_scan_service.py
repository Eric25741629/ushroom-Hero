import subprocess
import threading
import time
from typing import Optional

import bot_state
import config_manager
from device import get_adb_devices
# H6: 共用 running_threads lock，避免讀/迭代/修改時與其他執行緒競爭。
from runtime_services.thread_registry import running_threads_lock as _running_threads_lock


_FIXED_EMULATORS = ["emulator-5554", "emulator-5556", "emulator-5558", "emulator-5560"]
_FIXED_PHONES = ["7fe98fc6"]
_dynamic_optional_devices = set()
_monitored_devices = list(dict.fromkeys(_FIXED_EMULATORS + _FIXED_PHONES))
_missing_devices = set()


def refresh_adb_server(running_threads: dict, logger_obj, hard_reset: bool = False, exclude_device: Optional[str] = None):
    """刷新 ADB devices；hard_reset=True 時會先暫停其他裝置，再 kill-server，最後恢復。"""
    paused_devices = []
    try:
        if hard_reset:
            # H6: snapshot 字典後在鎖外處理，避免 set_pause 期間長時間持鎖。
            with _running_threads_lock:
                snapshot = list(running_threads.items())
            for other_ip, th in snapshot:
                if other_ip == exclude_device:
                    continue
                if th is None or not th.is_alive():
                    continue
                try:
                    bot_state.set_pause(other_ip, True)
                    paused_devices.append(other_ip)
                    logger_obj.info(f"[System] kill-server 前暫停裝置: {other_ip}")
                except Exception as pause_err:
                    logger_obj.warning(f"[System] 暫停裝置失敗 {other_ip}: {pause_err}")

            time.sleep(1)
            subprocess.run(["adb", "kill-server"], capture_output=True, text=True, timeout=10, check=False)

        devices_proc = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15, check=False)
        if devices_proc.returncode != 0:
            logger_obj.warning(f"[System] adb devices 失敗: {devices_proc.stderr.strip()}")
    except Exception as e:
        logger_obj.warning(f"[System] 刷新 ADB server 失敗: {e}")
    finally:
        for other_ip in paused_devices:
            try:
                bot_state.set_pause(other_ip, False)
                logger_obj.info(f"[System] kill-server 後恢復裝置: {other_ip}")
            except Exception as resume_err:
                logger_obj.warning(f"[System] 恢復裝置失敗 {other_ip}: {resume_err}")


def get_web_backend_devices(logger_obj) -> list[str]:
    try:
        cfg = config_manager.load_config()
        devices = cfg.get("devices", {}) if isinstance(cfg, dict) else {}
        result = []
        for serial, dcfg in devices.items():
            if str((dcfg or {}).get("backend", "adb")).strip().lower() == "web_h5":
                result.append(serial)
        return sorted(set(result))
    except Exception as e:
        logger_obj.warning(f"[System] Failed to load web_h5 devices from config: {e}")
        return []


def is_web_backend_device(ip: str) -> bool:
    try:
        cfg = config_manager.get_device_config(ip)
        return str((cfg or {}).get("backend", "adb")).strip().lower() == "web_h5"
    except Exception:
        return False


def use_phone_ocr_lamp_mode(ip: str) -> bool:
    try:
        cfg = config_manager.get_device_config(ip)
    except Exception:
        cfg = {}
    if not bool((cfg or {}).get("is_real_phone", False)):
        return False
    return ("7fe98fc6" in ip) or ("fc65396d" in ip)


def _is_infinite_host() -> bool:
    host = str(config_manager.get_hostname() or "").strip().lower()
    return host == "infinite" or host.startswith("infinite.")


def scan_and_start_devices(main_fn, running_threads: dict, cnn_model, oralce_cnn_model, oralce_classes, ocr, logger_obj):
    """掃描 ADB 設備並啟動新執行緒。"""
    global _monitored_devices, _missing_devices, _dynamic_optional_devices

    global_cfg = config_manager.get_global_config()
    global_mode = str(global_cfg.get("mode", "master")).strip().lower()
    is_worker_mode = (global_mode == "worker")
    allow_web_backend = bool(global_cfg.get("allow_web_backend", True))

    refresh_adb_server(running_threads, logger_obj, hard_reset=False)

    try:
        current_devices = get_adb_devices()
        current_devices = [d for d in current_devices if d != "emulator-5562"]
        if not is_worker_mode and allow_web_backend:
            for web_ip in get_web_backend_devices(logger_obj):
                if web_ip not in current_devices:
                    current_devices.append(web_ip)
        else:
            current_devices = [d for d in current_devices if not d.startswith("emulator-")]

        if not allow_web_backend:
            skipped_web_devices = [d for d in current_devices if is_web_backend_device(d)]
            if skipped_web_devices:
                logger_obj.info(f"[System] 本機已停用 web_h5 啟動，略過設備: {sorted(skipped_web_devices)}")
            current_devices = [d for d in current_devices if not is_web_backend_device(d)]
    except Exception as e:
        print(f"[System] 掃描 ADB 失敗: {e}")
        return

    if _is_infinite_host():
        fixed_set = set(_FIXED_EMULATORS + _FIXED_PHONES)
        dynamic_now = set(current_devices) - fixed_set
        added_dynamic = sorted(dynamic_now - _dynamic_optional_devices)
        removed_dynamic = sorted(_dynamic_optional_devices - dynamic_now)
        if added_dynamic:
            logger_obj.info(f"[System] infinite 動態加入設備: {added_dynamic}")
        if removed_dynamic:
            logger_obj.info(f"[System] infinite 動態移除設備: {removed_dynamic}")
        _dynamic_optional_devices = dynamic_now
        monitored_set = fixed_set | dynamic_now
        _monitored_devices = sorted(monitored_set)
    else:
        monitored_set = set(current_devices)
        _monitored_devices = sorted(monitored_set)

    if "emulator-5554" in current_devices:
        current_devices = ["emulator-5554"] + [d for d in current_devices if d != "emulator-5554"]

    current_set = set(current_devices)
    if _is_infinite_host():
        missing_base = set(_FIXED_EMULATORS + _FIXED_PHONES)
    else:
        missing_base = monitored_set
    missing_now = missing_base - current_set
    new_missing = sorted(missing_now - _missing_devices)
    recovered = sorted(_missing_devices - missing_now)
    for ip in new_missing:
        msg = f"[{ip}] 固定監控設備在 ADB 清單消失，判定為疑似當機"
        print(f"[Watchdog] {msg}")
        logger_obj.warning(msg)
        bot_state.update_state(
            ip,
            task="疑似當機",
            step="固定監控設備未出現在 ADB",
            log="ADB 掃描缺失，疑似當機",
        )
    for ip in recovered:
        msg = f"[{ip}] 固定監控設備已重新出現在 ADB，疑似當機狀態解除"
        print(f"[Watchdog] {msg}")
        logger_obj.info(msg)
        bot_state.update_state(
            ip,
            task="設備恢復",
            step="固定監控設備重新連線",
            log="ADB 重新掃描到設備",
        )
    _missing_devices = set(missing_now)

    if _is_infinite_host():
        ignored = sorted(current_set - monitored_set)
        if ignored:
            logger_obj.info(f"[System] 忽略非監控設備: {ignored}")

    current_devices = [d for d in current_devices if d in monitored_set]

    # H6: snapshot 與所有讀/寫 running_threads 的操作都在鎖內，
    # 避免 main thread 與 device thread 同時觸碰造成資料競爭。
    with _running_threads_lock:
        stopped_ips = []
        for ip, thread in list(running_threads.items()):
            if not thread.is_alive():
                print(f"[System] 設備 {ip} 的執行緒已結束")
                stopped_ips.append(ip)

        for ip in stopped_ips:
            del running_threads[ip]

        for ip in current_devices:
            should_start = False
            if ip not in running_threads:
                should_start = True
            elif not running_threads[ip].is_alive():
                should_start = True

            if should_start:
                print(f"[System] 發現新設備或重連設備: {ip}，正在啟動掛機執行緒...")
                t = threading.Thread(
                    target=main_fn,
                    args=(ip, cnn_model, oralce_cnn_model, oralce_classes, ocr),
                    name=f"Bot-{ip}",
                    daemon=True,
                )
                t.start()
                running_threads[ip] = t

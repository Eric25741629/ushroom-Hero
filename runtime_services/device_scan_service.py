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

# 裝置連續斷線超過此秒數後，自動停止嘗試重啟 thread（直到下次掃描偵測到設備恢復）
_DEAD_DEVICE_TIMEOUT_SEC = 3 * 3600

# 裝置從 ADB 清單消失超過此秒數後，直接判定離線（dashboard 顯示 OFFLINE）。
# 使用者需求 (2026-06-11)：wifi ADB 手機帶出門 → 1 小時後卡片要轉離線而非永遠 ONLINE。
_ADB_ABSENT_OFFLINE_SEC = 3600
_adb_last_seen: dict[str, float] = {}
_adb_absence_offlined: set[str] = set()


def _apply_adb_absence_rule(adb_present: set, running_threads: dict, logger_obj, now: Optional[float] = None):
    """掉線超過 1 小時 → set_offline；回到清單 → 恢復 ONLINE / 解除重啟封鎖。

    只追蹤「曾出現在 raw ADB 掃描」的序號；web_h5 / ws_token 裝置從不進
    ADB 清單，自然不受影響。
    """
    if now is None:
        now = time.time()

    for ip in adb_present:
        _adb_last_seen[ip] = now
        if ip in _adb_absence_offlined:
            _adb_absence_offlined.discard(ip)
            with _running_threads_lock:
                th = running_threads.get(ip)
            if th is not None and th.is_alive():
                bot_state.set_online(ip, reason="ADB 重新連線，恢復上線")
                logger_obj.info(f"[Watchdog] [{ip}] 重新出現在 ADB，thread 存活，恢復 ONLINE")
            else:
                # thread 已退出：清離線時間戳，讓 3 小時 dead-device 重啟 gate 放行
                bot_state.clear_offline_anchor(ip)
                logger_obj.info(f"[Watchdog] [{ip}] 重新出現在 ADB，解除離線重啟封鎖，等待重啟")

    states = bot_state.get_all_states()
    for ip, last_seen in list(_adb_last_seen.items()):
        if ip in adb_present or ip in _adb_absence_offlined:
            continue
        if now - last_seen < _ADB_ABSENT_OFFLINE_SEC:
            continue
        st = states.get(ip)
        if st is None:
            _adb_absence_offlined.add(ip)
            continue
        if str(st.get("status")) == "OFFLINE":
            # 已因其他原因離線（如 thread exit）：不覆寫原因，但登記起來，
            # 回連時才會清 anchor 解除重啟封鎖。
            _adb_absence_offlined.add(ip)
            continue
        bot_state.set_offline(ip, reason=f"ADB 斷線超過 {_ADB_ABSENT_OFFLINE_SEC // 60} 分鐘，判定離線")
        _adb_absence_offlined.add(ip)
        logger_obj.warning(f"[Watchdog] [{ip}] 從 ADB 清單消失超過 {_ADB_ABSENT_OFFLINE_SEC // 60} 分鐘，判定離線")


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
            if str((dcfg or {}).get("backend", "adb")).strip().lower() != "web_h5":
                continue
            # 停用的裝置 (新註冊後預設 enabled=false) 先別納入監控/啟動。
            # 缺鍵的舊設定視為已啟用。
            if not bool((dcfg or {}).get("enabled", True)):
                continue
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


def _is_ws_runner_cfg(dcfg) -> bool:
    """True when a device config opts into the pure-WS ws_token runner.

    Either ``use_ws_runner: true`` or ``backend: "ws_token"`` selects the
    WS runner. These devices have no ADB serial and are not web_h5, so they
    must be injected into the scan's device list explicitly.
    """
    dcfg = dcfg or {}
    if bool(dcfg.get("use_ws_runner", False)):
        return True
    return str(dcfg.get("backend", "adb")).strip().lower() == "ws_token"


def get_ws_runner_devices(logger_obj) -> list[str]:
    """Config-declared devices that should run via the ws_token WS runner.

    Mirrors ``get_web_backend_devices``: these devices are not produced by an
    ADB scan (no serial) so they're added to the monitored set from config.
    Disabled devices (enabled=false) are skipped; missing key reads as enabled.
    """
    try:
        cfg = config_manager.load_config()
        devices = cfg.get("devices", {}) if isinstance(cfg, dict) else {}
        result = []
        for serial, dcfg in devices.items():
            if not _is_ws_runner_cfg(dcfg):
                continue
            if not bool((dcfg or {}).get("enabled", True)):
                continue
            result.append(serial)
        return sorted(set(result))
    except Exception as e:
        logger_obj.warning(f"[System] Failed to load ws_token runner devices from config: {e}")
        return []


def is_ws_runner_device(ip: str) -> bool:
    try:
        return _is_ws_runner_cfg(config_manager.get_device_config(ip))
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


def scan_and_start_devices(main_fn, running_threads: dict, cnn_model, oracle_cnn_model, oracle_classes, ocr, logger_obj):
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
        adb_present = set(current_devices)
        if not is_worker_mode and allow_web_backend:
            for web_ip in get_web_backend_devices(logger_obj):
                if web_ip not in current_devices:
                    current_devices.append(web_ip)
        else:
            current_devices = [d for d in current_devices if not d.startswith("emulator-")]

        # ws_token runner devices come from config, not from the ADB scan, and
        # need neither an ADB serial nor a browser. Inject them in BOTH master
        # and worker mode (a worker may host a WS device); the worker-mode
        # emulator strip above doesn't touch them since they aren't emulator-*.
        for ws_ip in get_ws_runner_devices(logger_obj):
            if ws_ip not in current_devices:
                current_devices.append(ws_ip)

        if not allow_web_backend:
            skipped_web_devices = [d for d in current_devices if is_web_backend_device(d)]
            if skipped_web_devices:
                logger_obj.info(f"[System] 本機已停用 web_h5 啟動，略過設備: {sorted(skipped_web_devices)}")
            current_devices = [d for d in current_devices if not is_web_backend_device(d)]
    except Exception as e:
        print(f"[System] 掃描 ADB 失敗: {e}")
        return

    _apply_adb_absence_rule(adb_present, running_threads, logger_obj)

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
                # 手動開關:停用的裝置 (新裝置設定未完成 / 使用者關閉) 不自動啟動。
                # 缺鍵的舊設定一律視為已啟用。covers all backends at the real
                # start-decision point (web devices are also pre-filtered out of
                # current_devices by get_web_backend_devices).
                if not config_manager.is_device_enabled(ip):
                    continue

                offline_since = bot_state.get_offline_since(ip)
                if offline_since is not None:
                    dead_sec = time.time() - offline_since
                    if dead_sec >= _DEAD_DEVICE_TIMEOUT_SEC:
                        logger_obj.warning(
                            f"[Watchdog] [{ip}] 已持續斷線 {dead_sec/3600:.1f} 小時"
                            f"（超過 {_DEAD_DEVICE_TIMEOUT_SEC//3600} 小時門檻），"
                            f"跳過重啟。裝置恢復連線後將自動重新啟動。"
                        )
                        continue

                print(f"[System] 發現新設備或重連設備: {ip}，正在啟動掛機執行緒...")
                t = threading.Thread(
                    target=main_fn,
                    args=(ip, cnn_model, oracle_cnn_model, oracle_classes, ocr),
                    name=f"Bot-{ip}",
                    daemon=True,
                )
                t.start()
                running_threads[ip] = t

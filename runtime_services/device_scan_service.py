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


# --- 卡死暫停守望(stuck-pause watchdog)------------------------------------
# 裝置被借去當背景用途時 set_pause(True);owner thread 死掉或長時間不讓位就會卡在
# 暫停、重啟前不再醒來(見 online_monitor._loop try/finally 的歷史註解:5554 曾永久
# paused)。掛在既有掃描迴圈上,依「暫停來源」分三類門檻自動救援:
#   1. 線上監控 detector(ONLINE_MONITOR):把睡著的裝置當 detector 是正常長期工作,
#      傷害只在該醒卻醒不來 → 寬鬆,軟 2h / 硬 3h(對齊現有 3h dead-device 門檻)
#   2. 其他腳本借用(ONLINE_CHECK / MOUNT_TRACKER / TOOL):應短暫 → 軟 30m / 硬 45m
#   3. 手動暫停 / live-view(無 registry lease)→ 24h 保底
# 軟門檻:設 lease.preempted 請 owner 自己收線讓位(乾淨,先關 client 再 release,
#        無孤兒 session);硬門檻:owner 沒反應(thread 死/wedge)→ 強制 release
#        (連帶 set_pause(False))。借用型計時用 lease.acquired_at(monitor 不續租,
#        acquired_at 即本次持有起始);手動用 bot_state.paused_since。
_MONITOR_STUCK_SOFT_SEC = 2 * 3600
_MONITOR_STUCK_HARD_SEC = 3 * 3600
_BORROW_STUCK_SOFT_SEC = 30 * 60
_BORROW_STUCK_HARD_SEC = 45 * 60
_MANUAL_STUCK_RESUME_SEC = 24 * 3600


def _sweep_stuck_pauses(logger_obj, now: Optional[float] = None) -> None:
    """自動救援卡死的暫停(見上方註解)。任何例外都吞掉,絕不弄垮掃描迴圈。"""
    if now is None:
        now = time.time()
    try:
        from runtime_services import session_registry as registry
        from runtime_services.session_registry import Owner
    except Exception:  # noqa: BLE001 — registry import 失敗不可弄垮掃描
        return
    try:
        states = bot_state.get_all_states()
    except Exception:  # noqa: BLE001
        return

    for ip, st in states.items():
        try:
            if not st.get("paused") or not bot_state.is_local_device(ip):
                continue
            lease = registry.peek(ip)
            if lease is None:
                # 手動暫停 / live-view:無借用 lease → 24h 保底 resume。
                since = bot_state.get_paused_since(ip)
                if since is not None and (now - since) >= _MANUAL_STUCK_RESUME_SEC:
                    bot_state.set_pause(ip, False)
                    logger_obj.warning(
                        f"[Watchdog] [{ip}] 手動暫停超過 "
                        f"{_MANUAL_STUCK_RESUME_SEC // 3600}h,自動恢復")
                continue
            if lease.owner is Owner.SCHEDULER:
                continue  # 排程自身不會 pause,略過
            # 借用型:依 owner 選監控/其他兩組門檻,計時用 lease.acquired_at。
            if lease.owner is Owner.ONLINE_MONITOR:
                soft, hard = _MONITOR_STUCK_SOFT_SEC, _MONITOR_STUCK_HARD_SEC
                label = "線上監控"
            else:
                soft, hard = _BORROW_STUCK_SOFT_SEC, _BORROW_STUCK_HARD_SEC
                label = lease.owner.value
            age = now - float(lease.acquired_at)
            if age >= hard:
                registry.release(ip, lease.owner)
                logger_obj.warning(
                    f"[Watchdog] [{ip}] {label} 借用暫停卡 {age / 60:.0f} 分"
                    f"(>={hard // 60}m 硬門檻),強制釋放恢復")
            elif age >= soft:
                lease.preempted.set()
                logger_obj.info(
                    f"[Watchdog] [{ip}] {label} 借用暫停卡 {age / 60:.0f} 分"
                    f"(>={soft // 60}m 軟門檻),請 owner 讓位")
        except Exception as exc:  # noqa: BLE001 — 單台失敗不可中斷整輪
            logger_obj.warning(f"[Watchdog] stuck-pause sweep {ip} 失敗: {exc}")


def _special_wanshen_start_allowed(ip: str, cfg) -> bool:
    """萬神專用模式只在一次性排程到期時建立 thread。"""
    if not (bool(cfg.get("special_wanshen_account", False))
            and bool(cfg.get("special_wanshen_enabled", False))):
        return True
    from game_actions import special_wanshen

    return bool(special_wanshen.get_status(ip, cfg=cfg)["due"])


def _apply_adb_absence_rule(adb_present: set, running_threads: dict, logger_obj, now: Optional[float] = None, exempt: Optional[set] = None):
    """掉線超過 1 小時 → set_offline；回到清單 → 恢復 ONLINE / 解除重啟封鎖。

    只追蹤「曾出現在 raw ADB 掃描」的序號；web_h5 / ws_token 裝置從不進
    ADB 清單，自然不受影響。

    ``exempt`` 是 offline_fallback 裝置序號集合：這些是 backend=adb 的實體手機，
    上線時會出現在 raw ADB 掃描（因而進過 ``_adb_last_seen``），但離線時改跑純
    WS 掛機（new_main_v2 的 WS fallback 迴圈，「不放棄、不判離線」）。對這些序號
    絕不可因 ADB 缺席判離線，否則 dashboard 會在 1h 後誤標離線，甚至觸發 3h
    dead-device 重啟封鎖。
    """
    if now is None:
        now = time.time()
    exempt = exempt or set()

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
        if ip in exempt:
            # offline_fallback 裝置：純 WS 掛機中，絕不能因 ADB 缺席判離線。
            # 順手清掉殘留時間戳 + offlined 登記，避免日後關閉 fallback 時
            # 用舊 timestamp 立即觸發缺席規則。
            _adb_last_seen.pop(ip, None)
            _adb_absence_offlined.discard(ip)
            continue
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


def _is_ws_fallback_cfg(dcfg) -> bool:
    """True when an ADB device opts into the offline pure-WS fallback.

    Condition: backend == "adb" AND ws_token.enabled AND
    ws_token.offline_fallback. When the phone goes offline the ADB scan stops
    emitting its serial, so the serial must be injected from config like the
    ws_runner devices — otherwise the thread never spawns and WS never runs.
    """
    dcfg = dcfg or {}
    if str(dcfg.get("backend", "adb")).strip().lower() != "adb":
        return False
    ws_cfg = dcfg.get("ws_token") or {}
    return bool(ws_cfg.get("enabled", False)) and bool(ws_cfg.get("offline_fallback", False))


def get_ws_fallback_devices(logger_obj) -> list[str]:
    """ADB devices that should keep running pure-WS while offline.

    Mirrors ``get_ws_runner_devices``: these serials may be absent from the ADB
    scan (phone away), so they're injected from config. Disabled devices
    (enabled=false) are skipped; a missing key reads as enabled.

    Host gate (``ws_fallback_host_allowed``): master + worker share the same
    NAS-synced bot_config.json — without it BOTH hosts would inject the serial,
    and the host the phone never pairs with would loop init-fail -> WS fallback
    -> hourly WS login, kicking the phone session while the right host drives
    it normally. ``ws_token.fallback_host`` pins one hostname (case-insensitive);
    unset -> only the resolved-mode=="master" host injects.
    """
    try:
        # ws_fallback_service is import-light (sleep_service is lazy-loaded
        # inside it); lazy import here just avoids a top-level coupling.
        from runtime_services.ws_fallback_service import ws_fallback_host_allowed

        cfg = config_manager.load_config()
        devices = cfg.get("devices", {}) if isinstance(cfg, dict) else {}
        result = []
        for serial, dcfg in devices.items():
            if not _is_ws_fallback_cfg(dcfg):
                continue
            if not bool((dcfg or {}).get("enabled", True)):
                continue
            if not ws_fallback_host_allowed((dcfg or {}).get("ws_token")):
                continue
            result.append(serial)
        return sorted(set(result))
    except Exception as e:
        logger_obj.warning(f"[System] Failed to load ws fallback devices from config: {e}")
        return []


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

    fb_devices: set = set()
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

        # offline_fallback ADB devices: when the phone is unreachable the ADB
        # scan drops its serial, so inject it from config like ws_runner. The
        # device thread spawns, init connect fails, and new_main_v2 runs the
        # pure-WS wait round (see runtime_services.ws_fallback_service) instead
        # of dying. When the phone returns, init succeeds and full flow resumes.
        fb_devices = set(get_ws_fallback_devices(logger_obj))
        for fb_ip in fb_devices:
            if fb_ip not in current_devices:
                current_devices.append(fb_ip)

        if not allow_web_backend:
            skipped_web_devices = [d for d in current_devices if is_web_backend_device(d)]
            if skipped_web_devices:
                logger_obj.info(f"[System] 本機已停用 web_h5 啟動，略過設備: {sorted(skipped_web_devices)}")
            current_devices = [d for d in current_devices if not is_web_backend_device(d)]
    except Exception as e:
        print(f"[System] 掃描 ADB 失敗: {e}")
        return

    _apply_adb_absence_rule(adb_present, running_threads, logger_obj, exempt=fb_devices)

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
                device_cfg = config_manager.get_device_config(ip)
                if not _special_wanshen_start_allowed(ip, device_cfg):
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

    # 卡死暫停守望:自動救援被借用/手動暫停卡太久的本機裝置(不弄垮掃描迴圈)。
    _sweep_stuck_pauses(logger_obj)

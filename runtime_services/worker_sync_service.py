import threading
import time

import requests

import bot_state
import config_manager
from device import get_adb_devices
from worker_webhook_api import apply_remote_commands, normalize_master_url, resolve_worker_webhook_url


_worker_sync_thread_started = False


def _get_float(config: dict, key: str, default: float, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(config.get(key, default)))
    except Exception:
        return max(minimum, float(default))


def _worker_sync_loop():
    print("[WorkerSync] 啟動 Worker->Master 狀態同步執行緒")
    next_poll_at = 0.0
    next_report_at = 0.0
    while True:
        try:
            g = config_manager.get_global_config()
            if str(g.get("mode", "master")).lower() != "worker":
                time.sleep(2)
                continue
            master_url = normalize_master_url(g.get("master_url"))
            worker_id = str(g.get("worker_id", "unknown_worker")).strip() or "unknown_worker"
            if not master_url:
                time.sleep(2)
                continue
            webhook_url = resolve_worker_webhook_url(master_url, worker_id)
            sync_timeout_sec = _get_float(g, "worker_sync_timeout_sec", 10.0, minimum=1.0)
            failure_backoff_sec = _get_float(g, "worker_sync_failure_backoff_sec", 6.0, minimum=1.0)
            states = bot_state.get_all_states()
            try:
                adb_now = set(get_adb_devices())
            except Exception:
                adb_now = set()

            if adb_now:
                states = {ip: st for ip, st in states.items() if ip in adb_now}
            else:
                states = {}

            ips = sorted(states.keys())
            payload = {
                "__META__": {
                    "worker_id": worker_id,
                    "webhook_url": webhook_url,
                }
            }
            for ip, st in states.items():
                payload[f"{worker_id}:{ip}"] = {
                    "status": st.get("status"),
                    "task": st.get("task"),
                    "step": st.get("step"),
                    "next_wake_at": st.get("next_wake_at"),
                    "paused": st.get("paused", False),
                    "logs": st.get("logs", []),
                    "avg_screenshot_ms": st.get("avg_screenshot_ms"),
                }
            now = time.time()
            if now >= next_report_at:
                try:
                    requests.post(
                        f"{master_url}/api/report_status",
                        json=payload,
                        timeout=sync_timeout_sec,
                        verify=False,
                    )
                    next_report_at = now + 1.2
                except Exception as e:
                    print(f"[WorkerSync] 回報失敗: {e}")
                    next_report_at = now + failure_backoff_sec

            poll_interval = 6.0 if webhook_url else 1.2
            if now >= next_poll_at:
                next_poll_at = now + poll_interval
                try:
                    resp = requests.post(
                        f"{master_url}/api/poll_commands",
                        json={"worker_id": worker_id, "ips": ips},
                        timeout=sync_timeout_sec,
                        verify=False,
                    )
                    if resp.ok:
                        data = resp.json() if resp.content else {}
                        apply_remote_commands(ips, data.get("commands", {}))
                        if (data.get("global_commands") or {}).get("refresh_needed"):
                            bot_state.set_refresh_needed()
                except Exception as e:
                    print(f"[WorkerSync] 拉取指令失敗: {e}")
                    next_poll_at = now + failure_backoff_sec
        except Exception as e:
            print(f"[WorkerSync] 未預期錯誤: {e}")
        time.sleep(1.2)


def ensure_worker_sync_started():
    global _worker_sync_thread_started
    if _worker_sync_thread_started:
        return
    t = threading.Thread(target=_worker_sync_loop, daemon=True, name="worker-sync")
    t.start()
    _worker_sync_thread_started = True

import ssl
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

import bot_state
import config_manager
from device import get_adb_devices
from worker_webhook_api import apply_remote_commands, normalize_master_url, resolve_worker_webhook_url


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Per-device log ring buffer holds up to bot_state._MAX_DEVICE_LOGS (200) lines
# now that the file logger forwards every line. The master dashboard only renders
# the tail (cards: last 3; labeler/trainer panels: last 40/120), so shipping the
# whole buffer every ~1.2s would bloat each worker->master report. Send only the
# recent tail.
_WORKER_LOG_TAIL = 60

_worker_sync_thread_started = False


class _CaVerifyNoHostnameAdapter(HTTPAdapter):
    # OpenSSL 3.x 對 wildcard cert (*.domain.tld) 的 hostname matching 變嚴，
    # 導致 mushroom1_dashboard.infinite25741629.uk 被拒。
    # 此 adapter 保留 CA 憑證鏈驗證，僅跳過 hostname 比對。
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _get_session(url: str) -> requests.Session:
    """Returns a requests.Session configured for the TLS requirements of *url*."""
    s = requests.Session()
    if not url:
        return s
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
    except Exception:
        return s
    if scheme != "https":
        return s
    if host in _LOCAL_HOSTS:
        s.verify = False
        return s
    s.mount("https://", _CaVerifyNoHostnameAdapter())
    return s


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
                    "logs": (st.get("logs") or [])[-_WORKER_LOG_TAIL:],
                    "avg_screenshot_ms": st.get("avg_screenshot_ms"),
                }
            now = time.time()
            session = _get_session(master_url)
            if now >= next_report_at:
                try:
                    session.post(
                        f"{master_url}/api/report_status",
                        json=payload,
                        timeout=sync_timeout_sec,
                    )
                    next_report_at = now + 1.2
                except Exception as e:
                    print(f"[WorkerSync] 回報失敗: {e}")
                    next_report_at = now + failure_backoff_sec

            poll_interval = 6.0 if webhook_url else 1.2
            if now >= next_poll_at:
                next_poll_at = now + poll_interval
                try:
                    resp = session.post(
                        f"{master_url}/api/poll_commands",
                        json={"worker_id": worker_id, "ips": ips},
                        timeout=sync_timeout_sec,
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

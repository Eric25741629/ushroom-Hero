"""Master 模式的遠端指令佇列（唯一真相）。

`_remote_commands` / `_global_commands` / `_worker_webhook_endpoints` 與
`_commands_lock` 由 worker 同步路由與裝置控制路由共用——拆分後仍只存在
這一份；façade (control_panel_app) re-export 同一批物件給測試用。

⚠ 晚綁定規則：tests 會 monkeypatch ``control_panel_app._push_to_worker_webhook``
與 ``control_panel_app._push_remote_command_if_possible``，因此這裡呼叫
彼此時一律透過 façade 模組屬性查找，不可用模組內直接引用。
"""
import copy
import threading

import requests

import bot_state

# { "worker_id:ip": { "paused": True, "skip_sleep": False } }
_remote_commands = {}

# 全域指令 (發給所有 Worker)
_global_commands = {"refresh_needed": False}
_worker_webhook_endpoints = {}

# 保護上述三個 dict。Flask worker thread (poll_commands, report_status,
# queue_command) 與背景 timer thread (reset_flag) 並發存取。RLock 允許 nested
# critical section。HTTP I/O 一律在鎖外執行：先在鎖內 snapshot，釋放鎖後再
# requests.post。
_commands_lock = threading.RLock()


def _facade():
    """取 façade 模組以晚綁定可被 monkeypatch 的符號。"""
    import control_panel_app
    return control_panel_app


def _push_to_worker_webhook(worker_id: str, payload: dict) -> bool:
    # snapshot endpoint URL under lock，HTTP call 放在鎖外
    with _commands_lock:
        endpoint = (_worker_webhook_endpoints.get(worker_id) or {}).get("url", "")
    if not endpoint:
        return False
    try:
        resp = requests.post(endpoint, json=payload, timeout=1.8, verify=False)
        return bool(resp.ok)
    except Exception:
        return False


def _push_remote_command_if_possible(remote_id: str):
    if ":" not in remote_id:
        return
    worker_id, device_ip = remote_id.split(":", 1)
    # 鎖內 snapshot；HTTP push 與後續 one-shot 消費分開兩段鎖
    with _commands_lock:
        queued = _remote_commands.get(remote_id)
        if not isinstance(queued, dict) or not queued:
            return
        payload = {
            "worker_id": worker_id,
            "commands": {device_ip: copy.deepcopy(queued)},
            "global_commands": {"refresh_needed": _global_commands["refresh_needed"]},
        }
    pushed = _facade()._push_to_worker_webhook(worker_id, payload)
    if not pushed:
        return

    with _commands_lock:
        queued = _remote_commands.get(remote_id)
        if not isinstance(queued, dict):
            return
        # one-shot commands are consumed after successful push
        if "skip_sleep" in queued:
            del queued["skip_sleep"]
        if "recover" in queued:
            del queued["recover"]
        if "manual_release" in queued:
            del queued["manual_release"]
        if "force_sleep" in queued:
            del queued["force_sleep"]
        if "wake_delay_sec" in queued:
            del queued["wake_delay_sec"]


def _is_local_command_target(ip: str) -> bool:
    """Decide whether a control command targets a local device thread.

    Must NOT use ``":" in ip``: a TCP-attached local emulator (e.g.
    ``127.0.0.1:5555``) has a colon yet is local, and the old heuristic
    misrouted its pause / force-sleep commands to the remote-worker queue
    where nothing consumed them. Remote worker devices are keyed
    ``worker_id:ip`` and never register as a local thread, so the local
    registry is the reliable discriminator. The colon check survives only as
    a fallback for a local device whose thread has not registered yet.
    """
    if bot_state.is_local_device(ip):
        return True
    return ":" not in ip


def queue_command(ip, cmd_key, cmd_val):
    """將指令加入佇列 (針對遠端) 或直接執行 (針對本地)"""
    if not _is_local_command_target(ip):
        with _commands_lock:
            if ip not in _remote_commands:
                _remote_commands[ip] = {}
            _remote_commands[ip][cmd_key] = cmd_val
        _facade()._push_remote_command_if_possible(ip)
    else:
        # 本地設備
        if cmd_key == "paused":
            bot_state.set_pause(ip, cmd_val)
        elif cmd_key == "skip_sleep" and cmd_val:
            bot_state.set_skip_sleep(ip)
        elif cmd_key == "manual_release" and cmd_val:
            bot_state.set_manual_release(ip)
        elif cmd_key == "force_sleep" and cmd_val:
            bot_state.request_force_sleep(ip)
        elif cmd_key == "wake_delay_sec":
            bot_state.set_wake_override(ip, cmd_val)
        elif cmd_key == "recover" and cmd_val:
            # 本地直接執行 ADB (recover_screen 函數會處理)
            pass

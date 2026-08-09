"""Runtime/WS 中斷語意的特徵化測試。

所有測試都使用 fake state、fake client 或純 source pinning，不啟動真實
ADB、Playwright、WS server。重點是中斷不可被當成一般失敗，也不可讓
手動接管、worker 指令與本機裝置的語意分叉。
"""
from __future__ import annotations

import ast
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot_state


ROOT = Path(__file__).resolve().parents[1]


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        for table_name in (
            "_states", "_pause_events", "_signals", "_locks", "_wake_overrides",
            "_web_launch_requests",
        ):
            table = getattr(bot_state, table_name, None)
            if table is not None:
                table.pop(ip, None)
        local_ids = getattr(bot_state, "_local_device_ids", None)
        if local_ids is not None:
            local_ids.discard(ip)


def test_sleeping_device_wakes_immediately_on_skip_sleep(monkeypatch):
    """休眠中收到一次性喚醒，不能等到原本的 wake_ts。"""
    from runtime_services import device_runtime_service as runtime

    ip = "interrupt-sleep-skip"
    _cleanup(ip)
    bot_state.init_device(ip)
    try:
        monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(runtime.time, "time", lambda: 100.0)
        monkeypatch.setattr(runtime.bot_state, "check_force_sleep", lambda _ip: False)
        monkeypatch.setattr(runtime.bot_state, "check_pause", lambda _ip: None)
        monkeypatch.setattr(runtime.bot_state, "check_skip_sleep", lambda _ip: True)
        monkeypatch.setattr(runtime.bot_state, "has_pending_web_close_request", lambda _ip: False)
        monkeypatch.setattr(runtime.bot_state, "has_pending_web_launch_request", lambda _ip: False)
        monkeypatch.setattr(
            "runtime_services.wake_override_service.apply_manual_wake_override",
            lambda ip, wake_ts, logger_obj, task: (wake_ts, False),
        )

        assert runtime.sleep_until_wake_or_interrupt(ip, 10_000.0, logging.getLogger("test")) is True
    finally:
        _cleanup(ip)


def test_ws_abort_leaves_current_and_remaining_tasks_pending(monkeypatch):
    """WS 邊界中斷必須回報 aborted，不能把未執行 task 記成 errors。"""
    if "cv2" not in sys.modules:
        sys.modules["cv2"] = types.SimpleNamespace()
    from ws_token import runner

    class _Tracker:
        def __init__(self):
            self.counts = {}

        def on_push(self, *_args):
            pass

        def seed_from_query(self, *_args, **_kwargs):
            return 0

    class _Client:
        def __init__(self):
            self.closed = False

        def connect(self):
            return {"serv_time": 1, "role_id": 2}

        def is_kicked(self):
            return False

        def close(self):
            self.closed = True

    client = _Client()
    monkeypatch.setattr(runner, "load_creds", lambda _device: SimpleNamespace(login_time=0, role_id=0))
    monkeypatch.setattr(runner, "_make_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(runner.main_tasks, "TaskCollector", lambda: (lambda *_args: None))
    monkeypatch.setattr(runner.mining, "InventoryTracker", _Tracker)

    progress: list[tuple[str, str]] = []
    report = runner.run_device(
        "interrupt-ws",
        should_abort=lambda: True,
        progress=lambda name, status, detail="": progress.append((name, status)),
    )

    assert report.login_ok is True
    assert report.aborted is True
    assert report.tasks == {}
    assert report.errors == {}
    assert progress[0] == ("carpark", "aborted")
    assert client.closed is True


def test_pause_allows_manual_web_launch_to_take_over(monkeypatch):
    """暫停期間收到手動開網頁請求，check_pause 以 truthy 結果讓位。"""
    ip = "interrupt-manual-launch"
    _cleanup(ip)
    bot_state.init_device(ip)
    try:
        bot_state.set_pause(ip, True)
        bot_state.request_web_launch(ip, payload={"force_headful": True})

        assert bot_state.check_pause(ip) is True
        assert bot_state.is_paused(ip) is True
        assert bot_state.has_pending_web_launch_request(ip) is True
    finally:
        bot_state.set_pause(ip, False)
        _cleanup(ip)


class _ManualState:
    def __init__(self, *, release=False, web_close=False):
        self.release = release
        self.web_close = web_close
        self.updated: list[tuple[str, dict]] = []
        self.completed: list[tuple] = []
        self.browser_open: list[tuple] = []

    def consume_web_launch_request(self, _ip):
        return {"payload": {"manual_hold_until_closed": True, "force_headful": True}}

    def update_state(self, ip, **kwargs):
        self.updated.append((ip, kwargs))

    def complete_web_launch_request(self, ip, ok, message="", error=""):
        self.completed.append((ip, ok, message, error))

    def check_force_sleep(self, _ip):
        return False

    def check_manual_release(self, _ip):
        value, self.release = self.release, False
        return value

    def check_web_close(self, _ip):
        value, self.web_close = self.web_close, False
        return value

    def set_web_browser_open(self, ip, value):
        self.browser_open.append((ip, bool(value)))


class _ManualDevice:
    def __init__(self):
        self.app_start_calls = []
        self.close_calls = 0
        self.app_stop_calls = []
        self.restore_calls = []

    def app_start(self, *args, **kwargs):
        self.app_start_calls.append((args, kwargs))

    def is_alive(self):
        return True

    def close(self):
        self.close_calls += 1

    def app_stop(self, package):
        self.app_stop_calls.append(package)

    def restore_configured_headless_session(self, **kwargs):
        self.restore_calls.append(kwargs)


@pytest.fixture
def web_session_service(monkeypatch):
    stub_device_wrapper = types.ModuleType("device_wrapper")

    class _StubMonitoredDevice:
        def __init__(self, original, _ip):
            self._original = original

        def __getattr__(self, name):
            return getattr(self._original, name)

    stub_device_wrapper.MonitoredDevice = _StubMonitoredDevice
    stub_device_wrapper.close_all_web_devices = lambda logger_obj=None: None
    stub_device_wrapper.create_web_device_if_enabled = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "device_wrapper", stub_device_wrapper)
    monkeypatch.delitem(sys.modules, "runtime_services.web_session_service", raising=False)
    return importlib.import_module("runtime_services.web_session_service")


def test_manual_release_restores_configured_session(web_session_service, monkeypatch):
    state = _ManualState(release=True)
    device = _ManualDevice()
    monkeypatch.setattr(web_session_service, "bot_state", state)
    monkeypatch.setattr(web_session_service.time, "sleep", lambda _seconds: None)

    assert web_session_service.handle_pending_web_launch(
        "interrupt-manual-release", device, "web_h5", logging.getLogger("test")
    ) is True
    assert device.restore_calls == [{"reason": "manual web launch completed"}]
    assert any("手動操作已結束" in item[1].get("step", "") for item in state.updated)


def test_browser_close_does_not_stop_adb_device(web_session_service, monkeypatch):
    state = _ManualState(web_close=True)
    device = _ManualDevice()
    monkeypatch.setattr(web_session_service, "bot_state", state)
    monkeypatch.setattr(web_session_service.time, "sleep", lambda _seconds: None)

    assert web_session_service.handle_pending_web_launch(
        "interrupt-browser-close", device, "web_h5", logging.getLogger("test")
    ) is True
    assert device.close_calls == 1
    assert device.app_stop_calls == []


def test_login_conflict_has_three_runtime_sleep_entry_points():
    """初始化、WS fallback、運行中都沿用 runtime 30 分鐘 cooldown。"""
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename="new_main_v2.py")
    handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not (isinstance(node.type, ast.Name) and node.type.id == "LoginConflictError"):
            continue
        if any(
            isinstance(child, ast.Constant) and child.value == "runtime_login_conflict_30m"
            for child in ast.walk(node)
        ):
            handlers.append(node)

    assert len(handlers) == 3
    for handler in handlers:
        assert any(
            isinstance(child, ast.Name) and child.id in {"forced_wake_ts", "sleep_policy"}
            for child in ast.walk(handler)
        )
    # 運行中 handler 先設定 policy/reason，離開 try 後由共用結尾呼叫休眠。
    assert source.count("run_sleep_cycle(") >= 3


def test_master_worker_control_commands_have_the_same_state_effect(monkeypatch):
    """本機 queue 與 worker webhook 對同一組控制命令必須呼叫同一批 state API。"""
    if "uiautomator2" not in sys.modules:
        u2 = types.ModuleType("uiautomator2")
        u2.Device = object
        sys.modules["uiautomator2"] = u2
    from control_panel.shared import command_queue
    import worker_webhook_api

    ip = "interrupt-worker-device"
    _cleanup(ip)
    bot_state.init_device(ip)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(bot_state, "set_pause", lambda target, value: calls.append(("paused", (target, value))))
    monkeypatch.setattr(bot_state, "set_skip_sleep", lambda target: calls.append(("skip_sleep", target)))
    monkeypatch.setattr(bot_state, "set_manual_release", lambda target: calls.append(("manual_release", target)))
    monkeypatch.setattr(bot_state, "request_force_sleep", lambda target: calls.append(("force_sleep", target)))
    monkeypatch.setattr(bot_state, "set_wake_override", lambda target, value: calls.append(("wake_delay_sec", (target, value))))

    commands = {
        "paused": True,
        "skip_sleep": True,
        "manual_release": True,
        "force_sleep": True,
        "wake_delay_sec": 7.5,
    }
    try:
        for key, value in commands.items():
            command_queue.queue_command(ip, key, value)
        local_calls = list(calls)
        calls.clear()

        worker_webhook_api.apply_remote_commands([ip], {ip: commands})
        remote_calls = list(calls)
    finally:
        _cleanup(ip)

    assert remote_calls == local_calls

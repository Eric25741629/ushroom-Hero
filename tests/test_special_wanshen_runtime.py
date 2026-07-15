import ast
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import config_manager
from game_actions import browser_skip, task_due


ROOT = Path(__file__).resolve().parents[1]


def _load_ws_wake_function(namespace):
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_ws_phase_for_wake"
    )
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, "new_main_v2.py", "exec"), namespace)
    return namespace["_run_ws_phase_for_wake"]


def test_special_account_skips_ws_phase_after_acquiring_lease():
    calls = []
    namespace = {
        "acquire_scheduler_lease": lambda ip, logger_obj: calls.append("lease"),
        "config_manager": SimpleNamespace(
            get_device_config=lambda ip: {
                "special_wanshen_account": True,
                "special_wanshen_enabled": True,
                "ws_token": {"enabled": True},
            }
        ),
        "run_ws_phase": lambda *args, **kwargs: calls.append("ws") or frozenset({"x"}),
        "bot_state": SimpleNamespace(
            update_state=lambda *args, **kwargs: calls.append("state"),
            check_force_sleep=lambda ip: False,
        ),
        "ForceSleepRequested": RuntimeError,
    }
    run_ws_wake = _load_ws_wake_function(namespace)

    result = run_ws_wake("web-001", logging.getLogger("test"))

    assert result == frozenset()
    assert calls == ["lease"]


def test_special_account_never_skips_browser(monkeypatch):
    monkeypatch.setattr(
        config_manager,
        "get_device_config",
        lambda ip: {
            "special_wanshen_account": True,
            "special_wanshen_enabled": True,
            "backend": "web_h5",
            "skip_browser_when_all_done": True,
            "ws_token": {"enabled": True},
        },
    )
    monkeypatch.setattr(task_due, "any_client_due", lambda ip, now=None: False)

    assert browser_skip.should_skip_browser("web-001", ws_login_ok=True) is False


def test_full_mode_keeps_ws_phase_enabled():
    calls = []
    namespace = {
        "acquire_scheduler_lease": lambda ip, logger_obj: calls.append("lease"),
        "config_manager": SimpleNamespace(
            get_device_config=lambda ip: {
                "special_wanshen_account": True,
                "special_wanshen_enabled": False,
                "ws_token": {"enabled": True},
            }
        ),
        "run_ws_phase": lambda *args, **kwargs: calls.append("ws") or frozenset({"x"}),
        "bot_state": SimpleNamespace(
            update_state=lambda *args, **kwargs: calls.append("state"),
            check_force_sleep=lambda ip: False,
        ),
        "ForceSleepRequested": RuntimeError,
    }

    result = _load_ws_wake_function(namespace)("web-001", logging.getLogger("test"))

    assert result == frozenset({"x"})
    assert "ws" in calls


def test_full_mode_uses_normal_browser_skip_policy(monkeypatch):
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: {
            "special_wanshen_account": True,
            "special_wanshen_enabled": False,
            "backend": "web_h5",
            "skip_browser_when_all_done": True,
            "ws_token": {"enabled": True},
        },
    )
    monkeypatch.setattr(task_due, "any_client_due", lambda ip, now=None: False)

    assert browser_skip.should_skip_browser("web-001", ws_login_ok=True) is True


def test_scanner_starts_special_wanshen_only_when_due(monkeypatch):
    from game_actions import special_wanshen
    device_stub = types.ModuleType("device")
    device_stub.get_adb_devices = lambda: []
    monkeypatch.setitem(sys.modules, "device", device_stub)
    from runtime_services import device_scan_service

    cfg = {
        "enabled": True,
        "special_wanshen_account": True,
        "special_wanshen_enabled": True,
    }
    monkeypatch.setattr(
        special_wanshen, "get_status", lambda ip, cfg=None: {"due": False}
    )

    assert device_scan_service._special_wanshen_start_allowed(
        "web-001", cfg
    ) is False

    monkeypatch.setattr(
        special_wanshen, "get_status", lambda ip, cfg=None: {"due": True}
    )
    assert device_scan_service._special_wanshen_start_allowed(
        "web-001", cfg
    ) is True


def _load_main_function(namespace):
    """Compile just `main` from new_main_v2.py into a stub namespace.

    Executes the real control-flow bytecode of `main()` without importing the
    module (which pulls in cv2 / Playwright / torch). Any global the reached
    path touches but the namespace omits raises NameError — a loud failure,
    never a silent green — which is the whole point of driving behaviour.
    """
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, "new_main_v2.py", "exec"), namespace)
    return namespace["main"]


class _StubForceSleep(RuntimeError):
    pass


class _StubDevice:
    """Stands in for MonitoredDevice (isinstance check + screenshot)."""

    def __init__(self, *args, **kwargs):
        pass

    def screenshot(self, *args, **kwargs):
        return None


def _one_shot_cfg(backend):
    return {
        "special_wanshen_account": True,
        "special_wanshen_enabled": True,
        "backend": backend,
        "use_ws_runner": False,
    }


def _base_main_namespace(calls, cfg):
    """Namespace covering globals on the one-shot happy path through main()."""
    return {
        "config_manager": SimpleNamespace(get_device_config=lambda ip: cfg),
        "bot_state": SimpleNamespace(
            init_device=lambda ip: None,
            update_state=lambda *a, **k: None,
            set_web_browser_open=lambda ip, flag: calls.append(
                ("set_web_browser_open", flag)
            ),
            set_offline=lambda ip, reason=None: None,
            check_force_sleep=lambda ip: False,
            check_web_close=lambda ip: False,
            has_pending_web_launch_request=lambda ip: False,
            has_pending_force_sleep=lambda ip: False,
            is_paused=lambda ip: False,
            get_ws_login_ok=lambda ip: True,
        ),
        "logger": logging.getLogger("test-main"),
        "setup_logger_for_device": lambda ip: logging.getLogger("test-main"),
        "set_thread_logger": lambda logger_obj: None,
        "special_wanshen": SimpleNamespace(
            claim_if_due=lambda ip, cfg=None: calls.append("claim") or True
        ),
        "_handle_startup_sleep": lambda ip, logger_obj: None,
        "connect_u2_with_retries": lambda *a, **k: None,
        "reset_connect_failure": lambda ip: None,
        "run_sleep_cycle": lambda *a, **k: calls.append("sleep")
        or (0.0, False, 0.0),
        "stop_runtime_device_for_sleep": lambda *a, **k: calls.append("stop"),
        "ForceSleepRequested": _StubForceSleep,
    }


def test_one_shot_force_sleep_during_init_exits_and_clears_browser_flag():
    """One-shot + force-sleep during init: return without hourly sleep, and
    clear the web_browser_open dashboard flag (issue-2 regression)."""
    calls = []
    namespace = _base_main_namespace(calls, _one_shot_cfg("web_h5"))

    def _init(*args, **kwargs):
        calls.append("init")
        raise _StubForceSleep("force sleep during init")

    namespace["initialize_runtime_device"] = _init

    result = _load_main_function(namespace)("web-001", None, None, None, None)

    assert result is None
    # claim registered before init was attempted
    assert calls.index("claim") < calls.index("init")
    # exited without entering the generic hourly sleep loop
    assert "sleep" not in calls
    # dashboard browser-open flag cleared on the early exit (issue-2 fix)
    assert ("set_web_browser_open", False) in calls


def test_one_shot_exits_after_pipeline_without_hourly_sleep():
    """One-shot happy path: run daily_pipeline once, then return instead of
    entering the hourly sleep loop; the pipeline sees the claimed flag."""
    calls = []
    captured = {}
    cfg = _one_shot_cfg("adb")
    namespace = _base_main_namespace(calls, cfg)

    device = _StubDevice()
    namespace.update({
        "time": __import__("time"),
        "random": __import__("random"),
        "os": SimpleNamespace(path=__import__("os").path, makedirs=lambda *a, **k: None),
        "LogPaths": SimpleNamespace(safe_device_id=lambda ip: "web-001"),
        "DATASET_LOW_CONFIDENCE_DIR_STR": "dataset",
        "spin_wheel": lambda **k: SimpleNamespace(),
        "mission": lambda **k: SimpleNamespace(),
        "Family_manager": lambda **k: SimpleNamespace(),
        "state": lambda **k: SimpleNamespace(get_state=lambda: ""),
        "ClassifierCNN": lambda **k: SimpleNamespace(),
        "RLRecorder": lambda **k: SimpleNamespace(),
        "MonitoredDevice": _StubDevice,
        "initialize_runtime_device": lambda *a, **k: (device, device, "adb", False),
        "_run_ws_phase_for_wake": lambda ip, logger_obj: frozenset(),
        "handle_pending_web_launch": lambda *a, **k: False,
        "_maybe_resume_sleep": lambda *a, **k: (None, "", False),
        "should_skip_browser": lambda *a, **k: False,
        "resolve_skip_online_check_once": lambda *a, **k: False,
        "handle_device_wakeup": lambda d, *a, **k: device,
        "check_in_game": lambda d: True,
        "get_stage_with_check": lambda *a, **k: "戰鬥中",
        "daily_pipeline": SimpleNamespace(
            DailyContext=lambda **kw: SimpleNamespace(**kw),
            run=lambda ctx: captured.update(ctx=ctx),
        ),
    })

    result = _load_main_function(namespace)("web-001", None, None, None, None)

    assert result is None
    assert "ctx" in captured  # pipeline ran exactly once
    assert captured["ctx"].special_wanshen_claimed is True
    assert "sleep" not in calls  # never entered the hourly sleep loop

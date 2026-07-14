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


def test_one_shot_claim_happens_before_browser_initialization():
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    main_source = source[source.index("def main("):]

    assert main_source.index("special_wanshen.claim_if_due") < main_source.index(
        "initialize_runtime_device("
    )
    assert "special_wanshen_claimed=special_wanshen_claimed" in main_source


def test_one_shot_exits_instead_of_entering_hourly_sleep():
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    main_source = source[source.index("def main("):]

    assert "萬神一次性排程結束，關閉執行緒" in main_source
    assert "if special_wanshen_one_shot:" in main_source
    assert "萬神一次性排程不進入通用休眠" in main_source

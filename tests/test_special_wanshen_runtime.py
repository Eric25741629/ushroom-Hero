import ast
import logging
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
            "backend": "web_h5",
            "skip_browser_when_all_done": True,
            "ws_token": {"enabled": True},
        },
    )
    monkeypatch.setattr(task_due, "any_client_due", lambda ip, now=None: False)

    assert browser_skip.should_skip_browser("web-001", ws_login_ok=True) is False

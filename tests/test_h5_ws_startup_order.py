from __future__ import annotations

import ast
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _main_function() -> ast.FunctionDef:
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("main() not found")


def test_initialize_runtime_device_runs_hook_before_web_device_creation(monkeypatch):
    """h5+ws must run pure WS before Playwright opens the H5 session."""
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    from runtime_services import web_session_service as svc

    order: list[str] = []
    fake_web = SimpleNamespace()
    cfg = {"backend": "web_h5", "web_url": "https://example.invalid"}

    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda _ip: cfg)
    monkeypatch.setattr(svc.bot_state, "has_pending_web_launch_request", lambda _ip: False)
    monkeypatch.setattr(
        svc,
        "create_web_device_if_enabled",
        lambda *a, **k: order.append("create_web") or fake_web,
    )
    monkeypatch.setattr(
        svc,
        "MonitoredDevice",
        lambda device, ip: SimpleNamespace(device=device, ip=ip),
    )

    def before_web_start() -> None:
        order.append("ws_phase")

    d_orig, d, backend_kind, skip_online = svc.initialize_runtime_device(
        "web-ws",
        SimpleNamespace(info=lambda *a, **k: None),
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ADB must not be used")),
        before_web_device_start=before_web_start,
    )

    assert d_orig is fake_web
    assert d.device is fake_web
    assert backend_kind == "web_h5"
    assert skip_online is False
    assert order == ["ws_phase", "create_web"]


def test_main_passes_pre_web_start_hook_to_runtime_init():
    """The main wake loop must use the pre-web hook during first web_h5 init."""
    main_fn = _main_function()
    init_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call) and _name(node.func) == "initialize_runtime_device"
    ]
    assert init_calls, "initialize_runtime_device() call not found"
    assert any(
        any(keyword.arg == "before_web_device_start" for keyword in call.keywords)
        for call in init_calls
    ), "main() must pass before_web_device_start so h5+ws can run WS before opening H5"


def test_main_resolves_online_skip_after_ws_before_wakeup():
    main_fn = _main_function()
    resolver_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and _name(node.func) == "resolve_skip_online_check_once"
    ]
    assert resolver_calls, "main() must resolve the current WS-to-H5 handoff"

    wake_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call) and _name(node.func) == "handle_device_wakeup"
    ]
    skip_values = [
        keyword.value
        for call in wake_calls
        for keyword in call.keywords
        if keyword.arg == "skip_online_check_once"
    ]
    assert any(
        isinstance(value, ast.Name) and value.id == "skip_online_check_for_wakeup"
        for value in skip_values
    ), "handle_device_wakeup() must receive the resolved current-cycle value"


def test_main_refreshes_h5_credentials_when_existing_session_is_ready():
    """An already-open Playwright page must seed/refresh before daily tasks."""
    main_fn = _main_function()
    in_game_branches = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "in_game"
    ]
    assert in_game_branches, "main() in-game branch not found"
    assert any(
        isinstance(node, ast.Call)
        and _name(node.func) == "_refresh_h5_ws_credentials"
        for node in ast.walk(in_game_branches[0])
    ), "existing H5 sessions must refresh credentials after readiness"

"""Unit tests for startup/runtime popup cleanup."""
from __future__ import annotations

import importlib
import logging
import sys
import types


def _install_startup_import_stubs(monkeypatch):
    img_tools = types.ModuleType("img_tools")
    img_tools.click_str_by_server = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "img_tools", img_tools)

    tools = types.ModuleType("tools")
    tools.click_white = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "tools", tools)

    adb_operations = types.ModuleType("adb_operations")
    adb_operations.connect_u2_with_retries = lambda *args, **kwargs: None
    adb_operations.start_game_by_icon = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "adb_operations", adb_operations)

    detector = types.ModuleType("game_state.detector")
    detector.get_stage = lambda *args, **kwargs: "主頁面"
    monkeypatch.setitem(sys.modules, "game_state.detector", detector)

    reward_manager = types.ModuleType("game_actions.reward_manager")
    reward_manager.reward = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "game_actions.reward_manager", reward_manager)

    cnn_model = types.ModuleType("new_cnn.cnn_model")
    monkeypatch.setitem(sys.modules, "new_cnn.cnn_model", cnn_model)

    logging_utils = types.ModuleType("utils.logging_utils")
    logging_utils.logger = logging.getLogger("test_game_initialization")
    logging_utils.default_logger = logging_utils.logger
    monkeypatch.setitem(sys.modules, "utils.logging_utils", logging_utils)

    device_wrapper = types.ModuleType("device_wrapper")
    device_wrapper.create_web_device_if_enabled = lambda *args, **kwargs: None
    device_wrapper.get_alive_web_device = lambda *args, **kwargs: None
    device_wrapper.get_same_thread_web_device = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "device_wrapper", device_wrapper)

    # game_actions.navigation is imported lazily inside the "家族" handler
    # to avoid pulling in img_tools/playwright at module import time. The
    # tests stub it so the lazy import resolves without dragging real deps.
    navigation = types.ModuleType("game_actions.navigation")
    navigation._HOME_BTN = (321, 920)
    navigation._FARM_TAB = (480, 929)
    monkeypatch.setitem(sys.modules, "game_actions.navigation", navigation)


def _startup_module(monkeypatch):
    _install_startup_import_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "game_initialization", raising=False)
    return importlib.import_module("game_initialization")


class _FakeWebDevice:
    def __init__(self):
        self._page = object()
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _FakeCocosWebDevice(_FakeWebDevice):
    backend_kind = "web_h5"
    device_id = "7fe98fc6"


def test_carpark_warehouse_popup_uses_web_cleanup(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("claim"))
    monkeypatch.setattr(startup, "click_white", lambda _device: events.append("white"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    import utils.carpark_auto as carpark_auto

    monkeypatch.setattr(carpark_auto, "_close_carpark_transient_views", lambda page: events.append(("close", page)) or True)
    monkeypatch.setattr(carpark_auto, "_return_parking_to_main", lambda page: events.append(("main", page)) or True)

    assert startup._handle_known_stage_popup(device, "emulator-5554", "車位倉庫") is True
    assert events == ["claim", ("close", device._page), ("main", device._page)]


def test_h5_carpark_warehouse_uses_cocos_claim_without_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    import utils.carpark_auto as carpark_auto

    monkeypatch.setattr(carpark_auto, "claim_open_warehouse", lambda page: events.append(("cocos", page)) or True)
    monkeypatch.setattr(carpark_auto, "_close_carpark_transient_views", lambda page: events.append(("close", page)) or True)
    monkeypatch.setattr(carpark_auto, "_return_parking_to_main", lambda page: events.append(("main", page)) or True)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "車位倉庫") is True
    assert events == [("cocos", device._page), ("close", device._page), ("main", device._page)]


def test_h5_carpark_warehouse_cocos_failure_does_not_fallback_to_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    import utils.carpark_auto as carpark_auto

    monkeypatch.setattr(carpark_auto, "claim_open_warehouse", lambda _page: False)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "車位倉庫") is False
    assert events == []


def test_h5_offline_reward_uses_cocos_claim_without_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    reward_manager = sys.modules["game_actions.reward_manager"]
    monkeypatch.setattr(reward_manager, "claim_open_reward", lambda page: events.append(("cocos", page)) or True, raising=False)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "放置獎勵") is True
    assert events == [("cocos", device._page)]


def test_h5_offline_reward_cocos_failure_does_not_fallback_to_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    reward_manager = sys.modules["game_actions.reward_manager"]
    monkeypatch.setattr(reward_manager, "claim_open_reward", lambda _page: False, raising=False)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "放置獎勵") is False
    assert events == []


def test_h5_goods_reward_uses_cocos_close_without_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    reward_manager = sys.modules["game_actions.reward_manager"]
    monkeypatch.setattr(reward_manager, "close_goods_reward", lambda page: events.append(("cocos_close", page)) or True, raising=False)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "恭喜獲得") is True
    assert events == [("cocos_close", device._page)]


def test_h5_goods_reward_cocos_failure_does_not_fallback_to_ocr(monkeypatch):
    startup = _startup_module(monkeypatch)
    events = []
    device = _FakeCocosWebDevice()

    monkeypatch.setattr(startup.img_tools, "click_str_by_server", lambda *args, **kwargs: events.append("ocr"))
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)

    reward_manager = sys.modules["game_actions.reward_manager"]
    monkeypatch.setattr(reward_manager, "close_goods_reward", lambda _page: False, raising=False)

    assert startup._handle_known_stage_popup(device, "7fe98fc6", "恭喜獲得") is False
    assert events == []


def _install_cocos_navigator_stub(monkeypatch, return_value):
    """Stub utils.cocos_navigator.try_cocos_navigate before the lazy import.

    `return_value` may be True/False/None (the contract of try_cocos_navigate)
    or an Exception instance (raised when called).
    """
    calls = []

    def _try(d, ip, target):
        calls.append((d, ip, target))
        if isinstance(return_value, Exception):
            raise return_value
        return return_value

    stub = types.ModuleType("utils.cocos_navigator")
    stub.try_cocos_navigate = _try
    monkeypatch.setitem(sys.modules, "utils.cocos_navigator", stub)
    return calls


def test_family_stage_uses_cocos_navigation_when_successful(monkeypatch):
    startup = _startup_module(monkeypatch)
    device = _FakeWebDevice()
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    cocos_calls = _install_cocos_navigator_stub(monkeypatch, return_value=True)

    assert startup._handle_known_stage_popup(device, "emulator-5554", "家族") is True
    assert cocos_calls == [(device, "emulator-5554", "main")]
    # cocos succeeded → fallback tap must NOT fire.
    assert device.clicks == []


def test_family_stage_falls_back_to_home_tap_when_cocos_returns_false(monkeypatch):
    startup = _startup_module(monkeypatch)
    device = _FakeWebDevice()
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    cocos_calls = _install_cocos_navigator_stub(monkeypatch, return_value=False)

    assert startup._handle_known_stage_popup(device, "emulator-5554", "家族") is True
    assert cocos_calls == [(device, "emulator-5554", "main")]
    assert device.clicks == [(321, 920)]


def test_family_stage_falls_back_when_cocos_returns_none(monkeypatch):
    # None = cocos navigation flag disabled / device has no _page.
    # Caller must still try the coordinate fallback.
    startup = _startup_module(monkeypatch)
    device = _FakeWebDevice()
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    _install_cocos_navigator_stub(monkeypatch, return_value=None)

    assert startup._handle_known_stage_popup(device, "emulator-5554", "家族") is True
    assert device.clicks == [(321, 920)]


def test_family_stage_falls_back_when_cocos_raises(monkeypatch):
    startup = _startup_module(monkeypatch)
    device = _FakeWebDevice()
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    _install_cocos_navigator_stub(
        monkeypatch, return_value=RuntimeError("page closed mid-flight")
    )

    # Cocos raising must NOT propagate — recovery is best-effort.
    assert startup._handle_known_stage_popup(device, "emulator-5554", "家族") is True
    assert device.clicks == [(321, 920)]


def test_family_stage_fallback_swallows_click_errors(monkeypatch):
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    _install_cocos_navigator_stub(monkeypatch, return_value=False)

    class _ClickRaises:
        _page = object()

        def click(self, x, y):
            raise RuntimeError("device disconnected")

    # Even when fallback tap blows up the handler must still return True
    # so the resolve_stage_until_stable loop continues to its next probe
    # instead of leaking the exception to task code.
    assert startup._handle_known_stage_popup(_ClickRaises(), "emulator-5554", "家族") is True


def test_family_recovery_failure_terminates_at_max_chain(monkeypatch):
    """When 家族 stage persists across all max_chain attempts the resolver
    must terminate (return "家族") instead of looping forever.

    Without this guarantee a permanent guild-tab residue could spin the
    resolver indefinitely and starve the task loop. The contract is that
    resolve_stage_until_stable always returns within max_chain iterations
    regardless of how many times _handle_known_stage_popup reports True.
    """
    startup = _startup_module(monkeypatch)
    device = _FakeWebDevice()
    monkeypatch.setattr(startup.time, "sleep", lambda _sec: None)
    # Recovery is best-effort: fallback always fires, doesn't change state.
    _install_cocos_navigator_stub(monkeypatch, return_value=False)

    detect_calls = [0]

    def _always_family(*args, **kwargs):
        detect_calls[0] += 1
        return "家族"

    monkeypatch.setattr(startup, "get_stage", _always_family)

    result = startup.resolve_stage_until_stable(
        device, "emulator-5554", Cnn_model=None, max_chain=6
    )

    assert result == "家族"
    # Detector must have been called exactly max_chain times — no more,
    # no less. More = handler returned True but loop didn't exit. Less =
    # handler returned False (which would skip the resolve path entirely).
    assert detect_calls[0] == 6
    # Every iteration must have executed the recovery fallback tap.
    assert device.clicks == [(321, 920)] * 6

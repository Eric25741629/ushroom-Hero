"""Characterization tests for game_actions.stage_guard.

Written BEFORE the Phase 3 extraction. Locks:

  LoginConflictError
    - is an Exception subclass

  get_stage_with_check(d, ip, Cnn_model, img=None)
    - returns the stage produced by resolve_stage_until_stable on the
      happy path
    - when stage == "異地登錄": calls d.app_stop, calls
      mark_login_conflict_sleep(ip), raises LoginConflictError

  _run_at_main_page(d, ip, Cnn_model, task_name, mismatch_reason, fn, *,
                   step="執行中", log=None)
    - when stage == "主頁面": calls bot_state.update_state with
      task=task_name/step=step (and log=log when provided), invokes fn,
      returns "主頁面"
    - when stage != "主頁面": calls log_main_page_mismatch, does NOT
      call fn, returns the actual stage
"""
from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest


# Keep stubs local: do NOT stub `runtime_services` / `game_initialization`
# as packages (breaks other test files). Only stub leaf heavy deps.
for _name in ("opencc", "paddleocr", "img_tools"):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        if _name == "opencc":
            _m.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[_name] = _m

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

if "device" not in sys.modules:
    _dev = types.ModuleType("device")
    _dev.get_adb_devices = lambda *a, **k: []
    _dev.close_nofication = lambda *a, **k: None
    _dev.open_nofication = lambda *a, **k: None
    sys.modules["device"] = _dev

# Other test files (e.g. test_wake_loop_escape.py) stub `game_initialization`
# with only `check_on_line`. Top up the attribute stage_guard needs. If the
# stub is missing entirely, create one.
_gi = sys.modules.get("game_initialization")
if _gi is None:
    _gi = types.ModuleType("game_initialization")
    sys.modules["game_initialization"] = _gi
if not hasattr(_gi, "resolve_stage_until_stable"):
    _gi.resolve_stage_until_stable = lambda *a, **kw: "主頁面"
if not hasattr(_gi, "StartupLoginConflictError"):
    class _StartupLoginConflictError(Exception):
        pass
    _gi.StartupLoginConflictError = _StartupLoginConflictError
if not hasattr(_gi, "check_on_line"):
    _gi.check_on_line = lambda *a, **k: False
if not hasattr(_gi, "handle_game_startup_pages"):
    _gi.handle_game_startup_pages = lambda *a, **k: True


@pytest.fixture
def guard_mod():
    import importlib
    return importlib.import_module("game_actions.stage_guard")


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch, guard_mod):
    if getattr(guard_mod, "logger", None) is None:
        monkeypatch.setattr(guard_mod, "logger", logging.getLogger("test_stage_guard"))


@pytest.fixture
def fake_resolve(monkeypatch, guard_mod):
    """Control what resolve_stage_until_stable returns."""
    holder = {"stage": "主頁面"}

    def _resolve(d, ip, *, Cnn_model, reward_fn, logger, img=None):
        return holder["stage"]

    monkeypatch.setattr(guard_mod, "resolve_stage_until_stable", _resolve)
    return holder


@pytest.fixture
def fake_mark_conflict(monkeypatch, guard_mod):
    calls: list[str] = []
    monkeypatch.setattr(guard_mod, "mark_login_conflict_sleep", lambda ip: calls.append(ip))
    return calls


@pytest.fixture
def fake_bot_state(monkeypatch, guard_mod):
    calls: list[dict] = []
    monkeypatch.setattr(
        guard_mod.bot_state, "update_state",
        lambda ip, **kw: calls.append({"ip": ip, **kw}),
    )
    return calls


@pytest.fixture
def fake_mismatch(monkeypatch, guard_mod):
    calls: list[dict] = []

    def _log(device_obj, ip, stage, task, reason):
        calls.append({"ip": ip, "stage": stage, "task": task, "reason": reason})
        return "/tmp/shot.png"

    monkeypatch.setattr(guard_mod, "log_main_page_mismatch", _log)
    return calls


# ---------------------------------------------------------------------------
# LoginConflictError
# ---------------------------------------------------------------------------

def test_login_conflict_error_is_exception(guard_mod):
    assert issubclass(guard_mod.LoginConflictError, Exception)


# ---------------------------------------------------------------------------
# get_stage_with_check
# ---------------------------------------------------------------------------

def test_get_stage_with_check_returns_stage_on_happy_path(guard_mod, fake_resolve):
    fake_resolve["stage"] = "主頁面"
    d = SimpleNamespace(app_stop=lambda pkg: None)
    stage = guard_mod.get_stage_with_check(d, "emu-1", Cnn_model=object())
    assert stage == "主頁面"


def test_get_stage_with_check_raises_on_login_conflict(
    guard_mod, fake_resolve, fake_mark_conflict,
):
    fake_resolve["stage"] = "異地登錄"
    app_stop_calls: list[str] = []
    d = SimpleNamespace(app_stop=lambda pkg: app_stop_calls.append(pkg))

    with pytest.raises(guard_mod.LoginConflictError):
        guard_mod.get_stage_with_check(d, "emu-1", Cnn_model=object())

    assert app_stop_calls == ["com.mxdzz.tw.and"]
    assert fake_mark_conflict == ["emu-1"]


# ---------------------------------------------------------------------------
# _run_at_main_page
# ---------------------------------------------------------------------------

def test_run_at_main_page_invokes_fn_on_main_page(
    guard_mod, fake_resolve, fake_bot_state, fake_mismatch,
):
    fake_resolve["stage"] = "主頁面"
    fn_calls: list[int] = []
    d = SimpleNamespace(app_stop=lambda pkg: None)

    stage = guard_mod._run_at_main_page(
        d, "emu-1", Cnn_model=object(),
        task_name="測試任務", mismatch_reason="reason",
        fn=lambda: fn_calls.append(1),
    )

    assert stage == "主頁面"
    assert fn_calls == [1]
    assert fake_mismatch == []
    # default step is "執行中"
    assert any(c["task"] == "測試任務" and c["step"] == "執行中" for c in fake_bot_state)


def test_run_at_main_page_passes_custom_step_and_log(
    guard_mod, fake_resolve, fake_bot_state, fake_mismatch,
):
    fake_resolve["stage"] = "主頁面"
    d = SimpleNamespace(app_stop=lambda pkg: None)

    guard_mod._run_at_main_page(
        d, "emu-1", Cnn_model=object(),
        task_name="帶log任務", mismatch_reason="r", fn=lambda: None,
        step="自訂step", log="開始喔",
    )

    entry = next(c for c in fake_bot_state if c["task"] == "帶log任務")
    assert entry["step"] == "自訂step"
    assert entry["log"] == "開始喔"


def test_run_at_main_page_logs_mismatch_and_skips_fn_when_not_main_page(
    guard_mod, fake_resolve, fake_bot_state, fake_mismatch,
):
    fake_resolve["stage"] = "載入中"
    fn_calls: list[int] = []
    d = SimpleNamespace(app_stop=lambda pkg: None)

    stage = guard_mod._run_at_main_page(
        d, "emu-1", Cnn_model=object(),
        task_name="跳過任務", mismatch_reason="預期 reason",
        fn=lambda: fn_calls.append(1),
    )

    assert stage == "載入中"
    assert fn_calls == []  # fn never invoked
    assert len(fake_mismatch) == 1
    assert fake_mismatch[0]["task"] == "跳過任務"
    assert fake_mismatch[0]["reason"] == "預期 reason"

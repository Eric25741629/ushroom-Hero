"""Characterization tests for game_actions.daily_pipeline (Phase 7 — plan A).

These tests are written BEFORE the extraction and are expected to fail
until game_actions/daily_pipeline.py exists with the expected API.

API under test:

  DailyContext (dataclass)
    - packages the 10 parameters currently passed to _run_daily_tasks

  run(ctx: DailyContext) -> None
    - executes the per-wake 20-task sequence verbatim
    - calls the stage-guard helper (_run_at_main_page) enough times to
      drive the task sequence
    - emulator-5558 device-cleanup branch: calls switch_skill(d, '騙人用')
    - fc65396d device-cleanup branch: calls d.app_start('com.android.chrome')
"""
from __future__ import annotations

import logging
import sys
import time
import types
from types import SimpleNamespace

import pytest


# --- Heavy-dep stubs (copied from sibling test files) -------------------------
for _name in ("opencc", "paddleocr", "img_tools", "easyocr"):
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
    _dev.close_notification = lambda *a, **k: None
    _dev.open_notification = lambda *a, **k: None
    sys.modules["device"] = _dev

# Stub new_battle so we don't pull in heavy combat imports.
if "new_battle" not in sys.modules:
    _nb = types.ModuleType("new_battle")
    _nb.hell_door = lambda *a, **kw: None
    _nb.fight_test = lambda *a, **kw: None
    _nb.run_weekly_cloud_fighting_single = lambda *a, **kw: None
    _nb.run_biweekly_bounty_road_single = lambda *a, **kw: None
    sys.modules["new_battle"] = _nb

# Stub Open_gold_paddle_ocr + opengold_v2 so we don't pull in adb + playwright.
if "Open_gold_paddle_ocr" not in sys.modules:
    _gp = types.ModuleType("Open_gold_paddle_ocr")
    _gp.open_the_gold = lambda *a, **kw: None
    sys.modules["Open_gold_paddle_ocr"] = _gp

if "opengold_v2" not in sys.modules:
    sys.modules["opengold_v2"] = types.ModuleType("opengold_v2")
if "opengold_v2.lamp_service" not in sys.modules:
    _ls = types.ModuleType("opengold_v2.lamp_service")
    class _FakeLampSvc:
        def __init__(self, *a, **kw): pass
        def run(self, *a, **kw): pass
    _ls.LampService = _FakeLampSvc
    sys.modules["opengold_v2.lamp_service"] = _ls

# Stub daily_gift_task, Store, rank_events — task side effects.
if "daily_gift_task" not in sys.modules:
    _dgt = types.ModuleType("daily_gift_task")
    _dgt.buy_gift_for_friend_daily = lambda *a, **kw: None
    sys.modules["daily_gift_task"] = _dgt

if "Store" not in sys.modules:
    _st = types.ModuleType("Store")
    _st.buy_store = lambda *a, **kw: None
    sys.modules["Store"] = _st

if "rank_events" not in sys.modules:
    _re = types.ModuleType("rank_events")
    _re.park_spring = lambda *a, **kw: None
    sys.modules["rank_events"] = _re

# farm_v2 module (manager imported as farm_manager by daily_pipeline & new_main_v2).
if "farm_v2" not in sys.modules:
    _farm = types.ModuleType("farm_v2")
    _farm.manager = SimpleNamespace(farm=lambda *a, **kw: None)
    sys.modules["farm_v2"] = _farm
    sys.modules["farm_v2.manager"] = _farm.manager

# everyday_mission.Guardian_Spirit_manger.
if "everyday_mission" not in sys.modules:
    sys.modules["everyday_mission"] = types.ModuleType("everyday_mission")
if "everyday_mission.Guardian_Spirit_manger" not in sys.modules:
    _gs = types.ModuleType("everyday_mission.Guardian_Spirit_manger")
    _gs.get_Guardian_Spirit = lambda *a, **kw: None
    sys.modules["everyday_mission.Guardian_Spirit_manger"] = _gs

# Skill module (star import in new_main_v2.py brings get_skill_and_partner).
if "Skill" not in sys.modules:
    _sk = types.ModuleType("Skill")
    _sk.get_skill_and_partner = lambda *a, **kw: None
    sys.modules["Skill"] = _sk

# Sea module (sea function for Task 14).
if "Sea" not in sys.modules:
    _sea = types.ModuleType("Sea")
    _sea.sea = lambda *a, **kw: None
    sys.modules["Sea"] = _sea


@pytest.fixture
def pipeline_mod():
    import importlib
    return importlib.import_module("game_actions.daily_pipeline")


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch, pipeline_mod):
    if getattr(pipeline_mod, "logger", None) is None:
        monkeypatch.setattr(pipeline_mod, "logger", logging.getLogger("test_daily_pipeline"))


@pytest.fixture
def fake_all(monkeypatch, pipeline_mod):
    """Neutralize every side effect so run(ctx) does not need hardware."""
    calls: dict[str, list] = {
        "at_main_page": [],
        "stage_check": [],
        "lamp_if_due": [],
        "weekly_dungeon": [],
        "biweekly_dungeon": [],
        "bot_state": [],
        "switch_skill": [],
        "mismatch": [],
        "app_start": [],
        "app_stop": [],
    }

    # Always pretend we are on 主頁面 so each task body runs.
    def _stage_check(d, ip, Cnn_model, **kw):
        calls["stage_check"].append({"ip": ip})
        return "主頁面"

    def _at_main_page(d, ip, Cnn_model, task_name, mismatch_reason, fn, *, step="執行中", log=None):
        calls["at_main_page"].append({"task": task_name, "step": step})
        try:
            fn()
        except Exception:
            pass
        return "主頁面"

    def _lamp(d, ip, stage):
        calls["lamp_if_due"].append({"ip": ip, "stage": stage})

    def _weekly(d, ip, stage, enable_dungeon_manager, current_time):
        calls["weekly_dungeon"].append({"ip": ip})

    def _biweekly(d, ip, stage, enable_dungeon_manager, now_local):
        calls["biweekly_dungeon"].append({"ip": ip})

    monkeypatch.setattr(pipeline_mod, "get_stage_with_check", _stage_check)
    monkeypatch.setattr(pipeline_mod, "_run_at_main_page", _at_main_page)
    monkeypatch.setattr(pipeline_mod, "_run_lamp_if_due", _lamp)
    monkeypatch.setattr(pipeline_mod, "_run_weekly_dungeon", _weekly)
    monkeypatch.setattr(pipeline_mod, "_run_biweekly_dungeon", _biweekly)

    # The module-load `if "new_battle" not in sys.modules` stub at the top of
    # this file is no-op'd if a sibling test (test_biweekly_scheduler) loaded
    # the real new_battle first. Patch the combat functions per-test on the
    # real module so they don't drive the SimpleNamespace device into
    # AttributeError.  monkeypatch reverts on test teardown.
    for _fn in ("hell_door", "fight_test", "run_weekly_cloud_fighting_single",
                "run_biweekly_bounty_road_single"):
        if hasattr(pipeline_mod.new_battle, _fn):
            monkeypatch.setattr(pipeline_mod.new_battle, _fn, lambda *a, **kw: None)
        else:
            monkeypatch.setattr(pipeline_mod.new_battle, _fn, lambda *a, **kw: None, raising=False)

    monkeypatch.setattr(
        pipeline_mod.bot_state, "update_state",
        lambda ip, **kw: calls["bot_state"].append({"ip": ip, **kw}),
    )

    monkeypatch.setattr(
        pipeline_mod, "switch_skill",
        lambda d, *args, **kw: calls["switch_skill"].append({"args": args, "kw": kw}),
    )

    monkeypatch.setattr(
        pipeline_mod, "log_main_page_mismatch",
        lambda d, ip, stage, task, reason: calls["mismatch"].append({"task": task, "stage": stage}),
    )
    monkeypatch.setattr(
        pipeline_mod, "save_error_screenshot",
        lambda d, ip, stage, reason: None,
    )

    # Neutralize side effects of record/state helpers.
    monkeypatch.setattr(pipeline_mod, "return_time", lambda ip, name=None: None)
    monkeypatch.setattr(pipeline_mod, "time_recording", lambda ip, name=None: None)
    monkeypatch.setattr(pipeline_mod, "is_record_expired", lambda rec, sec: True)

    # Neutralize daily task helpers.
    monkeypatch.setattr(pipeline_mod, "daily_acceleration", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline_mod, "click_arena_challenges", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline_mod, "oracle", lambda *a, **kw: None)

    # reward is imported lazily in stage_guard; pipeline doesn't import it at top.
    # farm_manager.farm, rank_events.park_spring already stubbed in sys.modules.

    return calls


def _fake_device():
    """Lightweight device double with the methods daily pipeline may touch."""
    dev = SimpleNamespace()
    dev.tap = lambda x, y: None
    dev.app_start = lambda pkg: None
    dev.app_stop = lambda pkg: None
    return dev


def _build_ctx(pipeline_mod, ip, *, current_time=None):
    if current_time is None:
        current_time = time.strptime("2026-04-21 10:00:00", "%Y-%m-%d %H:%M:%S")
    d = _fake_device()
    # Patch app_start/app_stop to record via the fake_all fixture's call dict
    # — caller can attach trackers via SimpleNamespace swap if needed.
    return pipeline_mod.DailyContext(
        d=d,
        ip=ip,
        Cnn_model=SimpleNamespace(),
        clf=SimpleNamespace(),
        rl_recorder=SimpleNamespace(),
        current_time=current_time,
        enable_dungeon_manager=True,
        wheel_manager=SimpleNamespace(spin_and_send_gold=lambda: True),
        mission_manager=SimpleNamespace(do_allmission=lambda: None),
        family_manager=SimpleNamespace(go_to_family=lambda: None),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_daily_context_can_be_instantiated(pipeline_mod):
    """DailyContext accepts all 10 fields we care about."""
    ctx = _build_ctx(pipeline_mod, "emulator-5554")
    assert ctx.ip == "emulator-5554"
    assert ctx.enable_dungeon_manager is True
    # Structural fields present (no AttributeError).
    assert hasattr(ctx, "d")
    assert hasattr(ctx, "Cnn_model")
    assert hasattr(ctx, "clf")
    assert hasattr(ctx, "rl_recorder")
    assert hasattr(ctx, "current_time")
    assert hasattr(ctx, "wheel_manager")
    assert hasattr(ctx, "mission_manager")
    assert hasattr(ctx, "family_manager")


def test_run_invokes_stage_guard_helpers_repeatedly(pipeline_mod, fake_all):
    """run(ctx) should drive the task sequence via _run_at_main_page and
    _run_lamp_if_due at least once each (smoke-level)."""
    ctx = _build_ctx(pipeline_mod, "emulator-5554")
    pipeline_mod.run(ctx)
    # Many tasks route via _run_at_main_page; at minimum multiple calls.
    assert len(fake_all["at_main_page"]) >= 5
    # Task 19: lamp scheduler runs once per cycle.
    assert len(fake_all["lamp_if_due"]) == 1
    # Task 15 / 17 dungeon helpers each fire once.
    assert len(fake_all["weekly_dungeon"]) == 1
    assert len(fake_all["biweekly_dungeon"]) == 1


def test_run_triggers_emulator_5558_cleanup_branch(pipeline_mod, fake_all):
    """emulator-5558 must receive a switch_skill(d, '騙人用') cleanup call."""
    ctx = _build_ctx(pipeline_mod, "emulator-5558")
    pipeline_mod.run(ctx)
    # We expect at least one switch_skill call with '騙人用' as a positional arg.
    found = any(
        call["args"] == ("騙人用",)
        for call in fake_all["switch_skill"]
    )
    assert found, f"expected switch_skill(d, '騙人用') call; got {fake_all['switch_skill']}"


def test_run_triggers_fc65396d_chrome_branch(pipeline_mod, fake_all, monkeypatch):
    """fc65396d device cleanup must call d.app_start('com.android.chrome')."""
    ctx = _build_ctx(pipeline_mod, "adb-fc65396d")
    started: list[str] = []
    stopped: list[str] = []
    ctx.d.app_start = lambda pkg: started.append(pkg)
    ctx.d.app_stop = lambda pkg: stopped.append(pkg)

    # Skip real sleeps that the fc65396d branch invokes.
    monkeypatch.setattr(pipeline_mod.time, "sleep", lambda *_: None)

    pipeline_mod.run(ctx)
    assert "com.android.chrome" in started
    # It also stops the game app in the cleanup branch.
    assert "com.mxdzz.tw.and" in stopped


def test_run_recovers_from_consecutive_mismatch_abort(pipeline_mod, fake_all, monkeypatch):
    """When 4+ consecutive tasks land off the main page, run() must force-stop
    the game app and return *cleanly* — never let the abort escape.

    Regression: the consecutive-mismatch abort raised inside _run_tasks used to
    propagate out of run() (nothing caught it), so new_main_v2's outer handler
    logged it as '未預期錯誤' and tore the device thread down (set_offline +
    scanner respawn → immediate re-run, no sleep). The contract is the opposite:
    run() swallows the abort so the caller's wake loop sleeps and re-wakes at the
    next aligned window.
    """
    # Force every stage probe off the main page so the streak reaches the
    # abort threshold (4) at Task 7 (商店).
    monkeypatch.setattr(
        pipeline_mod, "get_stage_with_check",
        lambda d, ip, Cnn_model, **kw: "未知",
    )

    def _off_page(d, ip, Cnn_model, task_name, mismatch_reason, fn, *, step="執行中", log=None):
        # Real _run_at_main_page skips fn() when off the main page; mirror that.
        fake_all["at_main_page"].append({"task": task_name})
        return "未知"
    monkeypatch.setattr(pipeline_mod, "_run_at_main_page", _off_page)

    stopped: list[str] = []
    ctx = _build_ctx(pipeline_mod, "emulator-5554")
    ctx.d.app_stop = lambda pkg: stopped.append(pkg)

    try:
        pipeline_mod.run(ctx)
    except Exception as exc:  # noqa: BLE001 — the bug is precisely an escaping exception
        pytest.fail(
            "run() must recover from the consecutive-mismatch abort and return, "
            f"but it raised {type(exc).__name__}: {exc}"
        )

    # Recovery action: the game app was force-stopped before bailing out.
    assert "com.mxdzz.tw.and" in stopped, "abort must force-stop the game app"
    # The pipeline aborted early — tail tasks never ran.
    assert fake_all["lamp_if_due"] == [], "lamp (Task 19) must not run after abort"
    assert fake_all["weekly_dungeon"] == [], "weekly dungeon (Task 15) must not run after abort"
    assert fake_all["biweekly_dungeon"] == [], "biweekly dungeon (Task 17) must not run after abort"

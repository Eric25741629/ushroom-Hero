"""Characterization tests for game_actions.dungeon_scheduler (Phase 4).

Written BEFORE extraction. Locks:

  _run_weekly_dungeon (萬神試煉):
    - no-op when enable_dungeon_manager is False
    - no-op on Sunday (tm_wday == 6)
    - runs on Monday afternoon (tm_wday==0 and tm_hour > 12)
    - runs on Tue-Fri (tm_wday in 1..5)
    - runs when record is None OR record.is_next_week is True
    - skips when record.is_next_week is False
    - records time + updates state + calls fight_test on main page
    - logs mismatch off main page

  _run_biweekly_dungeon (雙週副本):
    - only for emulator-5556
    - only Sat/Sun at 20:xx
    - runs when record is None OR record.is_next_biweek is True
    - skips when record.is_next_biweek is False
    - main-page guard logs mismatch off main page
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
    _dev.close_nofication = lambda *a, **k: None
    _dev.open_nofication = lambda *a, **k: None
    sys.modules["device"] = _dev

# new_battle is heavy — stub so we can observe calls.
if "new_battle" not in sys.modules:
    _nb = types.ModuleType("new_battle")
    _nb.fight_test = lambda *a, **kw: None
    _nb.run_biweekly_bounty_road_single = lambda *a, **kw: None
    sys.modules["new_battle"] = _nb


@pytest.fixture
def dungeon_mod():
    import importlib
    return importlib.import_module("game_actions.dungeon_scheduler")


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch, dungeon_mod):
    if getattr(dungeon_mod, "logger", None) is None:
        monkeypatch.setattr(dungeon_mod, "logger", logging.getLogger("test_dungeon"))


@pytest.fixture
def fake_bot_state(monkeypatch, dungeon_mod):
    calls: list[dict] = []
    monkeypatch.setattr(
        dungeon_mod.bot_state, "update_state",
        lambda ip, **kw: calls.append({"ip": ip, **kw}),
    )
    monkeypatch.setattr(dungeon_mod.bot_state, "check_pause", lambda ip: False)
    monkeypatch.setattr(dungeon_mod.bot_state, "check_skip_sleep", lambda ip: False)
    return calls


@pytest.fixture
def fake_battle(monkeypatch, dungeon_mod):
    calls: list[dict] = []

    def _fight_test(d):
        calls.append({"fn": "fight_test", "d": d})

    def _biweekly(d, ip, *, logger_obj, should_stop):
        calls.append({"fn": "biweekly", "ip": ip})

    monkeypatch.setattr(dungeon_mod.new_battle, "fight_test", _fight_test)
    monkeypatch.setattr(dungeon_mod.new_battle, "run_biweekly_bounty_road_single", _biweekly)
    return calls


@pytest.fixture
def fake_records(monkeypatch, dungeon_mod):
    """Control return_time() output per record name."""
    state: dict[str, dict | None] = {}

    def _return(ip, name):
        return state.get(name)

    monkeypatch.setattr(dungeon_mod, "return_time", _return)
    recorded: list[dict] = []
    monkeypatch.setattr(dungeon_mod, "time_recording", lambda ip, name: recorded.append({"ip": ip, "name": name}))
    state["_recorded"] = recorded  # stash for easy test access
    return state


@pytest.fixture
def fake_mismatch(monkeypatch, dungeon_mod):
    calls: list[dict] = []
    monkeypatch.setattr(
        dungeon_mod, "log_main_page_mismatch",
        lambda d, ip, stage, task, reason: calls.append({"ip": ip, "task": task, "stage": stage}),
    )
    return calls


def _tuesday_10am():
    """struct_time for Tuesday 10:00."""
    return time.strptime("2026-04-21 10:00:00", "%Y-%m-%d %H:%M:%S")


def _sunday_10am():
    return time.strptime("2026-04-26 10:00:00", "%Y-%m-%d %H:%M:%S")


def _monday_10am():
    return time.strptime("2026-04-20 10:00:00", "%Y-%m-%d %H:%M:%S")


def _monday_2pm():
    return time.strptime("2026-04-20 14:00:00", "%Y-%m-%d %H:%M:%S")


def _saturday_8pm():
    return time.strptime("2026-04-25 20:05:00", "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# _run_weekly_dungeon
# ---------------------------------------------------------------------------

def test_weekly_runs_on_tuesday_with_no_record(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=True, current_time=_tuesday_10am(),
    )
    assert any(c["fn"] == "fight_test" for c in fake_battle)
    assert fake_records["_recorded"] == [{"ip": "emu-1", "name": "萬神試煉"}]
    assert fake_mismatch == []


def test_weekly_skipped_when_dungeon_manager_disabled(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=False, current_time=_tuesday_10am(),
    )
    assert fake_battle == []
    assert fake_records["_recorded"] == []


def test_weekly_skipped_on_sunday(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=True, current_time=_sunday_10am(),
    )
    assert fake_battle == []


def test_weekly_skipped_on_monday_morning(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    # Monday 10 AM — must be tm_hour > 12, so this should NOT run
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=True, current_time=_monday_10am(),
    )
    assert fake_battle == []


def test_weekly_runs_on_monday_afternoon(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=True, current_time=_monday_2pm(),
    )
    assert any(c["fn"] == "fight_test" for c in fake_battle)


def test_weekly_skipped_when_record_is_not_next_week(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    fake_records["萬神試煉"] = {"is_next_week": False}
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "主頁面",
        enable_dungeon_manager=True, current_time=_tuesday_10am(),
    )
    assert fake_battle == []


def test_weekly_logs_mismatch_when_off_main_page(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_weekly_dungeon(
        SimpleNamespace(), "emu-1", "載入中",
        enable_dungeon_manager=True, current_time=_tuesday_10am(),
    )
    assert fake_battle == []
    assert len(fake_mismatch) == 1
    assert fake_mismatch[0]["task"] == "萬神試煉"


# ---------------------------------------------------------------------------
# _run_biweekly_dungeon
# ---------------------------------------------------------------------------

def test_biweekly_only_runs_for_5556(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_biweekly_dungeon(
        SimpleNamespace(), "emulator-5554", "主頁面",
        enable_dungeon_manager=True, now_local=_saturday_8pm(),
    )
    assert fake_battle == []


def test_biweekly_runs_on_saturday_20xx_with_no_record(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_biweekly_dungeon(
        SimpleNamespace(), "emulator-5556", "主頁面",
        enable_dungeon_manager=True, now_local=_saturday_8pm(),
    )
    assert any(c["fn"] == "biweekly" for c in fake_battle)
    assert fake_records["_recorded"] == [{"ip": "emulator-5556", "name": "雙週副本"}]


def test_biweekly_skipped_when_not_next_biweek(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    fake_records["雙週副本"] = {"is_next_biweek": False}
    dungeon_mod._run_biweekly_dungeon(
        SimpleNamespace(), "emulator-5556", "主頁面",
        enable_dungeon_manager=True, now_local=_saturday_8pm(),
    )
    assert fake_battle == []


def test_biweekly_logs_mismatch_when_off_main_page(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    dungeon_mod._run_biweekly_dungeon(
        SimpleNamespace(), "emulator-5556", "載入中",
        enable_dungeon_manager=True, now_local=_saturday_8pm(),
    )
    assert fake_battle == []
    assert len(fake_mismatch) == 1
    assert fake_mismatch[0]["task"] == "雙週副本"


def test_biweekly_skipped_outside_20xx_window(
    dungeon_mod, fake_bot_state, fake_battle, fake_records, fake_mismatch,
):
    # Saturday 19:59 — 20-hour window not yet, so skip
    t = time.strptime("2026-04-25 19:59:00", "%Y-%m-%d %H:%M:%S")
    dungeon_mod._run_biweekly_dungeon(
        SimpleNamespace(), "emulator-5556", "主頁面",
        enable_dungeon_manager=True, now_local=t,
    )
    assert fake_battle == []

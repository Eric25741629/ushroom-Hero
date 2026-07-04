"""Gate-logic tests for the 煩惱消 (act_type 224) once-daily H5 task.

Covers `game_actions.fannaoxiao_scheduler.run_fannaoxiao_if_due`:
  - flag OFF (enable_fannaoxiao False)        -> skip (driver never called)
  - wrong backend (adb)                        -> skip
  - already ran today (record not expired)     -> skip
  - due + enabled + web_h5                      -> runs ONE bounded game, records date
  - no live page                               -> skip
  - 0-move run                                 -> does NOT record (retry next cycle)

No real Playwright page: a FakeDriver is injected via the `driver=` param, and
config / json_manager state are monkeypatched. `utils.pause_guard` is exercised
for real (bind/unbind on a fake page are inert).
"""
from __future__ import annotations

import sys
import types

import pytest

from game_actions import fannaoxiao_scheduler as sched


# ── fakes ──────────────────────────────────────────────────────────────


class FakePage:
    """A page object with no JS engine — pause_guard.bind snapshots it but the
    scheduler never calls page.evaluate directly (the driver does, and we inject
    a fake driver)."""

    def evaluate(self, *a, **k):
        return None


class FakeDevice:
    def __init__(self, page=None):
        self._page = page


class FakeDriver:
    """Stand-in for game_actions.fannaoxiao_driver. Records calls and returns a
    canned play summary."""

    def __init__(self, *, enter_ok=True, summary=None):
        self.enter_ok = enter_ok
        self.summary = summary if summary is not None else {
            "moves": 12, "score": 30, "level": 2, "rows": 4, "stopped": "game_over",
        }
        self.enter_calls = 0
        self.play_calls = 0

    def enter_game(self, page, settle_sec=3.0):
        self.enter_calls += 1
        return self.enter_ok

    def play_bounded(self, page, max_moves=40, move_pause=0.4):
        self.play_calls += 1
        self.last_max_moves = max_moves
        return self.summary


class FakeCfg(dict):
    """DeviceConfig-like: dict with .get already; nothing else needed."""


# ── monkeypatch helpers ────────────────────────────────────────────────


@pytest.fixture
def patch_env(monkeypatch):
    """Returns a configure(enable, backend, record) -> (recorded list) helper.

    `record` is the json_manager record dict (None = never run). `recorded`
    captures time_recording(name=...) calls so a test can assert the date was
    (or wasn't) stamped.
    """
    state = {"enable": False, "backend": "web_h5", "record": None}
    recorded: list = []

    def fake_get_device_config(ip):
        return FakeCfg(enable_fannaoxiao=state["enable"], backend=state["backend"])

    def fake_return_time(ip, name=""):
        return state["record"]

    def fake_is_record_expired(record, seconds, **kw):
        # Mirror real semantics enough for the gate: None -> expired (due).
        return record is None

    def fake_time_recording(ip, name=""):
        recorded.append(name)

    monkeypatch.setattr(sched.config_manager, "get_device_config", fake_get_device_config)
    monkeypatch.setattr(sched, "return_time", fake_return_time)
    monkeypatch.setattr(sched, "is_record_expired", fake_is_record_expired)
    monkeypatch.setattr(sched, "time_recording", fake_time_recording)

    def configure(*, enable, backend="web_h5", record=None):
        state["enable"] = enable
        state["backend"] = backend
        state["record"] = record

    return configure, recorded


# ── tests ──────────────────────────────────────────────────────────────


def test_skip_when_flag_off(patch_env):
    configure, recorded = patch_env
    configure(enable=False, backend="web_h5", record=None)
    drv = FakeDriver()
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0
    assert drv.play_calls == 0
    assert recorded == []


def test_skip_when_wrong_backend(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="adb", record=None)
    drv = FakeDriver()
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0
    assert drv.play_calls == 0
    assert recorded == []


def test_skip_when_already_ran_today(patch_env):
    configure, recorded = patch_env
    # A non-None record + fake_is_record_expired(None->True else False) => not due.
    configure(enable=True, backend="web_h5", record={"is_next_day": False, "timestamp": 1})
    drv = FakeDriver()
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0
    assert drv.play_calls == 0
    assert recorded == []


def test_runs_once_when_due(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="web_h5", record=None)
    drv = FakeDriver()
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 1
    assert drv.play_calls == 1
    # bounded: the scheduler must pass a finite max_moves to the driver
    assert drv.last_max_moves == sched._MAX_MOVES
    # recorded the date on a >0-move run
    assert recorded == [sched._RECORD_KEY]


def test_skip_when_no_live_page(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="web_h5", record=None)
    drv = FakeDriver()
    sched.run_fannaoxiao_if_due(FakeDevice(page=None), "dev1", driver=drv)
    assert drv.enter_calls == 0
    assert drv.play_calls == 0
    assert recorded == []


def test_does_not_record_when_enter_fails(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="web_h5", record=None)
    drv = FakeDriver(enter_ok=False)
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 1
    assert drv.play_calls == 0  # never reached play
    assert recorded == []  # not recorded -> retries next cycle


def test_does_not_record_on_zero_move_run(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="web_h5", record=None)
    drv = FakeDriver(summary={"moves": 0, "stopped": "no_move"})
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.play_calls == 1
    assert recorded == []  # 0 moves -> not recorded


def test_exception_in_driver_is_swallowed(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="web_h5", record=None)

    class BoomDriver(FakeDriver):
        def play_bounded(self, page, max_moves=40, move_pause=0.4):
            raise RuntimeError("boom")

    drv = BoomDriver()
    # must not raise — additive task never tears down the pipeline
    sched.run_fannaoxiao_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert recorded == []

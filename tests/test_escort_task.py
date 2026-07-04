"""Gate-logic tests for the 賞金之路 (Escort) weekend H5 task.

Covers `game_actions.escort_scheduler.run_escort_if_due`:
  - flag OFF (enable_escort False)             -> skip
  - wrong backend (adb)                        -> skip
  - not weekend (Monday)                       -> skip (無須每日尋找)
  - weekend but before 11:00                   -> skip
  - already ran today (record not expired)     -> skip
  - no live page                               -> skip
  - due + enabled + web_h5 + Sat >=11:00       -> enters, fights a round, records
  - enter fails (非賞金之路週末)                -> close_to_home, does NOT record
  - 0 NPC fought                               -> does NOT record (retry next cycle)

No real Playwright page: a FakeDriver is injected via the `driver=` param;
config / json_manager / the local clock are monkeypatched. `utils.pause_guard`
is exercised for real (bind/unbind on a fake page are inert).
"""
from __future__ import annotations

import datetime

import pytest

from game_actions import escort_scheduler as sched


# fixed clock points (only weekday()/hour matter):
SAT_11 = datetime.datetime(2022, 1, 1, 11, 30)   # 2022-01-01 = Saturday
SAT_09 = datetime.datetime(2022, 1, 1, 9, 0)     # Saturday, before 11:00
MON_12 = datetime.datetime(2022, 1, 3, 12, 0)    # 2022-01-03 = Monday


# ── fakes ──────────────────────────────────────────────────────────────


class FakePage:
    def evaluate(self, *a, **k):
        return None


class FakeDevice:
    def __init__(self, page=None):
        self._page = page


class FakeDriver:
    def __init__(self, *, enter_ok=True, summary=None):
        self.enter_ok = enter_ok
        self.summary = summary if summary is not None else {
            "fought": [{"name": "虛偽騎士", "outcome": "win"}], "count": 1,
            "win": 1, "lose": 0,
        }
        self.enter_calls = 0
        self.fight_calls = 0
        self.close_calls = 0

    def enter_escort(self, page):
        self.enter_calls += 1
        return self.enter_ok

    def fight_npc_round(self, page):
        self.fight_calls += 1
        return self.summary

    def close_to_home(self, page):
        self.close_calls += 1
        return True


class FakeCfg(dict):
    pass


# ── monkeypatch helpers ────────────────────────────────────────────────


@pytest.fixture
def patch_env(monkeypatch):
    state = {"enable": False, "backend": "web_h5", "record": None, "now": SAT_11}
    recorded: list = []

    def fake_get_device_config(ip):
        return FakeCfg(enable_escort=state["enable"], backend=state["backend"])

    def fake_return_time(ip, name=""):
        return state["record"]

    def fake_is_record_expired(record, seconds, **kw):
        # None -> never run (due). A cross-day record (is_next_day) -> expired
        # (due again), mirroring json_manager so Sat+Sun each run once.
        return record is None or bool(record.get("is_next_day"))

    def fake_time_recording(ip, name=""):
        recorded.append(name)

    monkeypatch.setattr(sched.config_manager, "get_device_config", fake_get_device_config)
    monkeypatch.setattr(sched, "return_time", fake_return_time)
    monkeypatch.setattr(sched, "is_record_expired", fake_is_record_expired)
    monkeypatch.setattr(sched, "time_recording", fake_time_recording)
    monkeypatch.setattr(sched, "_local_now", lambda: state["now"])

    def configure(*, enable, backend="web_h5", record=None, now=SAT_11):
        state["enable"] = enable
        state["backend"] = backend
        state["record"] = record
        state["now"] = now

    return configure, recorded


# ── tests ──────────────────────────────────────────────────────────────


def test_skip_when_flag_off(patch_env):
    configure, recorded = patch_env
    configure(enable=False)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0 and drv.fight_calls == 0 and recorded == []


def test_skip_when_wrong_backend(patch_env):
    configure, recorded = patch_env
    configure(enable=True, backend="adb")
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0 and recorded == []


def test_skip_when_not_weekend(patch_env):
    configure, recorded = patch_env
    configure(enable=True, now=MON_12)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0 and recorded == []


def test_skip_when_before_start_hour(patch_env):
    configure, recorded = patch_env
    configure(enable=True, now=SAT_09)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0 and recorded == []


def test_skip_when_already_ran(patch_env):
    configure, recorded = patch_env
    configure(enable=True, record={"timestamp": 1_700_000_000})  # not expired
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 0 and recorded == []


def test_runs_again_next_day(patch_env):
    # Sat run recorded; on Sun the record is cross-day (is_next_day) -> due again,
    # so the weekend yields two runs total (once Sat, once Sun).
    configure, recorded = patch_env
    configure(enable=True, record={"timestamp": 1_700_000_000, "is_next_day": True},
              now=SAT_11)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 1 and drv.fight_calls == 1
    assert recorded == ["escort_last_run"]


def test_skip_when_no_page(patch_env):
    configure, recorded = patch_env
    configure(enable=True)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(page=None), "dev1", driver=drv)
    assert drv.enter_calls == 0 and recorded == []


def test_runs_and_records_when_due(patch_env):
    configure, recorded = patch_env
    configure(enable=True, record=None, now=SAT_11)
    drv = FakeDriver()
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 1 and drv.fight_calls == 1 and drv.close_calls == 1
    assert recorded == ["escort_last_run"]


def test_enter_fail_closes_and_no_record(patch_env):
    configure, recorded = patch_env
    configure(enable=True)
    drv = FakeDriver(enter_ok=False)
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.enter_calls == 1 and drv.fight_calls == 0
    assert drv.close_calls == 1 and recorded == []


def test_zero_fights_not_recorded(patch_env):
    configure, recorded = patch_env
    configure(enable=True)
    drv = FakeDriver(summary={"fought": [], "count": 0, "win": 0, "lose": 0})
    sched.run_escort_if_due(FakeDevice(FakePage()), "dev1", driver=drv)
    assert drv.fight_calls == 1 and recorded == []

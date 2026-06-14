"""Tests for ws_token.runner._run_carpark — current-parked carpark plan path.

Model (2026-06-13 rewrite): each wake reads how many cross cars are CURRENTLY
parked (read_parked_cross / 12802 parking_data), parks up to ``win.cross`` minus
that, and stores ``carpark_repark.next_ts`` (min of: earliest car's 8h expiry
inside the window, next cross-open minus open_lead) for the sleep scheduler to
wake early to. A 09:59 grab wake lands in the pre-open lead -> wait to open ->
park (with a short retry while the server's lots are still empty). Legacy
target/auto path is unchanged when plan is off.
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from ws_token import runner  # noqa: E402
from ws_token import state as ws_state  # noqa: E402
from ws_token.carpark import Mount, ParkingInfo  # noqa: E402

PLAN = {
    "enabled": True,
    "silver_levels": [9, 10],
    "day": {"window": ["10:00", "22:00"], "cross": 1},
    "night": {"window": ["22:00", "10:00"], "cross": 0},
}
BEFORE_OPEN = datetime(2026, 6, 13, 9, 59, 0)
NOON = datetime(2026, 6, 13, 12, 0)
NIGHT = datetime(2026, 6, 13, 23, 0)
GAP = datetime(2026, 6, 13, 13, 0)   # in window for PLAN; use a gap plan below

CLIENT = object()


def _parked(start_dt, mount_id=101, master_id=900, pos=1):
    return Mount(mount_id=mount_id, car_lev=1, parking=True,
                 parking_info=ParkingInfo(type=3, master_id=master_id, pos=pos,
                                          start_time=int(start_dt.timestamp())))


def _stub_reads(monkeypatch, responses):
    """read_parked_cross returns successive lists (last one repeats)."""
    state = {"i": 0}

    def fake(client, **kw):
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return list(responses[i])

    monkeypatch.setattr(runner.carpark, "read_parked_cross", fake)
    return state


def _stub_park(monkeypatch, calls, results):
    """auto_select_and_park_many records calls, returns queued results (repeat last)."""
    state = {"i": 0}

    def fake(client, *, count, prefer_levels=(9, 10), **kw):
        calls.append({"count": count, "prefer_levels": tuple(prefer_levels)})
        i = min(state["i"], len(results) - 1)
        state["i"] += 1
        r = results[i]
        n = r.get("parked_count", 0)
        return {"parked_count": n, "requested": count,
                "reason": r.get("reason", "ok"), "results": []}

    monkeypatch.setattr(runner.carpark, "auto_select_and_park_many", fake)
    return state


@pytest.fixture(autouse=True)
def _stub_collect(monkeypatch):
    collected = []
    monkeypatch.setattr(runner.carpark, "collect_bag_rewards",
                        lambda client, **kw: collected.append(1)
                        or {"success": True})
    return collected


# --- out of any window: collect + skip, still schedule next open -------------

def test_plan_collects_income_out_of_window(monkeypatch, tmp_path, _stub_collect):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 0}])
    _stub_reads(monkeypatch, [[]])
    gap_plan = {"enabled": True,
                "day": {"window": ["10:00", "12:00"], "cross": 1},
                "night": {"window": ["12:00", "10:00"], "cross": 0}}
    out = runner._run_carpark(CLIENT, target=None, plan_cfg=gap_plan,
                              device="dev1", state_dir=tmp_path, now=GAP)
    assert "skipped" in out
    assert out["collect"] == {"success": True}
    assert _stub_collect == [1]
    assert calls == []                      # no parking outside the cross window
    st = ws_state.load_state("dev1", state_dir=tmp_path)
    # next cross open (tomorrow 10:00) minus default 60s lead is scheduled
    assert st["carpark_repark"]["next_ts"] == \
        datetime(2026, 6, 14, 10, 0).timestamp() - 60


# --- in window, no car parked -> park target, record next_ts -----------------

def test_plan_in_window_no_car_parks_target(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 1}])
    parked_after = _parked(NOON)            # freshly parked car (re-read result)
    _stub_reads(monkeypatch, [[], [parked_after]])
    out = runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                              device="dev1", state_dir=tmp_path, now=NOON)
    assert out["window"] == "day"
    assert out["target"] == 1
    assert calls == [{"count": 1, "prefer_levels": (9, 10)}]
    st = ws_state.load_state("dev1", state_dir=tmp_path)
    # next_ts = parked car expiry (start + 8h + 30s margin), inside the window
    assert st["carpark_repark"]["next_ts"] == \
        int(NOON.timestamp()) + 28800 + 30


# --- in window, already at target -> no park, but still store repark wake -----

def test_plan_already_target_skips_park(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 0}])
    parked = _parked(datetime(2026, 6, 13, 11, 30))   # parked 11:30, still live
    _stub_reads(monkeypatch, [[parked]])
    out = runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                              device="dev1", state_dir=tmp_path, now=NOON)
    assert calls == []                       # current(1) >= target(1)
    assert out["current"] == 1
    assert "skipped" in out
    st = ws_state.load_state("dev1", state_dir=tmp_path)
    assert st["carpark_repark"]["next_ts"] == \
        int(datetime(2026, 6, 13, 11, 30).timestamp()) + 28800 + 30


# --- snapshot written for the dashboard (cars + captured_ts + window) --------

def test_plan_writes_dashboard_snapshot(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 0}])
    parked = _parked(datetime(2026, 6, 13, 11, 30), mount_id=101,
                     master_id=1001001013, pos=4)
    _stub_reads(monkeypatch, [[parked]])
    runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                        device="dev1", state_dir=tmp_path, now=NOON)
    snap = ws_state.load_state("dev1", state_dir=tmp_path)["carpark_repark"]
    assert snap["window"] == "day"
    assert snap["target"] == 1
    assert snap["park_max"] == 28800
    assert snap["offset"] == 0
    assert snap["captured_ts"] == NOON.timestamp()
    assert snap["cars"] == [{
        "mount_id": 101, "master_id": 1001001013, "pos": 4,
        "start_time": int(datetime(2026, 6, 13, 11, 30).timestamp())}]


# --- after the 8h auto-collect the car is gone -> re-park ---------------------

def test_plan_after_autocollect_reparks(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 1}])
    _stub_reads(monkeypatch, [[], [_parked(NOON)]])  # 0 now -> park -> 1
    runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                        device="dev1", state_dir=tmp_path, now=NOON)
    assert calls == [{"count": 1, "prefer_levels": (9, 10)}]


# --- night window (cross=0): never parks, schedules the morning open ---------

def test_plan_night_window_no_park(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 0}])
    _stub_reads(monkeypatch, [[]])
    out = runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                              device="dev1", state_dir=tmp_path, now=NIGHT)
    assert calls == []
    assert "skipped" in out
    st = ws_state.load_state("dev1", state_dir=tmp_path)
    assert st["carpark_repark"]["next_ts"] == \
        datetime(2026, 6, 14, 10, 0).timestamp() - 60


# --- 09:59 grab: wait until open, then park ----------------------------------

def test_plan_grab_waits_until_open_then_parks(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls, [{"parked_count": 1}])
    _stub_reads(monkeypatch, [[], [_parked(datetime(2026, 6, 13, 10, 0))]])
    slept = []
    out = runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                              device="dev1", state_dir=tmp_path,
                              now=BEFORE_OPEN, sleep_fn=lambda s: slept.append(s))
    assert slept == [60.0]                   # waited 60s to 10:00
    assert out["window"] == "day"
    assert out.get("grab") is True
    assert calls == [{"count": 1, "prefer_levels": (9, 10)}]


# --- grab retries while the server's lots are still empty at open ------------

def test_plan_grab_retries_on_empty_lots(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls,
               [{"parked_count": 0, "reason": "no_parkable_lot"},
                {"parked_count": 0, "reason": "no_parkable_lot"},
                {"parked_count": 1, "reason": "ok"}])
    _stub_reads(monkeypatch, [[], [_parked(datetime(2026, 6, 13, 10, 0))]])
    slept = []
    grab_plan = dict(PLAN, grab_attempts=5, grab_poll_seconds=0.2)
    runner._run_carpark(CLIENT, target=None, plan_cfg=grab_plan,
                        device="dev1", state_dir=tmp_path,
                        now=BEFORE_OPEN, sleep_fn=lambda s: slept.append(s))
    assert len(calls) == 3                    # parked on the 3rd attempt
    # one 60s open-wait + two 0.2s poll gaps between the 3 attempts
    assert slept == [60.0, 0.2, 0.2]


def test_plan_grab_stops_retry_on_hard_failure(monkeypatch, tmp_path):
    calls = []
    _stub_park(monkeypatch, calls,
               [{"parked_count": 0, "reason": "no_available_mount"}])
    _stub_reads(monkeypatch, [[]])
    slept = []
    runner._run_carpark(CLIENT, target=None, plan_cfg=PLAN,
                        device="dev1", state_dir=tmp_path,
                        now=BEFORE_OPEN, sleep_fn=lambda s: slept.append(s))
    assert len(calls) == 1                    # no_available_mount -> no retry
    assert slept == [60.0]                    # only the open-wait


# --- legacy paths unchanged when plan off ------------------------------------

def test_plan_disabled_falls_back_to_legacy_target(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(runner.carpark, "auto_park_cross",
                        lambda client, *, target_id: seen.setdefault("t", target_id)
                        or {"parked": True})
    runner._run_carpark(CLIENT, target=777, auto=False,
                        plan_cfg={"enabled": False}, device="dev1",
                        state_dir=tmp_path, now=NOON)
    assert seen["t"] == 777


def test_legacy_no_flags_still_skips():
    out = runner._run_carpark(CLIENT, target=None, auto=False)
    assert "skipped" in out


def test_run_device_forwards_carpark_plan(monkeypatch, tmp_path):
    captured = {}

    def fake_run_carpark(client, **kw):
        captured.update(kw)
        return {"window": "day"}

    monkeypatch.setattr(runner, "_run_carpark", fake_run_carpark)

    class _FakeClient:
        def __init__(self, *a, **k): ...
        def connect(self):
            return {"serv_time": 1, "role_id": 9}
        def close(self): ...
        def is_kicked(self):
            return False

    monkeypatch.setattr(runner, "_make_client",
                        lambda creds, **kw: _FakeClient())
    monkeypatch.setattr(runner, "load_creds", lambda dev: type(
        "C", (), {"login_time": 0, "role_id": 9})())

    def boom(*a, **k):
        raise RuntimeError("offline test: no WS calls")

    for mod in (runner.main_tasks,):
        monkeypatch.setattr(mod, "collect_state", boom, raising=False)

    rep = runner.run_device("devX", carpark_plan=PLAN,
                            carpark_state_dir=tmp_path, carpark_now=NOON,
                            workshop_rotate=False, couple_gifts=False)
    assert rep.login_ok
    assert captured["plan_cfg"] == PLAN
    assert captured["device"] == "devX"
    assert captured["now"] == NOON
    assert captured["state_dir"] == tmp_path

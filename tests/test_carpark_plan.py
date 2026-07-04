"""Tests for ws_token.carpark_plan — day/night window + repark/grab wake timing.

模型（2026-06-13 改版）：current-parked 而非每日配額。
  - day/night 各自跨界目標 {cross}（日1夜0）；窗口可跨午夜（night 20:00->08:00）。
  - carpark task 每輪算 next wake = min(候選)，寫 ws_state（runner 負責），候選 =
    (a) 我方最早到期車 start_time + park_max_sec(+margin)（僅當開窗內、到期落窗內）、
    (b) 下個跨界開窗 - open_lead（09:59 搶位）。
  - cross_open_wait: 醒在開窗前 open_lead 內 -> (秒數, open_dt, win) 供 grab。
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token.carpark import Mount, ParkingInfo  # noqa: E402
from ws_token.carpark_plan import (  # noqa: E402
    DEFAULT_CLUSTER_MIN,
    DEFAULT_GRAB_POLL_SECONDS,
    DEFAULT_GRAB_WINDOW_SECONDS,
    DEFAULT_OPEN_LEAD_SECONDS,
    DEFAULT_PARK_MAX_SECONDS,
    active_window,
    allow_low_noncluster,
    carpark_wake_ts,
    cluster_min,
    cross_open_wait,
    grab_attempts,
    grab_poll_seconds,
    grab_window_seconds,
    next_cross_open_dt,
    open_lead_seconds,
    park_max_seconds,
    parse_plan,
    repark_margin_seconds,
    start_time_offset,
)

# Real device window: cross park allowed Taiwan 10:00-22:00; night no cross.
PLAN_CFG = {
    "enabled": True,
    "silver_levels": [9, 10],
    "day": {"window": ["10:00", "22:00"], "cross": 1},
    "night": {"window": ["22:00", "10:00"], "cross": 0},
}


def _dt(h, m=0, s=0, day=13):
    return datetime(2026, 6, day, h, m, s)


def _parked(start_time, mount_id=22, master_id=900, pos=1):
    return Mount(mount_id=mount_id, car_lev=1, parking=True,
                 parking_info=ParkingInfo(type=3, master_id=master_id,
                                          pos=pos, start_time=start_time))


# --- parse_plan / active_window (kept) ---------------------------------------

def test_parse_plan_builds_two_windows():
    plans = parse_plan(PLAN_CFG)
    assert [p.name for p in plans] == ["day", "night"]
    assert (plans[0].start.hour, plans[0].end.hour) == (10, 22)
    assert plans[0].cross == 1
    assert plans[1].cross == 0


def test_active_window_day_boundaries():
    plans = parse_plan(PLAN_CFG)
    assert active_window(plans, _dt(9, 59)).name == "night"
    assert active_window(plans, _dt(10, 0)).name == "day"
    assert active_window(plans, _dt(21, 59)).name == "day"
    assert active_window(plans, _dt(22, 0)).name == "night"
    assert active_window(plans, _dt(3, 0)).name == "night"


# --- config getters with defaults --------------------------------------------

def test_config_getters_defaults():
    assert park_max_seconds({}) == DEFAULT_PARK_MAX_SECONDS == 28800
    assert open_lead_seconds({}) == DEFAULT_OPEN_LEAD_SECONDS == 60
    assert repark_margin_seconds({}) == 30
    assert start_time_offset({}) == 0


def test_config_getters_overrides():
    cfg = {"park_max_seconds": 7200, "open_lead_seconds": 90,
           "repark_margin_seconds": 5, "start_time_offset": -3}
    assert park_max_seconds(cfg) == 7200
    assert open_lead_seconds(cfg) == 90
    assert repark_margin_seconds(cfg) == 5
    assert start_time_offset(cfg) == -3


# --- grab/cluster getters (2026-06-15 搶位分層 + 每秒重試) -------------------

def test_grab_getters_defaults():
    # poll default raised 0.3 -> 1.0 for the every-second grab retry
    assert grab_poll_seconds({}) == DEFAULT_GRAB_POLL_SECONDS == 1.0
    assert grab_window_seconds({}) == DEFAULT_GRAB_WINDOW_SECONDS == 60
    assert cluster_min({}) == DEFAULT_CLUSTER_MIN == 3
    assert grab_attempts({}) == 8
    # last-resort: park low non-cluster slots by default (user 2026-06-15)
    assert allow_low_noncluster({}) is True


def test_grab_getters_overrides():
    cfg = {"grab_poll_seconds": 0.5, "grab_window_seconds": 120,
           "cluster_min": 5, "grab_attempts": 20,
           "allow_low_noncluster": False}
    assert grab_poll_seconds(cfg) == 0.5
    assert grab_window_seconds(cfg) == 120
    assert cluster_min(cfg) == 5
    assert grab_attempts(cfg) == 20
    assert allow_low_noncluster(cfg) is False


def test_grab_getters_sanitize_bad_values():
    # non-positive / unparsable -> fall back to defaults
    bad = {"grab_poll_seconds": 0, "grab_window_seconds": -1,
           "cluster_min": "x", "grab_attempts": 0}
    assert grab_poll_seconds(bad) == 1.0
    assert grab_window_seconds(bad) == 60
    assert cluster_min(bad) == 3
    assert grab_attempts(bad) == 8
    # allow_low_noncluster coerces truthy/falsey config values to bool
    assert allow_low_noncluster({"allow_low_noncluster": 0}) is False
    assert allow_low_noncluster({"allow_low_noncluster": 1}) is True


# --- next_cross_open_dt: next time a cross (cross>0) window opens -------------

def test_next_cross_open_before_window_is_today():
    plans = parse_plan(PLAN_CFG)
    assert next_cross_open_dt(plans, _dt(9, 0)) == _dt(10, 0)


def test_next_cross_open_inside_window_is_tomorrow():
    plans = parse_plan(PLAN_CFG)
    assert next_cross_open_dt(plans, _dt(15, 0)) == _dt(10, 0, day=14)


def test_next_cross_open_after_window_is_tomorrow():
    plans = parse_plan(PLAN_CFG)
    assert next_cross_open_dt(plans, _dt(23, 0)) == _dt(10, 0, day=14)


def test_next_cross_open_none_when_no_cross_window():
    plans = parse_plan({"enabled": True,
                        "night": {"window": ["22:00", "10:00"], "cross": 0}})
    assert next_cross_open_dt(plans, _dt(9, 0)) is None


# --- cross_open_wait: woke just before open -> wait & grab -------------------

def test_cross_open_wait_within_lead_returns_seconds():
    plans = parse_plan(PLAN_CFG)
    res = cross_open_wait(plans, _dt(9, 59, 0), open_lead=60)
    assert res is not None
    secs, open_dt, win = res
    assert secs == 60
    assert open_dt == _dt(10, 0)
    assert win.name == "day"


def test_cross_open_wait_none_when_too_early():
    plans = parse_plan(PLAN_CFG)
    assert cross_open_wait(plans, _dt(9, 55, 0), open_lead=60) is None


def test_cross_open_wait_none_when_already_open():
    plans = parse_plan(PLAN_CFG)
    assert cross_open_wait(plans, _dt(10, 30), open_lead=60) is None


def test_cross_open_wait_zero_lead_disabled():
    plans = parse_plan(PLAN_CFG)
    assert cross_open_wait(plans, _dt(9, 59, 30), open_lead=0) is None


# --- carpark_wake_ts: min(repark expiry within window, next open - lead) -----

def test_wake_ts_day_no_car_is_next_open_minus_lead_tomorrow():
    plans = parse_plan(PLAN_CFG)
    now = _dt(15, 0)  # in window, no car parked
    ts = carpark_wake_ts([], plans, now, max_sec=28800, open_lead=60, margin=30)
    # only candidate: tomorrow 10:00 - 60s
    assert ts == _dt(10, 0, day=14).timestamp() - 60


def test_wake_ts_night_is_next_open_minus_lead():
    plans = parse_plan(PLAN_CFG)
    now = _dt(2, 0)  # night, cross=0
    ts = carpark_wake_ts([], plans, now, max_sec=28800, open_lead=60, margin=30)
    assert ts == _dt(10, 0).timestamp() - 60


def test_wake_ts_repark_expiry_within_window_wins():
    plans = parse_plan(PLAN_CFG)
    now = _dt(11, 0)            # in day window
    start = _dt(10, 30).timestamp()   # parked at 10:30
    ts = carpark_wake_ts([_parked(int(start))], plans, now,
                         max_sec=28800, open_lead=60, margin=30)
    # expiry 10:30 + 8h + 30s = 18:30:30 ; tomorrow-open-lead is far later
    assert ts == int(start) + 28800 + 30


def test_wake_ts_repark_after_window_close_falls_back_to_next_open():
    plans = parse_plan(PLAN_CFG)
    now = _dt(21, 0)               # late in day window
    start = _dt(20, 30).timestamp()  # parked 20:30 -> expiry 04:30 (> 22:00 close)
    ts = carpark_wake_ts([_parked(int(start))], plans, now,
                         max_sec=28800, open_lead=60, margin=30)
    # expiry is past window close -> ignored; next open (tomorrow 10:00 - lead)
    assert ts == _dt(10, 0, day=14).timestamp() - 60


def test_wake_ts_offset_shifts_expiry():
    plans = parse_plan(PLAN_CFG)
    now = _dt(11, 0)
    start = _dt(10, 30).timestamp()
    ts = carpark_wake_ts([_parked(int(start))], plans, now,
                         max_sec=28800, open_lead=60, margin=0, offset=120)
    assert ts == int(start) + 120 + 28800


def test_wake_ts_none_when_no_cross_window_and_no_car():
    plans = parse_plan({"enabled": True,
                        "night": {"window": ["22:00", "10:00"], "cross": 0}})
    assert carpark_wake_ts([], plans, _dt(9, 0),
                           max_sec=28800, open_lead=60, margin=30) is None

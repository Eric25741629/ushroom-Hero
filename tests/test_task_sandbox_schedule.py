from datetime import datetime

from task_sandbox.schedule import (
    Always, AndSchedule, Custom, DailyOnce, EveryHours, HourWindow, WeeklyOn,
)


def _dt(year=2026, month=5, day=2, hour=10, minute=0):
    return datetime(year, month, day, hour, minute)


def test_always_runs():
    assert Always().should_run("ip", _dt(), None) is True
    assert Always().should_run("ip", _dt(), _dt()) is True


def test_every_hours_runs_when_no_history():
    assert EveryHours(hours=2).should_run("ip", _dt(hour=10), None) is True


def test_every_hours_blocks_within_window():
    last = _dt(hour=10)
    now = _dt(hour=11)
    assert EveryHours(hours=2).should_run("ip", now, last) is False


def test_every_hours_runs_after_window():
    last = _dt(hour=10)
    now = _dt(hour=12, minute=1)
    assert EveryHours(hours=2).should_run("ip", now, last) is True


def test_daily_once_runs_when_no_history():
    assert DailyOnce(reset_hour=4).should_run("ip", _dt(hour=10), None) is True


def test_daily_once_blocks_same_day():
    last = _dt(hour=10)
    now = _dt(hour=15)
    assert DailyOnce(reset_hour=4).should_run("ip", now, last) is False


def test_daily_once_runs_after_reset():
    last = _dt(day=2, hour=23)
    now = _dt(day=3, hour=5)
    assert DailyOnce(reset_hour=4).should_run("ip", now, last) is True


def test_weekly_on_runs_only_on_listed_days():
    monday = _dt(year=2026, month=5, day=4)
    sunday = _dt(year=2026, month=5, day=3)
    sched = WeeklyOn(days=frozenset({0, 1, 2, 3, 4}))
    assert sched.should_run("ip", monday, None) is True
    assert sched.should_run("ip", sunday, None) is False


def test_hour_window_inclusive_start_exclusive_end():
    sched = HourWindow(start_hour=20, end_hour=23)
    assert sched.should_run("ip", _dt(hour=19), None) is False
    assert sched.should_run("ip", _dt(hour=20), None) is True
    assert sched.should_run("ip", _dt(hour=22), None) is True
    assert sched.should_run("ip", _dt(hour=23), None) is False


def test_and_schedule_requires_all():
    sched = AndSchedule(WeeklyOn(days=frozenset({0})), HourWindow(20, 23))
    monday_evening = _dt(year=2026, month=5, day=4, hour=21)
    monday_morning = _dt(year=2026, month=5, day=4, hour=10)
    assert sched.should_run("ip", monday_evening, None) is True
    assert sched.should_run("ip", monday_morning, None) is False


def test_custom_calls_fn():
    calls = []

    def fn(ip, now, last):
        calls.append((ip, now, last))
        return ip == "yes"

    assert Custom(fn=fn).should_run("yes", _dt(), None) is True
    assert Custom(fn=fn).should_run("no", _dt(), None) is False
    assert len(calls) == 2

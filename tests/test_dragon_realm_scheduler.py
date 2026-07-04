import datetime
from unittest import mock

from game_actions import dragon_realm_scheduler as sch


def test_skips_when_flag_off():
    cfg = {"global": {"dragon_realm_enabled": False}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_not_called()


def test_runs_when_flag_on_and_due():
    cfg = {"global": {"dragon_realm_enabled": True}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg), \
         mock.patch.object(sch, "_is_due", return_value=True), \
         mock.patch.object(sch, "_mark_done"):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_called_once()


def test_skips_when_not_due():
    cfg = {"global": {"dragon_realm_enabled": True}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg), \
         mock.patch.object(sch, "_is_due", return_value=False):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_not_called()


# --- triweekly cycle ---

def test_is_dragon_week_anchor_week():
    assert sch._is_dragon_week(datetime.date(2026, 6, 22)) is True  # Mon anchor
    assert sch._is_dragon_week(datetime.date(2026, 6, 24)) is True  # Wed same week
    assert sch._is_dragon_week(datetime.date(2026, 6, 26)) is True  # Fri same week
    assert sch._is_dragon_week(datetime.date(2026, 6, 28)) is True  # Sun same week


def test_is_dragon_week_off_weeks():
    assert sch._is_dragon_week(datetime.date(2026, 6, 29)) is False  # next Mon
    assert sch._is_dragon_week(datetime.date(2026, 7, 1)) is False   # Wed week+1
    assert sch._is_dragon_week(datetime.date(2026, 7, 6)) is False   # Mon week+2


def test_is_dragon_week_next_cycle():
    assert sch._is_dragon_week(datetime.date(2026, 7, 13)) is True   # Mon +3wk
    assert sch._is_dragon_week(datetime.date(2026, 7, 15)) is True   # Wed +3wk


def test_is_dragon_week_previous_cycle():
    assert sch._is_dragon_week(datetime.date(2026, 6, 1)) is True    # Mon -3wk
    assert sch._is_dragon_week(datetime.date(2026, 6, 3)) is True    # Wed -3wk
    assert sch._is_dragon_week(datetime.date(2026, 6, 8)) is False   # Mon -2wk


# --- time window ---

def test_open_window_wrong_weekday():
    # Monday — not Wed/Thu/Fri
    assert sch._within_open_window(datetime.datetime(2026, 6, 22, 12, 0)) is False
    # Tuesday
    assert sch._within_open_window(datetime.datetime(2026, 6, 23, 12, 0)) is False
    # Saturday
    assert sch._within_open_window(datetime.datetime(2026, 6, 27, 12, 0)) is False


def test_open_window_active_days():
    # Wed 10:00 — open
    assert sch._within_open_window(datetime.datetime(2026, 6, 24, 10, 0)) is True
    # Thu 15:00 — open
    assert sch._within_open_window(datetime.datetime(2026, 6, 25, 15, 0)) is True
    # Fri 21:59 — open
    assert sch._within_open_window(datetime.datetime(2026, 6, 26, 21, 59)) is True


def test_open_window_boundary():
    # Wed 09:59 — too early
    assert sch._within_open_window(datetime.datetime(2026, 6, 24, 9, 59)) is False
    # Wed 22:00 — closed
    assert sch._within_open_window(datetime.datetime(2026, 6, 24, 22, 0)) is False


# --- _is_due combines cycle + window + cooldown ---

def test_is_due_off_week():
    with mock.patch("game_actions.dragon_realm_scheduler.return_time", return_value=None):
        # Off-week Wed 12:00 — should not be due
        now = datetime.datetime(2026, 6, 29, 12, 0)  # Mon of off-week
        assert sch._is_due("x", datetime.datetime(2026, 7, 1, 12, 0)) is False


def test_is_due_active_week_in_window():
    with mock.patch("game_actions.dragon_realm_scheduler.return_time", return_value=None), \
         mock.patch("game_actions.dragon_realm_scheduler.is_record_expired", return_value=True):
        now = datetime.datetime(2026, 6, 24, 12, 0)  # Wed of active week
        assert sch._is_due("x", now) is True

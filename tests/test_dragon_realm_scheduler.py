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


def test_open_window_gate_before_10am():
    import datetime
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 9, 59)) is False
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 10, 0)) is True
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 23, 0)) is True

import pytest
pytest.importorskip('uiautomator2')
import datetime

from new_battle import _compute_biweekly_slot_key


TPE = datetime.timezone(datetime.timedelta(hours=8))


def test_trigger_window_and_weekday():
    sat_ok = datetime.datetime(2026, 3, 14, 20, 2, tzinfo=TPE)
    sun_ok = datetime.datetime(2026, 3, 15, 20, 0, tzinfo=TPE)
    fri_no = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=TPE)
    sat_late = datetime.datetime(2026, 3, 14, 20, 6, tzinfo=TPE)

    assert _compute_biweekly_slot_key(sat_ok) == "2026-03-14-20"
    assert _compute_biweekly_slot_key(sun_ok) == "2026-03-15-20"
    assert _compute_biweekly_slot_key(fri_no) is None
    assert _compute_biweekly_slot_key(sat_late) is None


def test_slot_dedupe_persistence():
    dt = datetime.datetime(2026, 3, 14, 20, 1, tzinfo=TPE)
    slot = _compute_biweekly_slot_key(dt)
    record = {"slot_key": slot}

    assert record["slot_key"] == slot
    assert slot == "2026-03-14-20"


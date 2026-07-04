"""
TDD tests for json_manager.py

Run with:  pytest tests/test_json_manager.py -v
All file I/O is isolated to tmp_path so tests are hermetic.
"""

import datetime
import time
import pytest
import pytz

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_TZ = pytz.timezone("Asia/Taipei")

def _now_ts() -> float:
    return time.time()

def _yesterday_ts() -> float:
    return time.time() - 86400

def _last_week_ts() -> float:
    return time.time() - 7 * 86400


# ──────────────────────────────────────────────────────────────────────────────
# _ts_same_day / _ts_same_week  (module-level private helpers)
# ──────────────────────────────────────────────────────────────────────────────

class TestTsHelpers:
    def setup_method(self):
        from json_manager import _ts_same_day, _ts_same_week
        self.same_day = _ts_same_day
        self.same_week = _ts_same_week

    def test_same_day_with_now(self):
        assert self.same_day(_now_ts()) is True

    def test_same_day_with_yesterday(self):
        assert self.same_day(_yesterday_ts()) is False

    def test_same_day_invalid_ts_returns_false(self):
        assert self.same_day(-1e18) is False
        assert self.same_day(float("inf")) is False

    def test_same_week_with_now(self):
        assert self.same_week(_now_ts()) is True

    def test_same_week_with_last_week(self):
        assert self.same_week(_last_week_ts()) is False

    def test_same_week_invalid_ts_returns_false(self):
        assert self.same_week(float("nan")) is False


# ──────────────────────────────────────────────────────────────────────────────
# _parse_recorded_date  (module-level private helper)
# ──────────────────────────────────────────────────────────────────────────────

class TestParseRecordedDate:
    def setup_method(self):
        from json_manager import _parse_recorded_date
        self.parse = _parse_recorded_date

    def test_from_recorded_date_string(self):
        rec = {"recorded_date": "2026-01-15", "timestamp": 0}
        result = self.parse(rec)
        assert result == datetime.date(2026, 1, 15)

    def test_from_timestamp_fallback(self):
        ts = _now_ts()
        rec = {"timestamp": ts}
        result = self.parse(rec)
        expected = datetime.datetime.fromtimestamp(ts, _TZ).date()
        assert result == expected

    def test_invalid_date_string_falls_back_to_timestamp(self):
        ts = _now_ts()
        rec = {"recorded_date": "bad-date", "timestamp": ts}
        result = self.parse(rec)
        expected = datetime.datetime.fromtimestamp(ts, _TZ).date()
        assert result == expected

    def test_none_record_returns_none(self):
        assert self.parse(None) is None

    def test_empty_dict_returns_none(self):
        assert self.parse({}) is None


# ──────────────────────────────────────────────────────────────────────────────
# JsonDataManager
# ──────────────────────────────────────────────────────────────────────────────

class TestJsonDataManager:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _mgr(self):
        from json_manager import JsonDataManager
        return JsonDataManager("test_device")

    def test_load_creates_file_when_missing(self, tmp_path):
        mgr = self._mgr()
        data = mgr.load_data({"key": "value"})
        assert data == {"key": "value"}
        assert (tmp_path / "test_device.json").exists()

    def test_save_and_reload(self):
        mgr = self._mgr()
        mgr.save_data({"x": 42})
        loaded = mgr.load_data()
        assert loaded["x"] == 42

    def test_record_timestamp_and_get_record(self):
        mgr = self._mgr()
        mgr.record_timestamp("ping")
        rec = mgr.get_record("ping")
        assert rec is not None
        assert "timestamp" in rec
        assert "date" in rec

    def test_get_record_returns_none_for_missing(self):
        mgr = self._mgr()
        assert mgr.get_record("nonexistent") is None

    def test_is_same_day_fresh_record(self):
        mgr = self._mgr()
        mgr.record_timestamp("action")
        assert mgr.is_same_day("action") is True

    def test_is_same_day_old_record(self):
        mgr = self._mgr()
        mgr.save_data({"action": {"timestamp": _yesterday_ts(), "date": "2000-01-01"}})
        assert mgr.is_same_day("action") is False

    def test_is_same_day_missing_record_returns_false(self):
        mgr = self._mgr()
        assert mgr.is_same_day("norecord") is False

    def test_is_same_week_fresh_record(self):
        mgr = self._mgr()
        mgr.record_timestamp("weekly_action")
        assert mgr.is_same_week("weekly_action") is True

    def test_is_same_week_old_record(self):
        mgr = self._mgr()
        mgr.save_data({"w": {"timestamp": _last_week_ts()}})
        assert mgr.is_same_week("w") is False

    def test_is_expired_no_record_returns_true(self):
        mgr = self._mgr()
        assert mgr.is_expired("norecord") is True

    def test_is_expired_fresh_record(self):
        mgr = self._mgr()
        mgr.record_timestamp("fresh")
        assert mgr.is_expired("fresh", expired_seconds=3600) is False

    def test_is_expired_old_record(self):
        mgr = self._mgr()
        mgr.save_data({"old": {"timestamp": _yesterday_ts()}})
        assert mgr.is_expired("old", expired_seconds=3600) is True

    def test_is_expired_or_next_day_combines(self):
        mgr = self._mgr()
        mgr.record_timestamp("now")
        # fresh + same day → not expired or next day
        assert mgr.is_expired_or_next_day("now", expired_seconds=3600) is False

    def test_get_and_update_numeric_value(self):
        mgr = self._mgr()
        mgr.record_timestamp("rec")
        mgr.update_numeric_value("rec", "count", 7)
        assert mgr.get_numeric_value("rec", "count", 0) == 7


# ──────────────────────────────────────────────────────────────────────────────
# ParkMarketDataManager
# ──────────────────────────────────────────────────────────────────────────────

class TestParkMarketDataManager:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _mgr(self):
        from json_manager import ParkMarketDataManager
        return ParkMarketDataManager("park_device")

    def test_should_purchase_on_first_call(self):
        mgr = self._mgr()
        assert mgr.should_purchase("daily") is True
        assert mgr.should_purchase("weekly") is True

    def test_record_and_check_daily_purchase(self):
        mgr = self._mgr()
        mgr.record_purchase("daily", buy_num=1, check_time=1)
        # within limits → still purchasable
        assert mgr.should_purchase("daily", max_purchases=2, max_checks=2) is True

    def test_record_daily_max_reached(self):
        mgr = self._mgr()
        mgr.record_purchase("daily", buy_num=2, check_time=2)
        assert mgr.should_purchase("daily", max_purchases=2, max_checks=2) is False

    def test_record_and_check_weekly_purchase(self):
        mgr = self._mgr()
        mgr.record_purchase("weekly", buy_num=1)
        assert mgr.should_purchase("weekly", max_purchases=2) is True

    def test_get_buy_data_returns_six_tuple(self):
        mgr = self._mgr()
        result = mgr.get_buy_data()
        assert len(result) == 6

    def test_get_buy_data_reflects_recorded_purchase(self):
        mgr = self._mgr()
        mgr.record_purchase("daily", buy_num=1, check_time=1)
        _, daily_num, _, _, daily_check, _ = mgr.get_buy_data()
        assert daily_num == 1
        assert daily_check == 1

    def test_invalid_mode_raises(self):
        mgr = self._mgr()
        with pytest.raises(ValueError):
            mgr.record_purchase("invalid_mode", buy_num=1)
        with pytest.raises(ValueError):
            mgr.should_purchase("invalid_mode")

    def test_daily_accumulates_in_same_day(self):
        mgr = self._mgr()
        mgr.record_purchase("daily", buy_num=1)
        mgr.record_purchase("daily", buy_num=1)
        _, daily_num, *_ = mgr.get_buy_data()
        assert daily_num == 2

    def test_daily_resets_on_new_day(self):
        mgr = self._mgr()
        # Simulate yesterday's purchase
        yesterday = _yesterday_ts()
        data = mgr.load_data()
        data["daily"] = {
            "car_market_timestamp": yesterday,
            "car_market_buy_num": 5,
            "car_market_check_time": 3,
        }
        mgr.save_data(data)
        # Record today → should reset counters
        mgr.record_purchase("daily", buy_num=1)
        _, daily_num, *_ = mgr.get_buy_data()
        assert daily_num == 1  # reset, not 6


# ──────────────────────────────────────────────────────────────────────────────
# FamilyMarketDataManager
# ──────────────────────────────────────────────────────────────────────────────

class TestFamilyMarketDataManager:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _mgr(self):
        from json_manager import FamilyMarketDataManager
        return FamilyMarketDataManager("family_device")

    def test_get_state_empty(self):
        from json_manager import PurchaseCountState
        mgr = self._mgr()
        state = mgr.get_state("daily")
        assert isinstance(state, PurchaseCountState)
        assert state.buy_num == 0

    def test_set_and_get_state(self):
        mgr = self._mgr()
        mgr.set_state("daily", buy_num=3)
        state = mgr.get_state("daily")
        assert state.buy_num == 3

    def test_increment_buy_num(self):
        mgr = self._mgr()
        mgr.set_state("daily", buy_num=2)
        state = mgr.increment_buy_num("daily", increment=1)
        assert state.buy_num == 3

    def test_reset_state(self):
        mgr = self._mgr()
        mgr.set_state("daily", buy_num=5)
        mgr.reset_state("daily")
        state = mgr.get_state("daily")
        assert state.buy_num == 0

    def test_weekly_mode(self):
        mgr = self._mgr()
        mgr.set_state("weekly", buy_num=4)
        state = mgr.get_state("weekly")
        assert state.buy_num == 4

    def test_invalid_mode_raises(self):
        mgr = self._mgr()
        with pytest.raises(ValueError):
            mgr.get_state("bad_mode")


# ──────────────────────────────────────────────────────────────────────────────
# TimeRecordDataManager
# ──────────────────────────────────────────────────────────────────────────────

class TestTimeRecordDataManager:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _mgr(self):
        from json_manager import TimeRecordDataManager
        return TimeRecordDataManager("time_device")

    def test_record_and_retrieve(self):
        mgr = self._mgr()
        mgr.record_time("task")
        rec = mgr.get_time_record("task")
        assert rec is not None
        assert "timestamp" in rec
        assert rec["is_next_day"] is False

    def test_get_record_returns_none_when_missing(self):
        mgr = self._mgr()
        assert mgr.get_time_record("nothing") is None

    def test_is_next_day_flag_for_old_record(self):
        mgr = self._mgr()
        mgr.save_data({"task": {"timestamp": _yesterday_ts(), "date": "2000-01-01"}})
        rec = mgr.get_time_record("task")
        assert rec is not None
        assert rec["is_next_day"] is True

    def test_is_next_week_flag_for_old_record(self):
        mgr = self._mgr()
        mgr.save_data({"task": {"timestamp": _last_week_ts()}})
        rec = mgr.get_time_record("task")
        assert rec is not None
        assert rec["is_next_week"] is True

    def test_is_next_day_false_for_fresh_record(self):
        mgr = self._mgr()
        mgr.record_time("fresh_task")
        rec = mgr.get_time_record("fresh_task")
        assert rec["is_next_day"] is False
        assert rec["is_next_week"] is False

    def test_legacy_plain_float_format(self):
        """Scalar timestamp stored directly (old format)."""
        mgr = self._mgr()
        mgr.save_data({"legacy": _now_ts()})
        rec = mgr.get_time_record("legacy")
        assert rec is not None
        assert rec["is_next_day"] is False

    def test_legacy_plain_float_yesterday(self):
        mgr = self._mgr()
        mgr.save_data({"old": _yesterday_ts()})
        rec = mgr.get_time_record("old")
        assert rec is not None
        assert rec["is_next_day"] is True


# ──────────────────────────────────────────────────────────────────────────────
# StoreDataManager
# ──────────────────────────────────────────────────────────────────────────────

class TestStoreDataManager:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _mgr(self):
        from json_manager import StoreDataManager
        return StoreDataManager("store_device")

    def test_record_purchase_stores_utc_fields(self):
        mgr = self._mgr()
        mgr.record_purchase("item", {"qty": 1})
        rec = mgr.get_purchase_record("item")
        assert rec is not None
        assert "last_time_utc" in rec
        assert rec.get("_schema") == "utc_v1"

    def test_is_purchased_today_true_after_record(self):
        mgr = self._mgr()
        mgr.record_purchase("item", {})
        assert mgr.is_purchased_today("item") is True

    def test_is_purchased_today_false_when_no_record(self):
        mgr = self._mgr()
        assert mgr.is_purchased_today("ghost") is False

    def test_is_purchased_today_false_for_yesterday(self):
        mgr = self._mgr()
        yesterday_utc = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1)
        )
        mgr.save_data({
            "old_item": {
                "last_time_utc": yesterday_utc.isoformat().replace("+00:00", "Z"),
                "_schema": "utc_v1",
            }
        })
        assert mgr.is_purchased_today("old_item") is False

    def test_is_purchased_week_true_after_record(self):
        mgr = self._mgr()
        mgr.record_purchase("weekly_item", {})
        assert mgr.is_purchased_today("weekly_item", period="week") is True

    def test_is_purchased_week_false_for_last_week(self):
        mgr = self._mgr()
        last_week_utc = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=8)
        )
        mgr.save_data({
            "w": {
                "last_time_utc": last_week_utc.isoformat().replace("+00:00", "Z"),
                "_schema": "utc_v1",
            }
        })
        assert mgr.is_purchased_today("w", period="week") is False

    def test_count_auto_increments(self):
        mgr = self._mgr()
        mgr.record_purchase("item", {})
        mgr.record_purchase("item", {})
        rec = mgr.get_purchase_record("item")
        assert rec["count"] == 2

    def test_count_explicit_overrides(self):
        mgr = self._mgr()
        mgr.record_purchase("item", {"count": 99})
        rec = mgr.get_purchase_record("item")
        assert rec["count"] == 99

    def test_legacy_last_time_field_still_works(self):
        """Old records with last_time (UTC naive string) should be readable."""
        mgr = self._mgr()
        utc_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        mgr.save_data({"old": {"last_time": utc_str}})
        assert mgr.is_purchased_today("old") is True


# ──────────────────────────────────────────────────────────────────────────────
# is_record_expired
# ──────────────────────────────────────────────────────────────────────────────

class TestIsRecordExpired:
    def setup_method(self):
        from json_manager import is_record_expired
        self.fn = is_record_expired

    def test_none_record_is_expired(self):
        assert self.fn(None, 3600) is True

    def test_fresh_record_not_expired(self):
        rec = {"timestamp": _now_ts(), "is_next_day": False}
        assert self.fn(rec, 3600) is False

    def test_old_record_is_expired(self):
        rec = {"timestamp": _yesterday_ts(), "is_next_day": False}
        assert self.fn(rec, 3600) is True

    def test_next_day_flag_triggers_expiry(self):
        rec = {"timestamp": _now_ts(), "is_next_day": True}
        assert self.fn(rec, 3600, check_next_day=True) is True

    def test_next_day_flag_ignored_when_disabled(self):
        rec = {"timestamp": _now_ts(), "is_next_day": True}
        assert self.fn(rec, 3600, check_next_day=False) is False

    def test_zero_timestamp_is_expired(self):
        rec = {"timestamp": 0, "is_next_day": False}
        assert self.fn(rec, 3600) is True


# ──────────────────────────────────────────────────────────────────────────────
# is_timestamp_expired  (was module-level is_expired)
# ──────────────────────────────────────────────────────────────────────────────

class TestIsTimestampExpired:
    def setup_method(self):
        from json_manager import is_timestamp_expired
        self.fn = is_timestamp_expired

    def test_fresh_ts_not_expired(self):
        assert self.fn(_now_ts(), expired_time=3600) is False

    def test_old_ts_is_expired(self):
        assert self.fn(_yesterday_ts(), expired_time=3600) is True

    def test_next_day_cross_boundary(self):
        # yesterday is always next-day
        assert self.fn(_yesterday_ts()) is True


class TestIsExpiredAlias:
    """Module-level is_expired alias must still work for backward compat."""
    def test_alias_exists(self):
        from json_manager import is_expired
        assert callable(is_expired)

    def test_alias_same_as_is_timestamp_expired(self):
        from json_manager import is_expired, is_timestamp_expired
        ts = _now_ts()
        assert is_expired(ts, 3600) == is_timestamp_expired(ts, 3600)


# ──────────────────────────────────────────────────────────────────────────────
# should_execute_cycle_from_record
# ──────────────────────────────────────────────────────────────────────────────

class TestShouldExecuteCycleFromRecord:
    def setup_method(self):
        from json_manager import should_execute_cycle_from_record
        self.fn = should_execute_cycle_from_record

    def _today(self):
        return datetime.datetime.now(pytz.timezone("Asia/Taipei")).date()

    def test_no_record_should_execute(self):
        should, need = self.fn(None)
        assert should is True
        assert need is True

    def test_same_week_should_execute_and_need_record(self):
        rec = {"recorded_date": self._today().strftime("%Y-%m-%d")}
        should, need = self.fn(rec, cycle_weeks=4, today=self._today())
        assert should is True
        assert need is True

    def test_wrong_cycle_week_should_not_execute(self):
        # 2 weeks ago, cycle=4 → weeks_passed=2 → 2%4≠0 → False
        two_weeks_ago = self._today() - datetime.timedelta(weeks=2)
        rec = {"recorded_date": two_weeks_ago.strftime("%Y-%m-%d")}
        should, need = self.fn(rec, cycle_weeks=4, today=self._today())
        assert should is False

    def test_correct_cycle_week_should_execute(self):
        # 4 weeks ago, cycle=4 → weeks_passed=4 → 4%4==0 → True
        four_weeks_ago = self._today() - datetime.timedelta(weeks=4)
        rec = {"recorded_date": four_weeks_ago.strftime("%Y-%m-%d")}
        should, need = self.fn(rec, cycle_weeks=4, today=self._today())
        assert should is True

    def test_allowed_weekdays_filters_correctly(self):
        today = self._today()
        rec = {"recorded_date": today.strftime("%Y-%m-%d")}
        # today's weekday is always in [0..6], exclude it to get False
        excluded = [(today.weekday() + 1) % 7]
        should, _ = self.fn(rec, cycle_weeks=1, today=today, allowed_weekdays=excluded)
        assert should is False

    def test_allowed_weekdays_includes_today(self):
        today = self._today()
        rec = {"recorded_date": today.strftime("%Y-%m-%d")}
        should, _ = self.fn(rec, cycle_weeks=1, today=today, allowed_weekdays=[today.weekday()])
        assert should is True


# ──────────────────────────────────────────────────────────────────────────────
# should_execute_sea_with_cooldown — Taiwan-time 10:00-24:00 window gate
#
# Regression: legacy Sea.sea is invoked by the scheduler with no time-of-day
# gate, so it fired in the early-morning hours. At hour<10 it skipped its inner
# 航海 block but still ran the trailing blind-tap "return to main" sequence,
# leaving the game on an unknown page and cascading every later task to skip.
# The scheduler must refuse to run sea outside 10:00-24:00 (Taiwan time).
# ──────────────────────────────────────────────────────────────────────────────

class TestShouldExecuteSeaWindow:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        # No record files → cycle/cooldown gates would otherwise say "run".
        monkeypatch.chdir(tmp_path)

    def setup_method(self):
        from json_manager import should_execute_sea_with_cooldown
        self.fn = should_execute_sea_with_cooldown

    def _at(self, hour):
        # 日期錨在 is_sea_week 的航海週上(而非硬編 2026-05-29 碰巧落在航海週)，
        # 這樣改 _SEA_ANCHOR_MONDAY/_SEA_CYCLE_DAYS 時「should is True」案例不會
        # 因日期意外變成非航海週而假性失敗；precondition assert 讓真因一眼可見。
        from json_manager.scheduling import _SEA_ANCHOR_MONDAY, is_sea_week
        tpe = datetime.timezone(datetime.timedelta(hours=8))
        d = _SEA_ANCHOR_MONDAY + datetime.timedelta(days=2)  # 航海週的週三
        assert is_sea_week(d), "test date must be a sea-week — anchor changed?"
        return datetime.datetime(d.year, d.month, d.day, hour, 30, tzinfo=tpe)

    def test_before_window_blocked(self):
        # 02:30 凌晨 → blocked regardless of cycle/cooldown
        assert self.fn("emulator-5560", now=self._at(2)) == (False, False)

    def test_just_before_open_blocked(self):
        # 09:30 → still blocked
        assert self.fn("emulator-5560", now=self._at(9)) == (False, False)

    def test_window_open_lower_bound_not_blocked_by_window(self):
        # 10:00 boundary is inclusive → window does not block; with no record
        # the cycle/cooldown path then says run.
        should, _ = self.fn("emulator-5560", now=self._at(10))
        assert should is True

    def test_inside_window_not_blocked_by_window(self):
        should, _ = self.fn("emulator-5560", now=self._at(15))
        assert should is True

    def test_last_hour_inside_window(self):
        # 23:xx is the last allowed hour (window end 24 is exclusive on hour)
        should, _ = self.fn("emulator-5560", now=self._at(23))
        assert should is True


# ──────────────────────────────────────────────────────────────────────────────
# Backward compatibility wrappers
# ──────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatWrappers:
    @pytest.fixture(autouse=True)
    def isolated_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def test_time_recording_and_return_time(self):
        from json_manager import time_recording, return_time
        time_recording("dev1", "task_a")
        rec = return_time("dev1", "task_a")
        assert rec is not None
        assert rec["is_next_day"] is False

    def test_return_time_none_when_no_record(self):
        from json_manager import return_time
        assert return_time("dev2", "missing") is None

    def test_record_json_and_check_json(self):
        from json_manager import record_json, check_json
        record_json("dev3", "purchase_x", {"qty": 5})
        rec = check_json("dev3", "purchase_x")
        assert rec is not None
        assert rec["qty"] == 5

    def test_create_park_manager(self):
        from json_manager import create_park_manager, ParkMarketDataManager
        mgr = create_park_manager("dev4")
        assert isinstance(mgr, ParkMarketDataManager)

    def test_create_family_market_manager(self):
        from json_manager import create_family_market_manager, FamilyMarketDataManager
        mgr = create_family_market_manager("dev5")
        assert isinstance(mgr, FamilyMarketDataManager)

    def test_create_time_manager(self):
        from json_manager import create_time_manager, TimeRecordDataManager
        mgr = create_time_manager("dev6")
        assert isinstance(mgr, TimeRecordDataManager)

    def test_create_store_manager(self):
        from json_manager import create_store_manager, StoreDataManager
        mgr = create_store_manager("dev7")
        assert isinstance(mgr, StoreDataManager)

import json
import datetime

import config_manager
import pytest

from game_actions import special_wanshen


TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


class FakeManager:
    def __init__(self, now, records=None):
        self.now = now
        self.records = dict(records or {})
        self.recorded = []

    def get_record(self, name):
        return self.records.get(name)

    def record_timestamp(self, name):
        self.recorded.append(name)
        self.records[name] = {
            "timestamp": self.now.timestamp(),
            "date": self.now.strftime("%Y-%m-%d"),
        }


def test_special_wanshen_config_defaults():
    cfg = config_manager.DeviceConfig.from_dict({})

    assert cfg.get("special_wanshen_account") is False
    assert cfg.get("special_wanshen_enabled") is False
    assert cfg.get("special_wanshen_rounds") == 10


def test_special_wanshen_rounds_are_clamped(tmp_path, monkeypatch):
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({"devices": {"web-x": {}}}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(path))

    config_manager.update_device_config("web-x", {
        "special_wanshen_account": True,
        "special_wanshen_enabled": True,
        "special_wanshen_rounds": 999,
    })

    cfg = config_manager.get_device_config("web-x")
    assert cfg.get("special_wanshen_account") is True
    assert cfg.get("special_wanshen_enabled") is True
    assert cfg.get("special_wanshen_rounds") == 50


@pytest.mark.parametrize("hour", [3, 4, 5, 6])
def test_due_tuesday_to_saturday_during_early_window(hour):
    now = datetime.datetime(2026, 7, 7, hour, 30, tzinfo=TAIPEI)

    assert special_wanshen.is_due(
        now=now,
        account=True,
        enabled=True,
        attempted_today=False,
        completed_this_week=False,
    ) is True


def test_due_on_saturday_during_early_window():
    now = datetime.datetime(2026, 7, 11, 3, 0, tzinfo=TAIPEI)

    assert special_wanshen.is_due(
        now=now,
        account=True,
        enabled=True,
        attempted_today=False,
        completed_this_week=False,
    ) is True


@pytest.mark.parametrize("now", [
    datetime.datetime(2026, 7, 7, 2, 59, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 7, 7, 0, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 6, 4, 0, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 12, 4, 0, tzinfo=TAIPEI),
])
def test_not_due_outside_day_or_time_window(now):
    assert special_wanshen.is_due(
        now=now,
        account=True,
        enabled=True,
        attempted_today=False,
        completed_this_week=False,
    ) is False


def test_attempt_is_recorded_before_fight_and_success_records_week(monkeypatch):
    now = datetime.datetime(2026, 7, 7, 4, 0, tzinfo=TAIPEI)
    manager = FakeManager(now)
    calls = []

    result = special_wanshen.run_if_due(
        object(),
        "web-001",
        cfg={
            "special_wanshen_account": True,
            "special_wanshen_enabled": True,
            "special_wanshen_rounds": 10,
        },
        now=now,
        manager=manager,
        fight_fn=lambda d, rounds: calls.append(
            (rounds, list(manager.recorded))
        ) or True,
    )

    assert calls == [(10, [special_wanshen.ATTEMPT_RECORD])]
    assert manager.recorded == [
        special_wanshen.ATTEMPT_RECORD,
        special_wanshen.COMPLETE_RECORD,
    ]
    assert result["attempted_today"] is True
    assert result["completed_this_week"] is True


@pytest.mark.parametrize("fight_fn", [
    lambda d, rounds: False,
    lambda d, rounds: (_ for _ in ()).throw(RuntimeError("boom")),
])
def test_failed_fight_records_only_the_daily_attempt(fight_fn):
    now = datetime.datetime(2026, 7, 8, 4, 0, tzinfo=TAIPEI)
    manager = FakeManager(now)

    result = special_wanshen.run_if_due(
        object(),
        "web-001",
        cfg={
            "special_wanshen_account": True,
            "special_wanshen_enabled": True,
            "special_wanshen_rounds": 10,
        },
        now=now,
        manager=manager,
        fight_fn=fight_fn,
    )

    assert manager.recorded == [special_wanshen.ATTEMPT_RECORD]
    assert result["attempted_today"] is True
    assert result["completed_this_week"] is False


@pytest.mark.parametrize("record_name", [
    special_wanshen.ATTEMPT_RECORD,
    special_wanshen.COMPLETE_RECORD,
])
def test_existing_attempt_or_completion_prevents_another_fight(record_name):
    now = datetime.datetime(2026, 7, 9, 4, 0, tzinfo=TAIPEI)
    manager = FakeManager(now, {
        record_name: {"timestamp": now.timestamp(), "date": "2026-07-09"},
    })
    calls = []

    result = special_wanshen.run_if_due(
        object(),
        "web-001",
        cfg={
            "special_wanshen_account": True,
            "special_wanshen_enabled": True,
            "special_wanshen_rounds": 10,
        },
        now=now,
        manager=manager,
        fight_fn=lambda d, rounds: calls.append(rounds) or True,
    )

    assert calls == []
    assert result["due"] is False

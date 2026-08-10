"""Regression tests for periodic-task completion records."""

from __future__ import annotations

from game_actions import periodic_tasks


def test_periodic_cycle_does_not_record_completion_when_action_fails(monkeypatch):
    records: list[str] = []

    monkeypatch.setattr(
        periodic_tasks,
        "time_recording",
        lambda ip, name: records.append(name),
    )
    monkeypatch.setattr(periodic_tasks.time, "sleep", lambda *_args: None)

    result = periodic_tasks._run_periodic_cycle(
        "emulator-5554",
        "arena_completed",
        lambda _ip: (True, True),
        lambda **_kwargs: False,
        "競技活動",
        object(),
        cycle_record_name="arena_cycle_started",
    )

    assert result is False
    assert records == ["arena_cycle_started"]


def test_periodic_cycle_records_completion_after_action(monkeypatch):
    records: list[str] = []

    monkeypatch.setattr(
        periodic_tasks,
        "time_recording",
        lambda ip, name: records.append(name),
    )
    monkeypatch.setattr(periodic_tasks.time, "sleep", lambda *_args: None)

    result = periodic_tasks._run_periodic_cycle(
        "emulator-5554",
        "arena_completed",
        lambda _ip: (True, False),
        lambda **_kwargs: True,
        "競技活動",
        object(),
        daily_limit_name="arena_daily",
    )

    assert result is True
    assert records == ["arena_completed", "arena_daily"]

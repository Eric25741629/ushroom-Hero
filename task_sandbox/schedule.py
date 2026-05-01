"""Schedule types - predicates that decide whether a TaskSpec should run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol


class Schedule(Protocol):
    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool: ...


@dataclass(frozen=True)
class Always:
    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        return True


@dataclass(frozen=True)
class EveryHours:
    hours: int

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        if last_run is None:
            return True
        return (now - last_run) >= timedelta(hours=self.hours)


@dataclass(frozen=True)
class DailyOnce:
    reset_hour: int = 4

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        if last_run is None:
            return True
        return self._game_day(now) > self._game_day(last_run)

    def _game_day(self, t: datetime) -> int:
        shifted = t - timedelta(hours=self.reset_hour)
        return shifted.toordinal()


@dataclass(frozen=True)
class WeeklyOn:
    days: frozenset[int]

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        return now.weekday() in self.days


@dataclass(frozen=True)
class HourWindow:
    start_hour: int
    end_hour: int

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        return self.start_hour <= now.hour < self.end_hour


@dataclass(frozen=True)
class AndSchedule:
    a: Schedule
    b: Schedule

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        return self.a.should_run(ip, now, last_run) and self.b.should_run(ip, now, last_run)


@dataclass(frozen=True)
class Custom:
    fn: Callable[[str, datetime, datetime | None], bool]

    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool:
        return self.fn(ip, now, last_run)

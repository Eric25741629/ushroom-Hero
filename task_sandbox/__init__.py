"""task_sandbox - universal task development & verification harness.

Public API:
    from task_sandbox import TaskSpec, NavTarget, run_task
"""
from .nav_target import NavTarget
from .schedule import (
    Always,
    AndSchedule,
    Custom,
    DailyOnce,
    EveryHours,
    HourWindow,
    Schedule,
    WeeklyOn,
)
from .spec import TaskContext, TaskResult, TaskSpec, VerifyResult
from .runner import run_task

__all__ = [
    "NavTarget",
    "Schedule",
    "Always",
    "EveryHours",
    "DailyOnce",
    "WeeklyOn",
    "HourWindow",
    "AndSchedule",
    "Custom",
    "TaskSpec",
    "TaskContext",
    "TaskResult",
    "VerifyResult",
    "run_task",
]

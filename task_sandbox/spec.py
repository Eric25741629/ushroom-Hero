"""Value-object types for harness tasks.

`TaskSpec` is the declarative entry: harness reads it, drives the device
to `entry`, calls `runner`, then optionally `verifier`. The `runner`
callable can be any existing function - no subclassing required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .nav_target import NavTarget
from .schedule import Schedule


@dataclass
class TaskResult:
    ok: bool
    reason: str = ""
    artifacts: dict = field(default_factory=dict)


@dataclass
class VerifyResult:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


@dataclass
class TaskContext:
    device: Any
    ip: str
    cnn_model: Any
    recorder: Any
    config: dict
    timeout_at: float


Runner = Callable[[TaskContext], TaskResult]
Verifier = Callable[[TaskContext], VerifyResult]
EnabledWhen = Callable[[str], bool]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    entry: NavTarget
    schedule: Schedule
    runner: Runner
    verifier: Verifier | None = None
    enabled_when: EnabledWhen | None = None
    skip_devices: frozenset[str] = frozenset()
    timeout_sec: float = 120.0
    references: tuple[str, ...] = ()

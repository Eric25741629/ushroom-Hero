# Task Sandbox — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `task_sandbox/` skeleton with `run` mode, navigate-to MAIN_PAGE / LAMP_PAGE, structured trace (events + key-moment screenshots), and a `LAMP` TaskSpec that wraps `opengold_v2.LampService` zero-modification — runnable via `python -m task_sandbox run lamp --device <id>`.

**Architecture:** Declarative `TaskSpec` value-object (`spec.py`) drives a thin `runner.py` that uses `navigator.py` to drive the device to the task's entry state, then calls the spec's `runner` function. A `Recorder` (`trace/recorder.py`) appends JSON events to `runs/<run-id>/trace.jsonl` and snaps PNGs only on stage-change / assertion-fail / OCR-miss / error. Backend-agnostic via `MonitoredDevice`. Tests use a `FakeDevice` + injected stage resolver so navigator/runner unit tests run with no real device.

**Tech Stack:** Python 3.13, `dataclass(frozen=True)` for spec types, `typing.Protocol` for Schedule, `pytest` for tests, `argparse` for CLI, `MonitoredDevice` (existing) for device interface, `img_tools` (existing) for OCR-driven clicks, `opengold_v2` (existing) for lamp logic.

---

## File Structure (locked in before tasks)

```
task_sandbox/
├── __init__.py                     # public exports: TaskSpec, NavTarget, Schedule, run_task
├── __main__.py                     # delegates to cli.main()
├── cli.py                          # argparse: list / run subcommands
├── exceptions.py                   # NavigationFailed, TaskTimeout
├── nav_target.py                   # NavTarget enum (separate so handlers can import without cycle)
├── navigator.py                    # NAV_HANDLERS registry + navigate_to()
├── runner.py                       # run(spec, ctx) entry; verify/explore are TODO future phase
├── schedule.py                     # Schedule protocol + Always/EveryHours/DailyOnce/WeeklyOn/HourWindow/AndSchedule/Custom
├── spec.py                         # TaskSpec, TaskContext, TaskResult, VerifyResult dataclasses
├── trace/
│   ├── __init__.py
│   ├── recorder.py                 # Recorder
│   └── schema.py                   # TraceEvent TypedDict + helpers
└── tasks/
    ├── __init__.py                 # TASK_REGISTRY: dict[str, TaskSpec]
    └── lamp.py                     # LAMP

tests/
├── fakes/
│   ├── __init__.py
│   └── device.py                   # FakeDevice
├── test_task_sandbox_recorder.py
├── test_task_sandbox_schedule.py
├── test_task_sandbox_spec.py
├── test_task_sandbox_navigator.py
├── test_task_sandbox_runner.py
└── test_task_sandbox_cli.py

runs/                               # gitignored output dir
.gitignore                          # add 'runs/'
```

Decision boundary clarifications:
- `nav_target.py` is a tiny enum-only module so `tasks/lamp.py` can declare `entry=NavTarget.LAMP_PAGE` without importing `navigator.py` (which itself depends on `img_tools`).
- `navigator.py` keeps NAV_HANDLERS as a module-level dict populated at import time; nav handlers receive `(ctx)` and use `ctx.device`, `ctx.recorder`, plus an injected `stage_resolver` callable so tests can fake the stage.
- `runner.py` exposes `run(spec, *, device, ip, cnn_model, recorder, config, stage_resolver=None)` so callers can inject everything; CLI assembles the real defaults.
- `Recorder` writes to a session directory it creates; trace.jsonl is line-buffered.

---

### Task 1: Repo skeleton + .gitignore

**Files:**
- Create: `task_sandbox/__init__.py`
- Create: `task_sandbox/trace/__init__.py`
- Create: `task_sandbox/tasks/__init__.py`
- Create: `tests/fakes/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Create empty package init files**

```python
# task_sandbox/__init__.py
"""task_sandbox — universal task development & verification harness."""
```

```python
# task_sandbox/trace/__init__.py
```

```python
# task_sandbox/tasks/__init__.py
"""TaskSpec registry for the harness."""

TASK_REGISTRY: dict[str, "TaskSpec"] = {}  # populated by tasks.lamp etc on import
```

```python
# tests/fakes/__init__.py
```

- [ ] **Step 2: Add `runs/` to .gitignore**

Append to `.gitignore`:

```
# task_sandbox session outputs (large screenshots/videos)
runs/
```

- [ ] **Step 3: Verify imports work**

Run: `python -c "import task_sandbox; import task_sandbox.trace; import task_sandbox.tasks; print('ok')"`
Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add task_sandbox/__init__.py task_sandbox/trace/__init__.py task_sandbox/tasks/__init__.py tests/fakes/__init__.py .gitignore
git commit -m "task_sandbox: skeleton packages + runs/ gitignore"
```

---

### Task 2: TraceEvent schema

**Files:**
- Create: `task_sandbox/trace/schema.py`
- Test: `tests/test_task_sandbox_recorder.py` (just import probe; full tests in Task 3)

- [ ] **Step 1: Write the schema module**

```python
# task_sandbox/trace/schema.py
"""Trace event format. Each line in trace.jsonl is one TraceEvent JSON."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

EventKind = Literal[
    "click", "swipe", "screenshot", "ocr", "wait_for",
    "stage_check", "assertion", "nav_step", "error",
    "span_start", "span_end", "video_start", "video_end",
    "task_start", "task_end",
]


class TraceEvent(TypedDict, total=False):
    ts: float
    seq: int
    kind: EventKind
    args: dict[str, Any]
    stage_before: str | None
    stage_after: str | None
    ok: bool
    elapsed_ms: int
    screenshot_path: str | None
    parent_span: str | None
    msg: str
```

- [ ] **Step 2: Write the import probe test**

```python
# tests/test_task_sandbox_recorder.py
"""Recorder + trace schema tests."""
from task_sandbox.trace.schema import TraceEvent, EventKind  # noqa: F401


def test_schema_imports():
    """Schema module is importable."""
    pass
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_task_sandbox_recorder.py::test_schema_imports -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add task_sandbox/trace/schema.py tests/test_task_sandbox_recorder.py
git commit -m "task_sandbox: TraceEvent schema (TypedDict)"
```

---

### Task 3: Recorder

**Files:**
- Create: `task_sandbox/trace/recorder.py`
- Modify: `tests/test_task_sandbox_recorder.py`

- [ ] **Step 1: Write failing tests for Recorder basic event + jsonl output**

Append to `tests/test_task_sandbox_recorder.py`:

```python
import json
from pathlib import Path

import pytest

from task_sandbox.trace.recorder import Recorder


def test_recorder_writes_event_to_jsonl(tmp_path: Path):
    rec = Recorder(tmp_path)
    rec.event("click", x=100, y=200)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["kind"] == "click"
    assert obj["args"] == {"x": 100, "y": 200}
    assert obj["seq"] == 0
    assert obj["ok"] is True
    assert "ts" in obj


def test_recorder_seq_increments(tmp_path: Path):
    rec = Recorder(tmp_path)
    rec.event("click", x=1, y=2)
    rec.event("click", x=3, y=4)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [0, 1]


def test_recorder_assertion_failure_triggers_screenshot(tmp_path: Path):
    """Assertion with ok=False must capture a screenshot."""
    rec = Recorder(tmp_path)

    class FakePngDevice:
        def screenshot(self, format: str = "opencv"):
            import numpy as np
            return np.zeros((10, 10, 3), dtype=np.uint8)

    rec.bind_device(FakePngDevice())
    rec.assertion("on_main_page", ok=False, detail="actual=lamp_page")
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[0])
    assert obj["kind"] == "assertion"
    assert obj["ok"] is False
    assert obj["screenshot_path"] is not None
    assert (tmp_path / obj["screenshot_path"]).exists()


def test_recorder_span_emits_start_and_end(tmp_path: Path):
    rec = Recorder(tmp_path)
    with rec.span("navigate"):
        rec.event("click", x=1, y=2)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["span_start", "click", "span_end"]


def test_recorder_stage_check_screenshots_on_change(tmp_path: Path):
    rec = Recorder(tmp_path)

    class FakePngDevice:
        def screenshot(self, format: str = "opencv"):
            import numpy as np
            return np.zeros((10, 10, 3), dtype=np.uint8)

    rec.bind_device(FakePngDevice())
    rec.stage_check(before="main_page", after="lamp_page")
    rec.stage_check(before="lamp_page", after="lamp_page")  # no change
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    objs = [json.loads(line) for line in lines]
    assert objs[0]["screenshot_path"] is not None  # changed
    assert objs[1].get("screenshot_path") is None  # unchanged
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_task_sandbox_recorder.py -v`
Expected: 5 tests fail with `ImportError: cannot import name 'Recorder'`.

- [ ] **Step 3: Implement Recorder**

Create `task_sandbox/trace/recorder.py`:

```python
"""Recorder — writes structured events to runs/<run-id>/trace.jsonl
and snaps screenshots only on key moments (stage change, assertion fail,
ocr miss, error, explicit request).
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

import cv2

from .schema import EventKind, TraceEvent


class Recorder:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self._fp = (self.run_dir / "trace.jsonl").open("a", encoding="utf-8")
        self._seq = 0
        self._device: Any = None
        self._span_stack: list[str] = []

    def bind_device(self, device: Any) -> None:
        self._device = device

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def event(
        self,
        kind: EventKind,
        *,
        ok: bool = True,
        stage_before: str | None = None,
        stage_after: str | None = None,
        elapsed_ms: int = 0,
        msg: str = "",
        screenshot_reason: str | None = None,
        **args: Any,
    ) -> TraceEvent:
        screenshot_path: str | None = None
        if screenshot_reason:
            screenshot_path = self._snap(reason=screenshot_reason)
        ev: TraceEvent = {
            "ts": time.time(),
            "seq": self._seq,
            "kind": kind,
            "args": args,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "stage_before": stage_before,
            "stage_after": stage_after,
            "screenshot_path": screenshot_path,
            "parent_span": self._span_stack[-1] if self._span_stack else None,
            "msg": msg,
        }
        self._fp.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._fp.flush()
        self._seq += 1
        return ev

    def assertion(self, name: str, *, ok: bool, detail: str = "") -> None:
        self.event(
            "assertion",
            ok=ok,
            name=name,
            detail=detail,
            screenshot_reason=None if ok else f"assertion_fail:{name}",
        )

    def stage_check(self, *, before: str, after: str) -> None:
        changed = before != after
        self.event(
            "stage_check",
            stage_before=before,
            stage_after=after,
            screenshot_reason="stage_change" if changed else None,
        )

    def ocr_miss(self, target: str, detail: str = "") -> None:
        self.event(
            "ocr",
            ok=False,
            target=target,
            detail=detail,
            screenshot_reason=f"ocr_miss:{target}",
        )

    def error(self, exc: BaseException) -> None:
        self.event(
            "error",
            ok=False,
            type=type(exc).__name__,
            msg=str(exc),
            screenshot_reason="error",
        )

    @contextlib.contextmanager
    def span(self, name: str) -> Iterator[None]:
        self.event("span_start", name=name)
        self._span_stack.append(name)
        try:
            yield
        finally:
            self._span_stack.pop()
            self.event("span_end", name=name)

    def screenshot(self, reason: str) -> str | None:
        return self._snap(reason=reason)

    def _snap(self, reason: str) -> str | None:
        if self._device is None:
            return None
        try:
            img = self._device.screenshot(format="opencv")
        except Exception:
            return None
        if img is None:
            return None
        safe_reason = reason.replace("/", "_").replace(":", "_")[:60]
        filename = f"{self._seq:03d}_{safe_reason}.png"
        path = self.run_dir / "screenshots" / filename
        cv2.imwrite(str(path), img)
        rel = f"screenshots/{filename}"
        return rel
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_task_sandbox_recorder.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add task_sandbox/trace/recorder.py tests/test_task_sandbox_recorder.py
git commit -m "task_sandbox: Recorder with key-moment screenshot capture"
```

---

### Task 4: FakeDevice fixture

**Files:**
- Create: `tests/fakes/device.py`

- [ ] **Step 1: Write FakeDevice (no test of its own; it is test infra)**

```python
# tests/fakes/device.py
"""In-memory MonitoredDevice substitute for harness unit tests.

Records every interaction so tests can assert on what the harness drove
the device to do, and lets tests pre-script a stage sequence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FakeDevice:
    ip: str = "fake-device"
    stages: list[str] = field(default_factory=lambda: ["主頁面"])
    clicks: list[tuple[int, int]] = field(default_factory=list)
    swipes: list[tuple[int, int, int, int]] = field(default_factory=list)

    def screenshot(self, format: str = "opencv") -> np.ndarray:
        return np.zeros((960, 540, 3), dtype=np.uint8)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def tap(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self.swipes.append((x1, y1, x2, y2))

    def app_stop(self, _pkg: str) -> None:
        pass

    def info(self) -> dict[str, Any]:
        return {"package": "com.mxdzz.tw.and"}

    def pop_stage(self) -> str:
        if len(self.stages) > 1:
            return self.stages.pop(0)
        return self.stages[0]
```

- [ ] **Step 2: Verify import**

Run: `python -c "from tests.fakes.device import FakeDevice; d = FakeDevice(); d.click(1,2); print(d.clicks)"`
Expected output: `[(1, 2)]`

- [ ] **Step 3: Commit**

```bash
git add tests/fakes/device.py
git commit -m "task_sandbox tests: FakeDevice fixture"
```

---

### Task 5: Schedule types

**Files:**
- Create: `task_sandbox/schedule.py`
- Create: `tests/test_task_sandbox_schedule.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_task_sandbox_schedule.py
from datetime import datetime, timedelta

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
    now = _dt(day=3, hour=5)  # past 4am reset on day 3
    assert DailyOnce(reset_hour=4).should_run("ip", now, last) is True


def test_weekly_on_runs_only_on_listed_days():
    monday = _dt(year=2026, month=5, day=4)  # 2026-05-04 is Monday
    sunday = _dt(year=2026, month=5, day=3)  # 2026-05-03 is Sunday
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
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_task_sandbox_schedule.py -v`
Expected: ImportError on `task_sandbox.schedule`.

- [ ] **Step 3: Implement schedule**

```python
# task_sandbox/schedule.py
"""Schedule types — predicates that decide whether a TaskSpec should run."""
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_task_sandbox_schedule.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add task_sandbox/schedule.py tests/test_task_sandbox_schedule.py
git commit -m "task_sandbox: Schedule types (Always/EveryHours/DailyOnce/WeeklyOn/HourWindow/And/Custom)"
```

---

### Task 6: NavTarget enum + exceptions

**Files:**
- Create: `task_sandbox/nav_target.py`
- Create: `task_sandbox/exceptions.py`

- [ ] **Step 1: Write modules**

```python
# task_sandbox/nav_target.py
"""Logical destinations the navigator can drive a device to.

Kept separate from navigator.py so TaskSpec modules can import it
without pulling in img_tools / OCR dependencies.
"""
from __future__ import annotations

from enum import Enum


class NavTarget(str, Enum):
    MAIN_PAGE = "main_page"
    LAMP_PAGE = "lamp_page"
```

```python
# task_sandbox/exceptions.py
"""Harness-level exceptions."""
from __future__ import annotations


class HarnessError(Exception):
    pass


class NavigationFailed(HarnessError):
    def __init__(self, target: str, reason: str):
        super().__init__(f"navigate to {target} failed: {reason}")
        self.target = target
        self.reason = reason


class TaskTimeout(HarnessError):
    pass
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from task_sandbox.nav_target import NavTarget; from task_sandbox.exceptions import NavigationFailed; print(NavTarget.LAMP_PAGE.value)"`
Expected output: `lamp_page`

- [ ] **Step 3: Commit**

```bash
git add task_sandbox/nav_target.py task_sandbox/exceptions.py
git commit -m "task_sandbox: NavTarget enum + harness exceptions"
```

---

### Task 7: Spec types (TaskSpec / TaskContext / TaskResult / VerifyResult)

**Files:**
- Create: `task_sandbox/spec.py`
- Create: `tests/test_task_sandbox_spec.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_task_sandbox_spec.py
from task_sandbox.nav_target import NavTarget
from task_sandbox.schedule import EveryHours
from task_sandbox.spec import TaskResult, TaskSpec, VerifyResult


def test_taskresult_defaults():
    r = TaskResult(ok=True)
    assert r.ok is True
    assert r.reason == ""
    assert r.artifacts == {}


def test_taskspec_minimal():
    def runner(ctx):
        return TaskResult(ok=True)

    spec = TaskSpec(
        name="x",
        entry=NavTarget.MAIN_PAGE,
        schedule=EveryHours(hours=1),
        runner=runner,
    )
    assert spec.name == "x"
    assert spec.entry == NavTarget.MAIN_PAGE
    assert spec.verifier is None
    assert spec.enabled_when is None
    assert spec.skip_devices == frozenset()
    assert spec.timeout_sec == 120.0
    assert spec.references == ()


def test_verifyresult_records_checks():
    r = VerifyResult(ok=False, checks=[("on_lamp_page", False, "actual=main")])
    assert r.ok is False
    assert r.checks[0][1] is False
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_task_sandbox_spec.py -v`
Expected: ImportError on `task_sandbox.spec`.

- [ ] **Step 3: Write spec.py**

```python
# task_sandbox/spec.py
"""Value-object types for harness tasks.

`TaskSpec` is the declarative entry: harness reads it, drives the device
to `entry`, calls `runner`, then optionally `verifier`. The `runner`
callable can be any existing function — no subclassing required.
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_task_sandbox_spec.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add task_sandbox/spec.py tests/test_task_sandbox_spec.py
git commit -m "task_sandbox: TaskSpec / TaskContext / TaskResult / VerifyResult"
```

---

### Task 8: Public exports in `__init__.py`

**Files:**
- Modify: `task_sandbox/__init__.py`

- [ ] **Step 1: Replace contents**

```python
# task_sandbox/__init__.py
"""task_sandbox — universal task development & verification harness.

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
]
```

- [ ] **Step 2: Smoke import**

Run: `python -c "from task_sandbox import TaskSpec, NavTarget, EveryHours; print('ok')"`
Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add task_sandbox/__init__.py
git commit -m "task_sandbox: re-export public types from package root"
```

---

### Task 9: Navigator — registry + main_page handler

**Files:**
- Create: `task_sandbox/navigator.py`
- Create: `tests/test_task_sandbox_navigator.py`

The navigator drives the device to a `NavTarget`. To keep tests isolated
from the real CNN model + img_tools server, both `stage_resolver` and
`navigator handlers` accept hooks that tests can substitute.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_task_sandbox_navigator.py
from pathlib import Path

import pytest

from task_sandbox import NavTarget
from task_sandbox.exceptions import NavigationFailed
from task_sandbox.navigator import navigate_to
from task_sandbox.spec import TaskContext
from task_sandbox.trace.recorder import Recorder
from tests.fakes.device import FakeDevice


def _ctx(tmp_path, device, *, ip="fake"):
    rec = Recorder(tmp_path)
    return TaskContext(
        device=device,
        ip=ip,
        cnn_model=None,
        recorder=rec,
        config={},
        timeout_at=1e18,
    )


def test_navigate_main_page_when_already_there(tmp_path: Path):
    d = FakeDevice(stages=["主頁面"])
    ctx = _ctx(tmp_path, d)
    stages = ["主頁面"]

    def stage_resolver(_ctx):
        return stages.pop(0) if stages else "主頁面"

    navigate_to(ctx, NavTarget.MAIN_PAGE, stage_resolver=stage_resolver)
    assert d.clicks == []  # no nav action needed


def test_navigate_main_page_drives_recovery(tmp_path: Path):
    d = FakeDevice()
    ctx = _ctx(tmp_path, d)

    drove = {"main_recovery": 0}
    stages = iter(["lamp_page", "主頁面"])

    def stage_resolver(_ctx):
        return next(stages)

    def fake_main(_ctx):
        drove["main_recovery"] += 1

    navigate_to(
        ctx, NavTarget.MAIN_PAGE,
        stage_resolver=stage_resolver,
        handler_overrides={NavTarget.MAIN_PAGE: fake_main},
    )
    assert drove["main_recovery"] == 1


def test_navigate_raises_after_repeated_failure(tmp_path: Path):
    d = FakeDevice()
    ctx = _ctx(tmp_path, d)

    def stage_resolver(_ctx):
        return "unknown_screen"

    def fake_main(_ctx):
        pass  # does nothing — stays on unknown_screen

    with pytest.raises(NavigationFailed):
        navigate_to(
            ctx, NavTarget.MAIN_PAGE,
            stage_resolver=stage_resolver,
            handler_overrides={NavTarget.MAIN_PAGE: fake_main},
            max_attempts=3,
        )
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_task_sandbox_navigator.py -v`
Expected: ImportError on `task_sandbox.navigator`.

- [ ] **Step 3: Implement navigator (main_page-only path; lamp added in Task 10)**

```python
# task_sandbox/navigator.py
"""Navigator — drives a device to a NavTarget.

Algorithm:
    1. Resolve current stage via stage_resolver(ctx).
    2. If already at target, return.
    3. Otherwise: drive MAIN_PAGE recovery, then run target's handler.
    4. Re-resolve; if still not at target, retry up to max_attempts.
    5. After max_attempts, raise NavigationFailed.

Stage names follow the existing repo convention ("主頁面", "lamp_page", …).
TARGET_STAGE_NAMES maps each NavTarget to the stage string the resolver
returns when arrived.
"""
from __future__ import annotations

from typing import Callable

from .exceptions import NavigationFailed
from .nav_target import NavTarget
from .spec import TaskContext

NavHandler = Callable[[TaskContext], None]
StageResolver = Callable[[TaskContext], str]

TARGET_STAGE_NAMES: dict[NavTarget, str] = {
    NavTarget.MAIN_PAGE: "主頁面",
    NavTarget.LAMP_PAGE: "lamp_page",
}


def _main_page_recovery(ctx: TaskContext) -> None:
    """Default MAIN_PAGE recovery: stop the game app and let the outer
    runtime re-launch via existing flow.
    Real production callers always pre-arrive on main page before running
    a task, so this fallback only fires when something has gone badly
    wrong (LLM-driven explore session, dev manually stuck, etc.).
    """
    try:
        ctx.device.app_stop("com.mxdzz.tw.and")
    except Exception:
        pass


NAV_HANDLERS: dict[NavTarget, NavHandler] = {
    NavTarget.MAIN_PAGE: _main_page_recovery,
}


def navigate_to(
    ctx: TaskContext,
    target: NavTarget,
    *,
    stage_resolver: StageResolver,
    handler_overrides: dict[NavTarget, NavHandler] | None = None,
    max_attempts: int = 3,
) -> None:
    handlers = {**NAV_HANDLERS, **(handler_overrides or {})}
    target_stage = TARGET_STAGE_NAMES[target]

    current = stage_resolver(ctx)
    if current == target_stage:
        ctx.recorder.event("nav_step", target=target.value, action="already_there", stage_after=current)
        return

    last_error = None
    for attempt in range(1, max_attempts + 1):
        with ctx.recorder.span(f"nav:{target.value}:attempt{attempt}"):
            if target != NavTarget.MAIN_PAGE:
                handlers[NavTarget.MAIN_PAGE](ctx)
                main_check = stage_resolver(ctx)
                ctx.recorder.event(
                    "nav_step", target="main_page",
                    stage_after=main_check, ok=(main_check == "主頁面"),
                )
                if main_check != "主頁面":
                    last_error = f"main_page recovery returned {main_check}"
                    continue

            handlers[target](ctx)
            after = stage_resolver(ctx)
            ctx.recorder.event(
                "nav_step", target=target.value,
                stage_after=after, ok=(after == target_stage),
            )
            if after == target_stage:
                return
            last_error = f"after handler stage={after}, expected={target_stage}"

    raise NavigationFailed(target.value, last_error or "exhausted attempts")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_task_sandbox_navigator.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add task_sandbox/navigator.py tests/test_task_sandbox_navigator.py
git commit -m "task_sandbox: navigate_to() with main_page recovery + retry"
```

---

### Task 10: Navigator — LAMP_PAGE handler

**Files:**
- Modify: `task_sandbox/navigator.py`
- Modify: `tests/test_task_sandbox_navigator.py`

- [ ] **Step 1: Add failing test for lamp_page navigation**

Append to `tests/test_task_sandbox_navigator.py`:

```python
def test_navigate_lamp_page_calls_lamp_handler(tmp_path: Path):
    d = FakeDevice()
    ctx = _ctx(tmp_path, d)

    stages = iter(["主頁面", "主頁面", "lamp_page"])

    def stage_resolver(_ctx):
        return next(stages)

    called = {"count": 0}

    def fake_lamp(_ctx):
        called["count"] += 1
        # simulate the click that takes us to the lamp page

    navigate_to(
        ctx, NavTarget.LAMP_PAGE,
        stage_resolver=stage_resolver,
        handler_overrides={NavTarget.LAMP_PAGE: fake_lamp},
    )
    assert called["count"] == 1
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_task_sandbox_navigator.py::test_navigate_lamp_page_calls_lamp_handler -v`
Expected: KeyError or NavigationFailed (LAMP_PAGE not in NAV_HANDLERS).

- [ ] **Step 3: Add lamp handler to navigator.py**

In `task_sandbox/navigator.py`, after `_main_page_recovery`:

```python
def _lamp_page_handler(ctx: TaskContext) -> None:
    """Drive main_page → lamp_page by clicking the 神燈 icon.

    Uses img_tools.click_str_by_server (the existing OCR-driven click).
    On miss, recorder logs an ocr_miss event with screenshot.
    """
    import img_tools  # local import: img_tools loads paddle/cv2 at import time

    found = img_tools.click_str_by_server(ctx.device, "神燈", wait_timeout=5)
    if not found:
        ctx.recorder.ocr_miss("神燈", detail="lamp icon not on main page")
        return
    ctx.recorder.event("click", target="神燈", via="ocr")
    import time as _time
    _time.sleep(2.0)
```

And update the registry:

```python
NAV_HANDLERS: dict[NavTarget, NavHandler] = {
    NavTarget.MAIN_PAGE: _main_page_recovery,
    NavTarget.LAMP_PAGE: _lamp_page_handler,
}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_task_sandbox_navigator.py -v`
Expected: 4 PASS (the new test passes because the handler is overridden in the test).

- [ ] **Step 5: Commit**

```bash
git add task_sandbox/navigator.py tests/test_task_sandbox_navigator.py
git commit -m "task_sandbox: lamp_page nav handler (clicks 神燈 via OCR)"
```

---

### Task 11: Runner — `run` mode

**Files:**
- Create: `task_sandbox/runner.py`
- Create: `tests/test_task_sandbox_runner.py`

The runner accepts a TaskSpec + a fully-built TaskContext, drives nav,
calls the runner function, optionally calls verifier. It does NOT consult
schedule (that decision belongs to the caller — pipeline or CLI).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_task_sandbox_runner.py
from datetime import datetime
from pathlib import Path

import pytest

from task_sandbox import EveryHours, NavTarget, TaskResult, TaskSpec, VerifyResult
from task_sandbox.runner import run_task
from task_sandbox.spec import TaskContext
from task_sandbox.trace.recorder import Recorder
from tests.fakes.device import FakeDevice


def _build(tmp_path, spec, *, stage="主頁面"):
    rec = Recorder(tmp_path)
    rec.bind_device(FakeDevice())
    ctx = TaskContext(
        device=FakeDevice(),
        ip="fake",
        cnn_model=None,
        recorder=rec,
        config={},
        timeout_at=1e18,
    )

    def stage_resolver(_):
        return stage

    return ctx, stage_resolver, rec


def _spec(runner, *, entry=NavTarget.MAIN_PAGE, verifier=None):
    return TaskSpec(
        name="t",
        entry=entry,
        schedule=EveryHours(hours=1),
        runner=runner,
        verifier=verifier,
    )


def test_run_task_success_emits_task_start_and_end(tmp_path: Path):
    def runner(ctx):
        return TaskResult(ok=True, reason="done")

    ctx, resolver, rec = _build(tmp_path, _spec(runner))
    result = run_task(_spec(runner), ctx, stage_resolver=resolver)
    assert result.ok is True

    import json
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds[0] == "task_start"
    assert kinds[-1] == "task_end"


def test_run_task_failure_recorded(tmp_path: Path):
    def runner(ctx):
        return TaskResult(ok=False, reason="boom")

    ctx, resolver, rec = _build(tmp_path, _spec(runner))
    result = run_task(_spec(runner), ctx, stage_resolver=resolver)
    assert result.ok is False
    assert result.reason == "boom"


def test_run_task_runner_exception_caught(tmp_path: Path):
    def runner(ctx):
        raise RuntimeError("explode")

    ctx, resolver, rec = _build(tmp_path, _spec(runner))
    result = run_task(_spec(runner), ctx, stage_resolver=resolver)
    assert result.ok is False
    assert "explode" in result.reason


def test_run_task_calls_verifier_when_present(tmp_path: Path):
    def runner(ctx):
        return TaskResult(ok=True)

    def verifier(ctx):
        return VerifyResult(ok=True, checks=[("on_main", True, "")])

    ctx, resolver, rec = _build(tmp_path, _spec(runner, verifier=verifier))
    result = run_task(_spec(runner, verifier=verifier), ctx, stage_resolver=resolver)
    assert result.ok is True
    assert result.artifacts["verify"]["ok"] is True


def test_run_task_skip_devices_short_circuits(tmp_path: Path):
    def runner(ctx):
        raise AssertionError("should not run")

    spec = TaskSpec(
        name="t",
        entry=NavTarget.MAIN_PAGE,
        schedule=EveryHours(hours=1),
        runner=runner,
        skip_devices=frozenset({"fake"}),
    )
    ctx, resolver, rec = _build(tmp_path, spec)
    result = run_task(spec, ctx, stage_resolver=resolver)
    assert result.ok is True
    assert "skipped" in result.reason
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_task_sandbox_runner.py -v`
Expected: ImportError on `task_sandbox.runner`.

- [ ] **Step 3: Implement runner.py**

```python
# task_sandbox/runner.py
"""Runner — wires TaskSpec + TaskContext through navigator → runner → verifier."""
from __future__ import annotations

import time
from typing import Callable

from .exceptions import HarnessError
from .navigator import navigate_to
from .spec import TaskContext, TaskResult, TaskSpec


def run_task(
    spec: TaskSpec,
    ctx: TaskContext,
    *,
    stage_resolver: Callable[[TaskContext], str],
) -> TaskResult:
    rec = ctx.recorder

    if ctx.ip in spec.skip_devices:
        rec.event("task_start", task=spec.name, skipped=True, reason="skip_devices")
        result = TaskResult(ok=True, reason=f"skipped:device={ctx.ip}")
        rec.event("task_end", task=spec.name, ok=True, reason=result.reason)
        return result

    if spec.enabled_when is not None and not spec.enabled_when(ctx.ip):
        rec.event("task_start", task=spec.name, skipped=True, reason="enabled_when=false")
        result = TaskResult(ok=True, reason="skipped:enabled_when")
        rec.event("task_end", task=spec.name, ok=True, reason=result.reason)
        return result

    rec.event("task_start", task=spec.name, entry=spec.entry.value)
    started = time.time()

    try:
        navigate_to(ctx, spec.entry, stage_resolver=stage_resolver)
    except HarnessError as e:
        rec.error(e)
        result = TaskResult(ok=False, reason=f"nav_failed:{e}")
        rec.event("task_end", task=spec.name, ok=False, reason=result.reason,
                  elapsed_ms=int((time.time() - started) * 1000))
        return result

    try:
        result = spec.runner(ctx)
    except Exception as e:
        rec.error(e)
        result = TaskResult(ok=False, reason=f"runner_exc:{type(e).__name__}:{e}")
        rec.event("task_end", task=spec.name, ok=False, reason=result.reason,
                  elapsed_ms=int((time.time() - started) * 1000))
        return result

    if spec.verifier is not None:
        try:
            verify = spec.verifier(ctx)
            result.artifacts["verify"] = {
                "ok": verify.ok,
                "checks": verify.checks,
            }
            if not verify.ok:
                result.ok = False
                if not result.reason:
                    result.reason = "verify_failed"
        except Exception as e:
            rec.error(e)
            result.artifacts["verify"] = {"ok": False, "error": str(e)}

    rec.event("task_end", task=spec.name, ok=result.ok, reason=result.reason,
              elapsed_ms=int((time.time() - started) * 1000))
    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_task_sandbox_runner.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Add `run_task` to package exports**

In `task_sandbox/__init__.py`, append to imports:

```python
from .runner import run_task
```

And add `"run_task"` to `__all__`.

- [ ] **Step 6: Smoke import**

Run: `python -c "from task_sandbox import run_task; print('ok')"`
Expected output: `ok`

- [ ] **Step 7: Commit**

```bash
git add task_sandbox/runner.py task_sandbox/__init__.py tests/test_task_sandbox_runner.py
git commit -m "task_sandbox: run_task — nav + runner + optional verifier"
```

---

### Task 12: LAMP TaskSpec

**Files:**
- Create: `task_sandbox/tasks/lamp.py`
- Modify: `task_sandbox/tasks/__init__.py`

- [ ] **Step 1: Write LAMP TaskSpec**

```python
# task_sandbox/tasks/lamp.py
"""LAMP TaskSpec — wraps opengold_v2.LampService zero-modification."""
from __future__ import annotations

from task_sandbox import EveryHours, NavTarget, TaskResult, TaskSpec
from task_sandbox.spec import TaskContext


def _lamp_runner(ctx: TaskContext) -> TaskResult:
    from opengold_v2 import LampService, OpenGoldConfig

    cfg = OpenGoldConfig()
    svc = LampService(ctx.device, cfg, device_ip=ctx.ip)
    times = int(ctx.config.get("lamp_times", 1000))
    try:
        svc.run(times=times, is_compare=True)
    except Exception as e:
        return TaskResult(ok=False, reason=f"lamp_service_exc:{type(e).__name__}:{e}")
    return TaskResult(ok=True)


def _lamp_enabled(ip: str) -> bool:
    try:
        import config_manager
        return bool(config_manager.get_device_config(ip).get("lamp_check_interval"))
    except Exception:
        return True


LAMP = TaskSpec(
    name="lamp",
    entry=NavTarget.LAMP_PAGE,
    schedule=EveryHours(hours=2),
    runner=_lamp_runner,
    enabled_when=_lamp_enabled,
    references=(
        "opengold_v2/lamp_service.py",
        "opengold_v2/ui_controller.py",
        "Open_gold_paddle_ocr.py",
    ),
)
```

- [ ] **Step 2: Register in `tasks/__init__.py`**

Replace contents of `task_sandbox/tasks/__init__.py`:

```python
"""TaskSpec registry — populated at import time by individual task modules."""
from __future__ import annotations

from task_sandbox.spec import TaskSpec

from . import lamp as _lamp_module

TASK_REGISTRY: dict[str, TaskSpec] = {
    _lamp_module.LAMP.name: _lamp_module.LAMP,
}

__all__ = ["TASK_REGISTRY"]
```

- [ ] **Step 3: Smoke import**

Run: `python -c "from task_sandbox.tasks import TASK_REGISTRY; print(list(TASK_REGISTRY))"`
Expected output: `['lamp']`

- [ ] **Step 4: Commit**

```bash
git add task_sandbox/tasks/lamp.py task_sandbox/tasks/__init__.py
git commit -m "task_sandbox: LAMP TaskSpec wrapping opengold_v2.LampService"
```

---

### Task 13: CLI — `list` and `run` subcommands

**Files:**
- Create: `task_sandbox/cli.py`
- Create: `task_sandbox/__main__.py`
- Create: `tests/test_task_sandbox_cli.py`

- [ ] **Step 1: Write failing tests for `list` subcommand**

```python
# tests/test_task_sandbox_cli.py
from io import StringIO
from pathlib import Path

import pytest

from task_sandbox.cli import build_parser, run_list


def test_list_subcommand_outputs_registered_tasks(capsys):
    args = build_parser().parse_args(["list"])
    run_list(args)
    out = capsys.readouterr().out
    assert "lamp" in out
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_task_sandbox_cli.py -v`
Expected: ImportError on `task_sandbox.cli`.

- [ ] **Step 3: Write cli.py**

```python
# task_sandbox/cli.py
"""task_sandbox CLI — `python -m task_sandbox <subcommand>`."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .runner import run_task
from .spec import TaskContext
from .tasks import TASK_REGISTRY
from .trace.recorder import Recorder


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="task_sandbox")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list registered TaskSpecs")

    run_p = sub.add_parser("run", help="run a single task end-to-end")
    run_p.add_argument("task", help="task name (see `list`)")
    run_p.add_argument("--device", required=True, help="adb serial / device ip")
    run_p.add_argument("--out", default="runs", help="output dir for trace + screenshots")
    run_p.add_argument("--timeout", type=float, default=600.0)

    return p


def run_list(_args: argparse.Namespace) -> int:
    for name, spec in sorted(TASK_REGISTRY.items()):
        print(f"{name:<24} entry={spec.entry.value:<12} schedule={type(spec.schedule).__name__}")
    return 0


def _build_runtime_context(device_ip: str, out_root: Path, task_name: str, timeout_sec: float) -> tuple[TaskContext, Any]:
    """Connect to a real device and produce a TaskContext + stage_resolver.

    Imports adb_operations / config_manager / cnn_model lazily so unit
    tests of the CLI parser don't require those heavy modules.
    """
    from adb_operations import connect_u2_with_retries
    from device_wrapper import MonitoredDevice
    from game_actions.stage_guard import get_stage_with_check
    import config_manager
    from utils.model_sync import ensure_local_model
    import new_cnn.cnn_model as cnn_model

    raw = connect_u2_with_retries(device_ip)
    device = MonitoredDevice(raw, device_ip)

    local_pth = ensure_local_model("cnn_model.pth")
    cnn = cnn_model.load_cnn_model(local_pth)

    run_id = f"{task_name}_{time.strftime('%Y-%m-%d_%H-%M-%S')}_{device_ip}"
    run_dir = out_root / run_id
    rec = Recorder(run_dir)
    rec.bind_device(device)

    cfg = config_manager.get_device_config(device_ip) or {}

    ctx = TaskContext(
        device=device,
        ip=device_ip,
        cnn_model=cnn,
        recorder=rec,
        config=dict(cfg),
        timeout_at=time.time() + timeout_sec,
    )

    def stage_resolver(_ctx: TaskContext) -> str:
        return get_stage_with_check(_ctx.device, _ctx.ip, _ctx.cnn_model)

    return ctx, stage_resolver


def run_run(args: argparse.Namespace) -> int:
    if args.task not in TASK_REGISTRY:
        print(f"unknown task: {args.task!r}; known: {sorted(TASK_REGISTRY)}", file=sys.stderr)
        return 2

    spec = TASK_REGISTRY[args.task]
    ctx, stage_resolver = _build_runtime_context(
        args.device, Path(args.out), args.task, args.timeout,
    )

    try:
        result = run_task(spec, ctx, stage_resolver=stage_resolver)
    finally:
        ctx.recorder.close()

    print(f"task={args.task} ok={result.ok} reason={result.reason!r}")
    print(f"trace: {ctx.recorder.run_dir}/trace.jsonl")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return run_list(args)
    if args.cmd == "run":
        return run_run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `__main__.py`**

```python
# task_sandbox/__main__.py
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 5: Run cli tests**

Run: `pytest tests/test_task_sandbox_cli.py -v`
Expected: 1 PASS.

- [ ] **Step 6: Smoke list via CLI**

Run: `python -m task_sandbox list`
Expected output (one line):
```
lamp                     entry=lamp_page    schedule=EveryHours
```

- [ ] **Step 7: Commit**

```bash
git add task_sandbox/cli.py task_sandbox/__main__.py tests/test_task_sandbox_cli.py
git commit -m "task_sandbox: CLI with list / run subcommands"
```

---

### Task 14: End-to-end FakeDevice integration test

This test wires runner + Recorder + a fake nav handler + a fake stage
resolver to prove the full pipeline produces the expected trace.jsonl
without touching the network or a real device.

**Files:**
- Modify: `tests/test_task_sandbox_runner.py`

- [ ] **Step 1: Add integration test**

Append to `tests/test_task_sandbox_runner.py`:

```python
def test_full_pipeline_produces_expected_trace(tmp_path: Path):
    """Runner + Recorder + Navigator wired together with FakeDevice.

    Spec entry = LAMP_PAGE; resolver returns 主頁面 first call,
    then lamp_page after the nav handler runs. Runner records its own
    event. Verifier asserts on lamp_page.
    """
    import json

    from task_sandbox.navigator import navigate_to
    from task_sandbox import NavTarget
    from task_sandbox.spec import TaskContext

    rec = Recorder(tmp_path)
    device = FakeDevice()
    rec.bind_device(device)

    stages = iter(["主頁面", "主頁面", "lamp_page", "lamp_page"])

    def stage_resolver(_ctx):
        return next(stages)

    def fake_lamp_handler(_ctx):
        device.click(274, 841)

    def runner(ctx):
        ctx.recorder.event("click", x=100, y=200, msg="lamp run body")
        return TaskResult(ok=True, reason="finished")

    def verifier(ctx):
        return VerifyResult(ok=True, checks=[("on_lamp", True, "")])

    spec = TaskSpec(
        name="lamp_test",
        entry=NavTarget.LAMP_PAGE,
        schedule=EveryHours(hours=1),
        runner=runner,
        verifier=verifier,
    )

    ctx = TaskContext(
        device=device, ip="fake", cnn_model=None,
        recorder=rec, config={}, timeout_at=1e18,
    )

    # Patch NAV_HANDLERS just for this test by using handler_overrides
    # via the nav module's override hook.
    import task_sandbox.runner as runner_mod
    original_navigate_to = runner_mod.navigate_to

    def patched_navigate_to(ctx, target, *, stage_resolver):
        return original_navigate_to(
            ctx, target,
            stage_resolver=stage_resolver,
            handler_overrides={NavTarget.LAMP_PAGE: fake_lamp_handler},
        )

    runner_mod.navigate_to = patched_navigate_to
    try:
        result = run_task(spec, ctx, stage_resolver=stage_resolver)
    finally:
        runner_mod.navigate_to = original_navigate_to

    assert result.ok is True
    assert device.clicks == [(274, 841)]

    rec.close()
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds[0] == "task_start"
    assert "nav_step" in kinds
    assert "click" in kinds
    assert kinds[-1] == "task_end"
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_task_sandbox_runner.py::test_full_pipeline_produces_expected_trace -v`
Expected: PASS.

- [ ] **Step 3: Run the full task_sandbox suite**

Run: `pytest tests/test_task_sandbox_*.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_task_sandbox_runner.py
git commit -m "task_sandbox: end-to-end integration test (runner + recorder + nav)"
```

---

### Task 15: Manual smoke test on real device + final checks

**Files:**
- (no code changes; documentation only — appended below)

This task is run ONCE on a real device by the operator. The other tasks
get a script-mergable code package; this one validates production wiring.

- [ ] **Step 1: Run list on the real machine**

Run: `python -m task_sandbox list`
Expected: lamp listed.

- [ ] **Step 2: Run lamp against a real emulator**

Pick an emulator that's already in main page state. Run:

```
python -m task_sandbox run lamp --device emulator-5554
```

Expected:
- A directory `runs/lamp_<timestamp>_emulator-5554/` is created.
- `runs/lamp_*/trace.jsonl` contains `task_start`, at least one `nav_step`, and `task_end` lines.
- `runs/lamp_*/screenshots/` contains at least one PNG (stage change or assertion fail).
- Process exits 0 on success.

- [ ] **Step 3: Document outcome in spec doc**

Append a section under `## 8. 風險 / 開放問題` of
`docs/superpowers/specs/2026-05-02-task-sandbox-design.md`:

```markdown
## Phase 1 manual smoke (filled in after run)

- date: <when>
- device: <id>
- result: <pass/fail + brief notes>
- trace path: <e.g. runs/lamp_2026-05-03_09-12-22_emulator-5554/trace.jsonl>
```

- [ ] **Step 4: Commit (only if smoke passed)**

```bash
git add docs/superpowers/specs/2026-05-02-task-sandbox-design.md
git commit -m "task_sandbox: record Phase 1 smoke result"
```

---

## Self-Review Notes

**Spec coverage (mapping § → task):**
- §3.1 (3 modes: run/verify/explore) → Phase 1 ships `run` only (Tasks 11/13). `verify` plumbing exists in `TaskSpec.verifier` (Task 7) and runner uses it (Task 11), but no built-in `verify` CLI subcommand — that's Phase 2 (covered in §6).
- §3.2 (file layout) → Task 1 + each individual task creates the listed files. `explorer/`, `trace/video.py` deliberately skipped (Phases 3–4).
- §4.1 TaskSpec → Task 7.
- §4.2 NavTarget → Task 6 (only MAIN_PAGE + LAMP_PAGE; rest deferred to Phase 2).
- §4.3 Schedule → Task 5.
- §4.4 TaskContext / TaskResult / VerifyResult → Task 7.
- §4.5 Trace events + screenshot triggers → Tasks 2 + 3 (recorder.assertion / stage_check / ocr_miss / error all snap).
- §4.6 Video → deferred to Phase 4 (per §6); `video_start` / `video_end` event kinds are reserved in schema (Task 2) but not emitted.
- §4.7 Explorer → deferred to Phase 3.
- §4.8 CLI → Task 13 ships `list` + `run`; `verify` and `explore` subcommands deferred.
- §5.1 (existing task modules unchanged) → confirmed; Task 12 only imports `opengold_v2`, no edits.
- §5.2 (existing helpers reused) → Task 10 uses `img_tools.click_str_by_server`; Task 13 uses `get_stage_with_check`, `connect_u2_with_retries`, `MonitoredDevice`, `config_manager`, `cnn_model`.
- §5.3 (lamp-debug / playwright-lamp-test skill) → no changes in Phase 1; left to Phase 2 follow-up.

**Naming consistency check:** `Recorder.event(kind, ...)`, `Recorder.assertion(name, ok=...)`, `Recorder.stage_check(before=, after=)`, `Recorder.ocr_miss(target, detail=)`, `Recorder.error(exc)`, `Recorder.span(name)`, `Recorder.screenshot(reason)`, `Recorder.bind_device(d)`, `Recorder.close()`. Used consistently across Tasks 3, 9, 10, 11, 14.

**No-placeholder check:** scanned for TBD/TODO/FIXME — only one TODO is in §3.1 of the SPEC ("verify/explore are TODO future phase" inside `runner.py`'s docstring); that's a deliberate phase marker, not an unfinished plan task.

**Test coverage:**
- Tasks 3, 5, 7, 9, 10, 11, 13, 14 ship tests.
- Task 6 is enum + exception classes — no behaviour to test beyond import.
- Task 12 (LAMP TaskSpec) is a value object that imports `opengold_v2`; tested transitively by Task 14's integration test (with a fake handler) and Task 15's manual smoke (real device).

**Risks for the executing engineer:**
- `cv2.imwrite` (Task 3) must accept the path as `str(...)`; do not pass `Path` directly on Windows.
- `img_tools` import in `_lamp_page_handler` is intentionally lazy because `img_tools` initializes paddle/cv2 at import — keeping it lazy keeps unit tests fast.
- `_build_runtime_context` (Task 13) imports heavy modules (`cnn_model`, `adb_operations`); the parser-only test in Task 13 calls `build_parser` and `run_list`, not `run_run`, so heavy imports are not triggered in unit tests.
- `TaskSpec` is `frozen=True` but holds `Schedule` (frozen dataclass) and `frozenset`; nested frozen-ness is preserved.
- The existing `LampService.run(times=1000, is_compare=True)` is a long-running loop. For Phase 1 smoke (Task 15) consider passing a smaller `lamp_times` via device config to avoid a multi-hour run during the test.

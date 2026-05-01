"""Runner - wires TaskSpec + TaskContext through navigator -> runner -> verifier."""
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
        rec.event(
            "task_end", task=spec.name, ok=False, reason=result.reason,
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return result

    try:
        result = spec.runner(ctx)
    except Exception as e:
        rec.error(e)
        result = TaskResult(ok=False, reason=f"runner_exc:{type(e).__name__}:{e}")
        rec.event(
            "task_end", task=spec.name, ok=False, reason=result.reason,
            elapsed_ms=int((time.time() - started) * 1000),
        )
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

    rec.event(
        "task_end", task=spec.name, ok=result.ok, reason=result.reason,
        elapsed_ms=int((time.time() - started) * 1000),
    )
    return result

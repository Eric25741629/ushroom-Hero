from pathlib import Path

import pytest

from task_sandbox import EveryHours, NavTarget, TaskResult, TaskSpec, VerifyResult
from task_sandbox.runner import run_task
from task_sandbox.spec import TaskContext
from task_sandbox.trace.recorder import Recorder
from tests.fakes.device import FakeDevice


def _build(tmp_path):
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
        return "主頁面"

    return ctx, stage_resolver, rec


def _spec(runner, *, entry=NavTarget.MAIN_PAGE, verifier=None, **kwargs):
    return TaskSpec(
        name="t",
        entry=entry,
        schedule=EveryHours(hours=1),
        runner=runner,
        verifier=verifier,
        **kwargs,
    )


def test_run_task_success_emits_task_start_and_end(tmp_path: Path):
    def runner(ctx):
        return TaskResult(ok=True, reason="done")

    ctx, resolver, rec = _build(tmp_path)
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

    ctx, resolver, rec = _build(tmp_path)
    result = run_task(_spec(runner), ctx, stage_resolver=resolver)
    assert result.ok is False
    assert result.reason == "boom"


def test_run_task_runner_exception_caught(tmp_path: Path):
    def runner(ctx):
        raise RuntimeError("explode")

    ctx, resolver, rec = _build(tmp_path)
    result = run_task(_spec(runner), ctx, stage_resolver=resolver)
    assert result.ok is False
    assert "explode" in result.reason


def test_run_task_calls_verifier_when_present(tmp_path: Path):
    def runner(ctx):
        return TaskResult(ok=True)

    def verifier(ctx):
        return VerifyResult(ok=True, checks=[("on_main", True, "")])

    ctx, resolver, rec = _build(tmp_path)
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
    ctx, resolver, rec = _build(tmp_path)
    result = run_task(spec, ctx, stage_resolver=resolver)
    assert result.ok is True
    assert "skipped" in result.reason


def test_full_pipeline_produces_expected_trace(tmp_path: Path, monkeypatch):
    """Runner + Recorder + Navigator wired together with FakeDevice + a
    monkey-patched LAMP_PAGE handler. Proves trace.jsonl contains
    task_start, nav_step, runner click, and task_end events.
    """
    import json

    import task_sandbox.navigator as nav_mod

    rec = Recorder(tmp_path)
    device = FakeDevice()
    rec.bind_device(device)

    stages = iter(["主頁面"] * 10)

    def stage_resolver(_ctx):
        return next(stages)

    def fake_lamp_handler(ctx, _resolver):
        ctx.device.click(274, 841)
        return True

    monkeypatch.setitem(nav_mod.NAV_HANDLERS, NavTarget.LAMP_PAGE, fake_lamp_handler)

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

    result = run_task(spec, ctx, stage_resolver=stage_resolver)
    assert result.ok is True
    assert device.clicks == [(274, 841)]

    rec.close()
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds[0] == "task_start"
    assert "nav_step" in kinds
    assert "click" in kinds
    assert kinds[-1] == "task_end"

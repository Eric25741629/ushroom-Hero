from pathlib import Path

import pytest

import task_sandbox.navigator as nav_mod
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

    def stage_resolver(_ctx):
        return "主頁面"

    navigate_to(ctx, NavTarget.MAIN_PAGE, stage_resolver=stage_resolver)
    assert d.clicks == []


def test_navigate_main_page_recovers_via_app_stop(tmp_path: Path):
    d = FakeDevice()
    ctx = _ctx(tmp_path, d)

    stages = iter(["lamp_page", "主頁面"])

    def stage_resolver(_ctx):
        return next(stages)

    navigate_to(ctx, NavTarget.MAIN_PAGE, stage_resolver=stage_resolver)


def test_navigate_raises_after_repeated_failure(tmp_path: Path, monkeypatch):
    d = FakeDevice()
    ctx = _ctx(tmp_path, d)

    def stage_resolver(_ctx):
        return "unknown_screen"

    def stuck_main(ctx, resolver):
        return False

    monkeypatch.setitem(nav_mod.NAV_HANDLERS, NavTarget.MAIN_PAGE, stuck_main)

    with pytest.raises(NavigationFailed):
        navigate_to(
            ctx, NavTarget.MAIN_PAGE,
            stage_resolver=stage_resolver,
            max_attempts=3,
        )

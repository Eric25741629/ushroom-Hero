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

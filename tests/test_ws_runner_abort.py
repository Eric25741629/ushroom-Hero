"""Tests for the ws_token.runner abort + skip mechanism (interruptible WS phase).

run_device gained two optional knobs (default None -> behaviour unchanged):
  - should_abort(): polled at every task boundary; once true the run stops ASAP,
    records nothing for the remaining tasks, and returns RunReport.aborted=True.
  - skip_tasks: a set of task names already done (resume) that are bypassed.

A long task (lamp / mining) can raise WSRunAborted from inside its own loop;
the runner must treat that as an abort (NOT a task error) and leave the task
pending (absent from both tasks and errors).

Reuses the heavy `patched` fixture from test_ws_token_runner (stubs every task
orchestrator + the client) so we assert against recorded calls.
"""
import types

import pytest

# Importing this module stubs cv2 and patches nothing yet; it gives us the
# `patched` fixture (every task orchestrator stubbed) and the runner handle.
from tests.test_ws_token_runner import patched  # noqa: F401 (used as a fixture)
from ws_token import runner  # noqa: E402
from ws_token.abort import WSRunAborted  # noqa: E402
from ws_token.runner import run_device  # noqa: E402


# --- regression: defaults leave behaviour identical -------------------------

def test_defaults_report_not_aborted(patched):
    """No should_abort / skip_tasks -> aborted is False and every task runs."""
    rep = run_device("dev", spend=False)

    assert rep.aborted is False
    assert "main_tasks" in rep.tasks
    assert "steward" in rep.tasks


# --- should_abort stops the run at a task boundary --------------------------

def test_abort_after_main_tasks_stops_remaining(patched):
    calls, _ = patched

    # Fire once main_tasks has fully run; the next _step (league_solo) aborts.
    def should_abort():
        return ("main_tasks", "claim_achievement") in calls

    rep = run_device("dev", spend=False, should_abort=should_abort)

    assert rep.aborted is True
    assert "main_tasks" in rep.tasks          # ran before the abort
    assert "league_solo" not in rep.tasks     # aborted before this one
    assert "steward" not in rep.tasks         # and everything after
    assert "league_solo" not in rep.errors    # abort is not a task error


def test_abort_immediately_runs_no_tasks(patched):
    """should_abort true from the start -> first _step aborts, zero tasks run."""
    rep = run_device("dev", spend=False, should_abort=lambda: True)

    assert rep.aborted is True
    assert rep.tasks == {}
    assert rep.errors == {}
    assert rep.login_ok is True               # login still happened


def test_abort_closes_client(patched):
    _calls, spy_holder = patched

    run_device("dev", spend=False, should_abort=lambda: True)

    assert spy_holder["client"].closed is True


# --- skip_tasks bypasses already-done tasks (resume) ------------------------

def test_skip_tasks_are_not_run(patched):
    calls, _ = patched

    rep = run_device("dev", spend=False,
                     skip_tasks={"league_solo", "redpack"})

    # skipped tasks: their orchestrators were never called and no result recorded
    assert ("league_solo", "claim_available") not in {(t, a) for t, a in calls}
    assert ("redpack", "grab_claimable") not in {(t, a) for t, a in calls}
    assert "league_solo" not in rep.tasks
    assert "redpack" not in rep.tasks
    # neighbours still ran
    assert "main_tasks" in rep.tasks
    assert "idle_reward" in rep.tasks
    assert rep.aborted is False


def test_skip_tasks_and_abort_compose(patched):
    """A resume run can skip done tasks AND still abort on a fresh web launch."""
    calls, _ = patched

    def should_abort():
        return ("idle_reward", "claim_online") in calls

    rep = run_device("dev", spend=False,
                     skip_tasks={"main_tasks", "league_solo", "redpack"},
                     should_abort=should_abort)

    assert "main_tasks" not in rep.tasks      # skipped (resume)
    assert "idle_reward" in rep.tasks         # ran, then triggered abort
    assert rep.aborted is True
    assert "turntable" not in rep.tasks       # aborted before this one


# --- a long task raising WSRunAborted is an abort, not an error -------------

def test_lamp_abort_is_pending_not_error(patched, monkeypatch):
    """WSRunAborted from lamp.open_lamp -> aborted=True, lamp left pending."""
    def aborting_open(c, **k):
        raise WSRunAborted("web launch pending mid-lamp")

    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(open_lamp=aborting_open))

    rep = run_device("dev", spend=False, open_lamp=True)

    assert rep.aborted is True
    assert "lamp" not in rep.tasks            # not recorded as done ...
    assert "lamp" not in rep.errors           # ... and not an error -> pending


def test_mining_abort_is_pending_not_error(patched, monkeypatch):
    def aborting_mine(client, tracker, **k):
        raise WSRunAborted("web launch pending mid-mining")

    monkeypatch.setattr(runner.mining_supervised,
                        "mine_until_pickaxe_empty", aborting_mine)

    rep = run_device("dev", spend=False,
                     mining_config={"enabled": True, "max_steps": 50})

    assert rep.aborted is True
    assert "mining" not in rep.tasks
    assert "mining" not in rep.errors


def test_lamp_receives_should_abort_callable(patched, monkeypatch):
    """run_device must forward should_abort into lamp.open_lamp so the batch loop
    can poll it (the actual raise is unit-tested in lamp's own test)."""
    captured: dict = {}

    def spy_open(c, **k):
        captured.update(k)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "dry_run": k.get("dry_run", True)}

    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(open_lamp=spy_open))

    sentinel = lambda: False
    run_device("dev", spend=False, open_lamp=True, should_abort=sentinel)

    assert captured.get("should_abort") is sentinel


def test_mining_receives_should_abort_callable(patched, monkeypatch):
    captured: dict = {}

    def spy_mine(client, tracker, **k):
        captured.update(k)
        return {"executed": [], "stopped_reason": "pickaxe_empty"}

    monkeypatch.setattr(runner.mining_supervised,
                        "mine_until_pickaxe_empty", spy_mine)

    sentinel = lambda: False
    run_device("dev", spend=False,
               mining_config={"enabled": True, "max_steps": 10},
               should_abort=sentinel)

    assert captured.get("should_abort") is sentinel

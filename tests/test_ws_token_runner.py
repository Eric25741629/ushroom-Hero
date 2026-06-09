"""Tests for ws_token.runner — the single-device daily task orchestrator.

run_device builds ONE WSGameClient, connects once (background heartbeat), then
runs every daily task in a fixed order with per-task isolation, and finally
closes the client. These tests:

  - verify the call ORDER of the per-task orchestrators,
  - verify the spend gate (spend=False sends no cost actions; spend=True does),
  - verify per-task isolation (one task raising WSTimeoutError does not abort the
    others; its error is recorded on the RunReport),
  - verify a login failure surfaces on the report without running any task.

Tasks are isolated by monkeypatching the orchestrator functions on the
``runner`` module's task-module references, so we assert against recorded calls
rather than real WS round-trips. A separate end-to-end test drives the real
task code over the FakeTransport responder to prove the wiring is sound.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import runner  # noqa: E402
from ws_token.client import WSLoginError, WSTimeoutError  # noqa: E402
from ws_token.runner import RunReport, run_device  # noqa: E402
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    s2c,
)


# --- fakes ------------------------------------------------------------------

class _SpyClient:
    """Stand-in for WSGameClient: records connect/close, returns a login dict."""

    def __init__(self, *, login: dict | None = None, connect_error: Exception | None = None,
                 kicked: bool = False):
        self._login = login if login is not None else {"code": 0, "role_id": 1, "serv_time": 99}
        self._connect_error = connect_error
        self._kicked = kicked
        self.connected = False
        self.closed = False

    def connect(self) -> dict:
        if self._connect_error is not None:
            raise self._connect_error
        self.connected = True
        return self._login

    def is_kicked(self) -> bool:
        return self._kicked

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched(monkeypatch):
    """Patch client construction + every task orchestrator; record the call log.

    Returns ``(calls, spy_holder)`` where ``calls`` is the ordered list of
    ``(task, action)`` tuples and ``spy_holder["client"]`` is the SpyClient that
    run_device constructed (so the test can assert connect/close).
    """
    calls: list[tuple[str, str]] = []
    spy_holder: dict = {"client": None, "login": {"code": 0, "role_id": 1, "serv_time": 99}}

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(login=spy_holder["login"])
        spy_holder["client"] = spy
        spy_holder["push_handler"] = kwargs.get("push_handler")
        return spy

    def fake_load_creds(device, **kwargs):
        return CREDS

    monkeypatch.setattr(runner, "_make_client", fake_make_client)
    monkeypatch.setattr(runner, "load_creds", fake_load_creds)

    # main_tasks
    monkeypatch.setattr(runner.main_tasks, "collect_state",
                        lambda c, col, **k: (calls.append(("main_tasks", "collect_state")) or "STATE"))
    monkeypatch.setattr(runner.main_tasks, "claim_daily_tasks",
                        lambda c, st, **k: (calls.append(("main_tasks", "claim_daily_tasks")) or {"claimed": 0}))
    monkeypatch.setattr(runner.main_tasks, "claim_daily_box",
                        lambda c, st, **k: (calls.append(("main_tasks", "claim_daily_box")) or False))
    monkeypatch.setattr(runner.main_tasks, "claim_weekly_box",
                        lambda c, st, **k: (calls.append(("main_tasks", "claim_weekly_box")) or False))
    monkeypatch.setattr(runner.main_tasks, "claim_achievement",
                        lambda c, **k: (calls.append(("main_tasks", "claim_achievement")) or {"claimed": 0}))

    # league_solo
    monkeypatch.setattr(runner.league_solo, "claim_available",
                        lambda c, **k: (calls.append(("league_solo", "claim_available")) or {"claimed": 0}))

    # redpack (free; always runs)
    monkeypatch.setattr(runner.redpack, "grab_claimable",
                        lambda c, **k: (calls.append(("redpack", "grab_claimable"))
                                        or {"attempted": 0, "claimed": 0, "results": []}))

    # idle_reward (free; always runs). claim_* return a result with .success or None.
    monkeypatch.setattr(runner.idle_reward, "claim_online",
                        lambda c, **k: (calls.append(("idle_reward", "claim_online")) or _ClaimOK()))
    monkeypatch.setattr(runner.idle_reward, "claim_offline_from_push",
                        lambda c, b, **k: (calls.append(("idle_reward", "claim_offline")) or _ClaimOK()))

    # turntable (free; always runs)
    monkeypatch.setattr(runner.turntable, "spin_all_free",
                        lambda c, **k: (calls.append(("turntable", "spin_all_free"))
                                        or {"spun": 0, "results": []}))

    # farm (harvest free + always; plant/work gated on farm_config).
    # read_farm is read ONCE in _run_farm and the snapshot reused (server answers
    # home_farm_info once per session), so stub it too.
    monkeypatch.setattr(runner.farm, "read_farm",
                        lambda c, rid, **k: (calls.append(("farm", "read_farm")) or object()))
    monkeypatch.setattr(runner.farm, "harvest_ready",
                        lambda c, rid, **k: (calls.append(("farm", "harvest_ready"))
                                             or {"attempted": 0, "harvested": 0,
                                                 "rewards": {}, "results": []}))
    monkeypatch.setattr(runner.farm, "plant_empty",
                        lambda c, rid, sid, **k: (calls.append(("farm", "plant_empty"))
                                                  or {"attempted": 0, "planted": 0, "results": []}))
    monkeypatch.setattr(runner.farm, "start_work",
                        lambda c, tid, **k: (calls.append(("farm", "start_work"))
                                             or {"running": True, "worker_status": 1, "raw": {}}))

    # dungeon (掃蕩 only; gated on dungeon_sweeps)
    monkeypatch.setattr(runner.dungeon, "run_sweep",
                        lambda c, **k: (calls.append(("dungeon", "run_sweep")) or _SweepOK()))

    # carpark (只停不收; gated on carpark_target)
    monkeypatch.setattr(runner.carpark, "auto_park_cross",
                        lambda c, **k: (calls.append(("carpark", "auto_park_cross"))
                                        or {"parked": True, "reason": "ok", "pos": 1,
                                            "mount_id": 11}))

    # lamp (opt-in via open_lamp; spends 神燈 items)
    monkeypatch.setattr(runner.lamp, "open_lamp",
                        lambda c, **k: (calls.append(("lamp", "open_lamp"))
                                        or {"opened": 0, "equipped": [], "sold": [],
                                            "left": [], "dry_run": k.get("dry_run", True)}))

    # guild
    monkeypatch.setattr(runner.guild, "help_all",
                        lambda c, **k: (calls.append(("guild", "help_all")) or {"helped": 0}))
    monkeypatch.setattr(runner.guild, "donate_until_capped",
                        lambda c, **k: (calls.append(("guild", "donate_until_capped")) or {"donated": 0}))
    monkeypatch.setattr(runner.guild, "list_treasure",
                        lambda c, **k: (calls.append(("guild", "list_treasure")) or _NoRound()))
    monkeypatch.setattr(runner.guild, "open_all_treasure",
                        lambda c, **k: (calls.append(("guild", "open_all_treasure")) or {"opened": 0}))

    # steward
    monkeypatch.setattr(runner.steward, "read_info",
                        lambda c, **k: (calls.append(("steward", "read_info")) or _Info()))
    monkeypatch.setattr(runner.steward, "run_shopping",
                        lambda c, **k: (calls.append(("steward", "run_shopping")) or "SHOP"))
    monkeypatch.setattr(runner.steward, "run_dungeon_sweep",
                        lambda c, sl, **k: (calls.append(("steward", "run_dungeon_sweep")) or "SWEEP"))
    monkeypatch.setattr(runner.steward, "ensure_active",
                        lambda c, sid, **k: (calls.append(("steward", "ensure_active")) or True))

    return calls, spy_holder


class _NoRound:
    """guild.list_treasure result with no active round (event dormant)."""
    round = 0
    my_open = 0
    box_list: list = []


class _Info:
    """steward.read_info result; expiry empty => nothing expired/active."""
    expiry: dict = {}


class _ClaimOK:
    """idle_reward.claim_* result: a successful claim."""
    success = True


class _SweepOK:
    """dungeon.run_sweep result with the fields _run_dungeon reads."""
    success = True
    rewards: dict = {}
    error_code = 0


# --- RunReport shape --------------------------------------------------------

def test_run_report_is_frozen_dataclass():
    rep = RunReport(device="dev", login_ok=True, spend=False, tasks={}, errors={})
    assert rep.device == "dev"
    with pytest.raises(Exception):
        rep.device = "other"  # frozen


# --- call order -------------------------------------------------------------

def test_run_device_runs_tasks_in_fixed_order(patched):
    calls, _ = patched

    rep = run_device("dev", spend=False)

    # main_tasks first (and its sub-claims), then league_solo, redpack, the free
    # idle_reward / turntable / farm-harvest group, then guild, steward.
    # dungeon (掃蕩) and carpark are gated on config and SKIP by default; lamp is
    # opt-in and OFF here — none of the three appear.
    task_order = [t for t, _a in calls]
    assert task_order.index("main_tasks") < task_order.index("league_solo")
    assert task_order.index("league_solo") < task_order.index("redpack")
    assert task_order.index("redpack") < task_order.index("idle_reward")
    assert task_order.index("idle_reward") < task_order.index("turntable")
    assert task_order.index("turntable") < task_order.index("farm")
    assert task_order.index("farm") < task_order.index("guild")
    assert task_order.index("guild") < task_order.index("steward")
    assert "dungeon" not in task_order   # no dungeon_sweeps -> skipped
    assert "carpark" not in task_order   # no carpark_target -> skipped
    assert "lamp" not in task_order
    assert rep.login_ok is True


def test_run_device_main_tasks_collects_then_claims(patched):
    calls, _ = patched

    run_device("dev", spend=False)

    mt = [a for t, a in calls if t == "main_tasks"]
    assert mt[0] == "collect_state"
    assert set(mt[1:]) == {
        "claim_daily_tasks", "claim_daily_box", "claim_weekly_box", "claim_achievement"
    }


# --- spend gate -------------------------------------------------------------

def test_spend_false_sends_no_cost_actions(patched):
    calls, _ = patched

    run_device("dev", spend=False)

    actions = {(t, a) for t, a in calls}
    # free reads/claims happened
    assert ("main_tasks", "collect_state") in actions
    assert ("league_solo", "claim_available") in actions
    assert ("guild", "help_all") in actions
    assert ("steward", "read_info") in actions
    # NO cost actions
    assert ("guild", "donate_until_capped") not in actions
    assert ("steward", "run_shopping") not in actions
    assert ("steward", "run_dungeon_sweep") not in actions


def test_spend_true_adds_cost_actions(patched):
    calls, _ = patched

    # provide a sweep_list so the 副本管家 sweep is exercised (it is gated on a
    # caller-supplied chapter list; with none configured the sweep is skipped).
    run_device("dev", spend=True, sweep_list=[(1, 5, 10)])

    actions = {(t, a) for t, a in calls}
    assert ("guild", "donate_until_capped") in actions
    assert ("steward", "run_shopping") in actions
    assert ("steward", "run_dungeon_sweep") in actions


def test_spend_true_skips_sweep_without_chapter_list(patched):
    calls, _ = patched

    run_device("dev", spend=True)  # no sweep_list

    actions = {(t, a) for t, a in calls}
    # shopping still runs on spend, but the sweep is skipped (nothing configured)
    assert ("steward", "run_shopping") in actions
    assert ("steward", "run_dungeon_sweep") not in actions


# --- redpack (free; always runs) --------------------------------------------

def test_redpack_runs_for_free_and_records_result(patched):
    calls, _ = patched

    rep = run_device("dev", spend=False)

    # redpack.grab_claimable was called (free, no spend gate) ...
    assert ("redpack", "grab_claimable") in {(t, a) for t, a in calls}
    # ... and its summary landed on the report.
    assert "redpack" in rep.tasks
    assert rep.tasks["redpack"] == {"attempted": 0, "claimed": 0, "results": []}


def test_redpack_failure_does_not_abort_other_tasks(patched, monkeypatch):
    calls, _ = patched

    def boom(c, **k):
        calls.append(("redpack", "grab_claimable"))
        raise WSTimeoutError("redpack list timed out")

    monkeypatch.setattr(runner.redpack, "grab_claimable", boom)

    rep = run_device("dev", spend=False)

    assert "redpack" in rep.errors          # error recorded ...
    assert "redpack" not in rep.tasks       # ... and no bogus result
    # guild + steward still ran afterwards.
    assert any(t == "guild" for t, _a in calls)
    assert any(t == "steward" for t, _a in calls)


# --- lamp (opt-in via open_lamp; spends 神燈 items) --------------------------

def test_lamp_not_run_when_open_lamp_false(patched):
    """Default open_lamp=False must NOT open the lamp (no 神燈 spend)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("lamp", "open_lamp") not in {(t, a) for t, a in calls}
    assert "lamp" not in rep.tasks


def test_lamp_not_run_even_with_spend_true(patched):
    """open_lamp is independent of spend: spend=True alone must not open lamps."""
    calls, _ = patched

    run_device("dev", spend=True, sweep_list=[(1, 5, 10)])

    assert ("lamp", "open_lamp") not in {(t, a) for t, a in calls}


def test_lamp_runs_when_open_lamp_true(patched):
    calls, _ = patched

    rep = run_device("dev", spend=False, open_lamp=True)

    assert ("lamp", "open_lamp") in {(t, a) for t, a in calls}
    assert "lamp" in rep.tasks
    assert rep.tasks["lamp"]["opened"] == 0


def test_lamp_opened_for_real_with_bounded_batch(patched, monkeypatch):
    """The daily runner opens lamps for REAL (dry_run=False) with a bounded
    batch_num — winners get equipped/sold, never an unbounded drain."""
    captured: dict = {}

    def spy_open(c, **k):
        captured.update(k)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "dry_run": k.get("dry_run", True)}

    monkeypatch.setattr(runner.lamp, "open_lamp", spy_open)

    run_device("dev", spend=False, open_lamp=True)

    assert captured.get("dry_run") is False          # REAL open, not simulated
    assert captured.get("batch_num") == runner._LAMP_BATCH_NUM  # bounded batch


def test_lamp_failure_does_not_abort_report(patched, monkeypatch):
    calls, _ = patched

    def boom(c, **k):
        calls.append(("lamp", "open_lamp"))
        raise WSTimeoutError("lamp open timed out")

    monkeypatch.setattr(runner.lamp, "open_lamp", boom)

    rep = run_device("dev", spend=False, open_lamp=True)

    assert "lamp" in rep.errors        # error recorded ...
    assert "lamp" not in rep.tasks     # ... and no bogus result
    assert rep.login_ok is True        # the rest of the run completed fine


# --- new free tasks: idle_reward / turntable / farm-harvest -----------------

def test_new_free_tasks_run_on_spend_false(patched):
    """idle_reward (claim online), turntable (spin free) and farm harvest are free
    and run unconditionally — no spend, no config needed."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    actions = {(t, a) for t, a in calls}
    assert ("idle_reward", "claim_online") in actions
    assert ("turntable", "spin_all_free") in actions
    assert ("farm", "harvest_ready") in actions
    for name in ("idle_reward", "turntable", "farm"):
        assert name in rep.tasks


def test_farm_plant_and_work_skipped_without_config(patched):
    """No farm_config -> only harvest runs; planting / 打工 are not attempted."""
    calls, _ = patched

    run_device("dev", spend=False)

    actions = {(t, a) for t, a in calls}
    assert ("farm", "harvest_ready") in actions
    assert ("farm", "plant_empty") not in actions
    assert ("farm", "start_work") not in actions


def test_farm_plant_and_work_run_with_config(patched):
    """farm_config {seed_id, team_cfg_id} -> plant empties + start 打工."""
    calls, _ = patched

    run_device("dev", spend=False,
               farm_config={"seed_id": 102, "team_cfg_id": 7})

    actions = {(t, a) for t, a in calls}
    assert ("farm", "plant_empty") in actions
    assert ("farm", "start_work") in actions


def test_dungeon_sweep_skipped_without_config(patched):
    """No dungeon_sweeps -> dungeon task is skipped (run_sweep never called)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("dungeon", "run_sweep") not in {(t, a) for t, a in calls}
    assert rep.tasks["dungeon"]["skipped"]


def test_dungeon_sweep_runs_with_config(patched):
    """dungeon_sweeps -> 掃蕩 each chapter (battle is never auto-run)."""
    calls, _ = patched

    run_device("dev", spend=False, dungeon_sweeps=[(2, 4001, 1), (23, 1081, 1)])

    sweep_calls = [a for t, a in calls if t == "dungeon"]
    assert sweep_calls == ["run_sweep", "run_sweep"]   # one per chapter, no battle


def test_carpark_skipped_without_target(patched):
    """No carpark_target -> carpark is skipped (auto_park_cross never called)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("carpark", "auto_park_cross") not in {(t, a) for t, a in calls}
    assert rep.tasks["carpark"]["skipped"]


def test_carpark_runs_with_target(patched):
    """carpark_target set -> auto_park_cross into that cross lot (只停不收)."""
    calls, _ = patched

    rep = run_device("dev", spend=False, carpark_target=5001)

    assert ("carpark", "auto_park_cross") in {(t, a) for t, a in calls}
    assert rep.tasks["carpark"]["parked"] is True


def test_idle_offline_claimed_from_login_push(monkeypatch):
    """End-to-end: an OFFLINE reward_info{type:2} pushed at login is captured by the
    runner's composite handler and claimed via claim_offline_from_push.

    Drives the REAL idle_reward code over FakeTransport: login emits a
    reward_info_s2c{type:2} with a non-zero reward (claimable), and the claim cmd
    is answered with a success. Other tasks get empty replies (no-ops)."""
    from ws_token import idle_reward
    from ws_token.client import WSGameClient
    from tests.fakes.ws_fakes import login_ok

    # OFFLINE reward_info: type#1=2, time#2=600, res_list#3 = one p_reward {1:1, 2:9}
    offline_push = (codec.pb_uint(1, idle_reward.TYPE_OFFLINE)
                    + codec.pb_uint(2, 600)
                    + codec.pb_msg(3, codec.pb_uint(1, 1) + codec.pb_uint(2, 9)))

    def responder(cmd, body):
        if cmd == 257:  # role_login: ack + the offline reward push
            return [login_ok(), s2c(idle_reward.CMD_REWARD_INFO, offline_push)]
        if cmd == idle_reward.CMD_REWARD_INFO:   # ONLINE read -> nothing claimable
            return [s2c(idle_reward.CMD_REWARD_INFO, codec.pb_uint(1, idle_reward.TYPE_ONLINE))]
        # Echo an empty reply for every other read/claim so no call hits the 15s
        # timeout. The CLAIM_REWARD echo (code 0) makes claim_offline_from_push
        # succeed; every other task gets an empty (no-op) reply.
        return [s2c(cmd, b"")]

    fake = FakeTransport(responder)

    def fake_make_client(creds, **kwargs):
        return WSGameClient(creds, transport_factory=factory_for(fake),
                            heartbeat_enabled=False, **kwargs)

    monkeypatch.setattr(runner, "_make_client", fake_make_client)
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)
    monkeypatch.setattr(runner, "_PUSH_SETTLE_S", 0.2)

    rep = run_device("dev", spend=False)

    assert "idle_reward" in rep.tasks
    assert rep.tasks["idle_reward"]["offline"] is True   # claimed from the login push


# --- per-task isolation -----------------------------------------------------

def test_task_failure_does_not_abort_other_tasks(patched, monkeypatch):
    calls, _ = patched

    def boom(c, **k):
        calls.append(("league_solo", "claim_available"))
        raise WSTimeoutError("league_solo timed out")

    monkeypatch.setattr(runner.league_solo, "claim_available", boom)

    rep = run_device("dev", spend=False)

    # league_solo failed, but guild + steward still ran afterwards.
    assert any(t == "guild" for t, _a in calls)
    assert any(t == "steward" for t, _a in calls)
    # error recorded for league_solo, not for the others.
    assert "league_solo" in rep.errors
    assert "guild" not in rep.errors
    assert "steward" not in rep.errors


def test_each_task_isolated_when_first_raises(patched, monkeypatch):
    calls, _ = patched

    def boom(c, col, **k):
        raise WSTimeoutError("main_tasks push read timed out")

    monkeypatch.setattr(runner.main_tasks, "collect_state", boom)

    rep = run_device("dev", spend=False)

    assert "main_tasks" in rep.errors
    # subsequent tasks still ran
    assert any(t == "league_solo" for t, _a in calls)
    assert any(t == "guild" for t, _a in calls)
    assert any(t == "steward" for t, _a in calls)


def test_client_is_closed_even_when_a_task_raises(patched, monkeypatch):
    _calls, spy_holder = patched

    monkeypatch.setattr(runner.guild, "help_all",
                        lambda c, **k: (_ for _ in ()).throw(WSTimeoutError("x")))

    run_device("dev", spend=False)

    assert spy_holder["client"].closed is True


# --- guild treasure is event-gated -----------------------------------------

def test_guild_treasure_skipped_when_no_round(patched):
    calls, _ = patched

    run_device("dev", spend=True)

    # list_treasure returns round=0 -> open_all_treasure must NOT be called.
    assert ("guild", "list_treasure") in {(t, a) for t, a in calls}
    assert ("guild", "open_all_treasure") not in {(t, a) for t, a in calls}


def test_guild_treasure_opened_when_round_active(patched, monkeypatch):
    calls, _ = patched

    class _ActiveRound:
        round = 7
        my_open = 2
        box_list = [object()]

    monkeypatch.setattr(runner.guild, "list_treasure",
                        lambda c, **k: (calls.append(("guild", "list_treasure")) or _ActiveRound()))

    run_device("dev", spend=True)

    assert ("guild", "open_all_treasure") in {(t, a) for t, a in calls}


def test_guild_treasure_timeout_is_skipped_not_fatal(patched, monkeypatch):
    """A dormant 尋寶 event never answers guild_treasure_info -> WSTimeoutError.

    Under spend the runner must skip treasure gracefully: help + donate still
    count, the guild task is NOT recorded as an error, and open is not attempted.
    (Regression: a raw list_treasure timeout used to fail the whole guild task.)
    """
    calls, _ = patched

    def timeout_treasure(c, **k):
        calls.append(("guild", "list_treasure"))
        raise WSTimeoutError("no response for cmd=7459")

    monkeypatch.setattr(runner.guild, "list_treasure", timeout_treasure)

    rep = run_device("dev", spend=True)

    assert "guild" not in rep.errors          # treasure timeout did NOT fail guild
    assert "guild" in rep.tasks
    actions = {(t, a) for t, a in calls}
    assert ("guild", "help_all") in actions
    assert ("guild", "donate_until_capped") in actions
    assert ("guild", "open_all_treasure") not in actions
    assert rep.tasks["guild"]["treasure"] == "unavailable (dormant event)"


def test_guild_treasure_not_read_when_free(patched):
    """spend=False must not even probe treasure (it is spend-gated), so a dormant
    event never costs the free daily run a timeout."""
    calls, _ = patched

    run_device("dev", spend=False)

    assert ("guild", "list_treasure") not in {(t, a) for t, a in calls}


# --- login failure ----------------------------------------------------------

def test_login_failure_records_error_and_runs_no_tasks(patched, monkeypatch):
    calls, spy_holder = patched

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(connect_error=WSLoginError("role_login failed: code=7"))
        spy_holder["client"] = spy
        return spy

    monkeypatch.setattr(runner, "_make_client", fake_make_client)

    rep = run_device("dev", spend=False)

    assert rep.login_ok is False
    assert "login" in rep.errors
    assert calls == []  # no task ran
    # client still closed
    assert spy_holder["client"].closed is True


# --- kick detection surfaces on the report ----------------------------------

def test_report_kicked_false_on_normal_run(patched):
    """A run on a healthy (not-kicked) client reports kicked=False."""
    _calls, spy_holder = patched

    rep = run_device("dev", spend=False)

    assert rep.kicked is False
    # default _SpyClient is not kicked, and a clean run keeps the flag clear
    assert spy_holder["client"].is_kicked() is False


def test_report_kicked_true_when_client_kicked(patched, monkeypatch):
    """When the client was kicked mid-run, RunReport.kicked is True.

    The kicked client's in-flight tasks may also fail (connection gone); the
    point is that ``kicked`` is set so the loop can tell this apart from an
    ordinary task failure. login_ok stays True (login itself succeeded).
    """
    _calls, spy_holder = patched

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(kicked=True)
        spy_holder["client"] = spy
        return spy

    monkeypatch.setattr(runner, "_make_client", fake_make_client)

    rep = run_device("dev", spend=False)

    assert rep.kicked is True
    assert rep.login_ok is True
    assert spy_holder["client"].closed is True  # still closed


def test_report_kicked_false_when_client_lacks_is_kicked(patched, monkeypatch):
    """Defensive: a client without is_kicked() must not crash the run."""

    class _NoKickClient:
        def connect(self):
            return {"code": 0, "role_id": 1, "serv_time": 99}

        def close(self):
            pass

    monkeypatch.setattr(runner, "_make_client", lambda creds, **k: _NoKickClient())

    rep = run_device("dev", spend=False)

    assert rep.kicked is False


def test_report_kicked_via_fake_transport_259_then_close(monkeypatch):
    """End-to-end: a cmd-259 push then socket close → RunReport.kicked=True.

    Drives the REAL WSGameClient over a FakeTransport whose login response
    includes the 異地登入 kick frame (cmd 259, body {1:20}) and then closes the
    transport — exactly the LIVE-observed kick sequence (kick push, then the
    server hangs up). Emitting it at login keeps the test deterministic
    regardless of which task issues the first round-trip.
    """
    from ws_token.client import CMD_KICKED, WSGameClient
    from tests.fakes.ws_fakes import login_ok

    def responder(cmd, body):
        if cmd == 257:  # role_login: ack first, then the 異地登入 kick push frame
            return [login_ok(), s2c(CMD_KICKED, codec.pb_uint(1, 20))]
        return []

    fake = FakeTransport(responder)

    def fake_make_client(creds, **kwargs):
        return WSGameClient(creds, transport_factory=factory_for(fake),
                            heartbeat_enabled=False, **kwargs)

    monkeypatch.setattr(runner, "_make_client", fake_make_client)
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)
    monkeypatch.setattr(runner, "_PUSH_SETTLE_S", 0.2)

    rep = run_device("dev", spend=False)

    assert rep.kicked is True


# --- end-to-end over the real task code + FakeTransport responder ----------

def test_run_device_end_to_end_over_fake_transport(monkeypatch):
    """Drive the REAL task orchestrators against a scripted responder to prove
    the wiring (cmd ids, body building, push collection) is sound end-to-end."""
    from ws_token import (
        farm, idle_reward, league_solo, main_tasks, redpack, steward, turntable,
    )

    # main_tasks reads are PUSH-based; emit the login-time frames on login.
    def login_with_pushes(cmd, body):
        if cmd == 257:  # role_login
            from tests.fakes.ws_fakes import login_ok
            task_all = codec.pb_msg(1, codec.pb_uint(1, 10) + codec.pb_uint(2, 2)
                                    + codec.pb_uint(3, 0) + codec.pb_uint(4, main_tasks.TYPE_DAILY))
            return [login_ok(),
                    s2c(main_tasks.CMD_ALL, task_all),
                    s2c(main_tasks.CMD_DAILY_POINT, codec.pb_uint(1, 100)),
                    s2c(main_tasks.CMD_WEEKLY_BOX, codec.pb_uint(1, 0))]
        return None  # fall through to extra

    extra = {
        # main_tasks: claim the one claimable daily, achievement caught up
        main_tasks.CMD_COMMIT: lambda b: [s2c(main_tasks.CMD_COMMIT, b"")],
        main_tasks.CMD_ACHIEVEMENT: lambda b: [
            s2c(main_tasks.CMD_ACHIEVEMENT,
                codec.pb_uint(1, 5) + codec.pb_uint(2, 5) + codec.pb_uint(3, 0))],
        # league_solo: no claimable boxes
        league_solo.CMD_SOLO_INFO: lambda b: [s2c(league_solo.CMD_SOLO_INFO, b"")],
        # redpack: empty brief list (no claimable bags) -> attempted=0
        redpack.CMD_BRIEF_LIST: lambda b: [s2c(redpack.CMD_BRIEF_LIST, b"")],
        # idle_reward: ONLINE read -> type=1, nothing claimable (no claim sent)
        idle_reward.CMD_REWARD_INFO: lambda b: [
            s2c(idle_reward.CMD_REWARD_INFO, codec.pb_uint(1, idle_reward.TYPE_ONLINE))],
        # turntable: info -> num=0 (no free spins)
        turntable.CMD_INFO: lambda b: [
            s2c(turntable.CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 0))],
        # farm: home_farm_info -> empty (no lands -> 0 harvested)
        farm.CMD_INFO: lambda b: [s2c(farm.CMD_INFO, b"")],
        # guild: empty help list, no treasure round
        runner.guild.CMD_HELP_INFO: lambda b: [
            s2c(runner.guild.CMD_HELP_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 0))],
        runner.guild.CMD_TREASURE_INFO: lambda b: [
            s2c(runner.guild.CMD_TREASURE_INFO,
                codec.pb_uint(1, 0) + codec.pb_uint(2, 0) + codec.pb_uint(4, 0))],
        # steward: read info -> empty expiry (nothing active)
        steward.CMD_INFO: lambda b: [s2c(steward.CMD_INFO, b"")],
    }

    def responder(cmd, body):
        frames = login_with_pushes(cmd, body)
        if frames is not None:
            return frames
        if cmd in extra:
            return extra[cmd](body)
        return []

    fake = FakeTransport(responder)

    def fake_make_client(creds, **kwargs):
        from ws_token.client import WSGameClient
        return WSGameClient(creds, transport_factory=factory_for(fake),
                            heartbeat_enabled=False, **kwargs)

    monkeypatch.setattr(runner, "_make_client", fake_make_client)
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)
    # zero settle so the test does not sleep waiting for pushes
    monkeypatch.setattr(runner, "_PUSH_SETTLE_S", 0.05)

    rep = run_device("dev", spend=False)

    assert rep.login_ok is True
    assert rep.errors == {} or all(v is None for v in rep.errors.values())
    assert "main_tasks" in rep.tasks
    assert "league_solo" in rep.tasks
    assert "redpack" in rep.tasks
    assert rep.tasks["redpack"]["attempted"] == 0
    assert "guild" in rep.tasks
    assert "steward" in rep.tasks
    # new free tasks ran end-to-end over the real code
    assert "idle_reward" in rep.tasks
    assert "turntable" in rep.tasks
    assert rep.tasks["turntable"]["spun"] == 0
    assert "farm" in rep.tasks
    assert rep.tasks["farm"]["harvest"]["harvested"] == 0
    # dungeon / carpark are gated (no config) -> skipped, not errored
    assert rep.tasks["dungeon"]["skipped"]
    assert rep.tasks["carpark"]["skipped"]
    # lamp is opt-in (open_lamp defaults False) so it must NOT have run.
    assert "lamp" not in rep.tasks

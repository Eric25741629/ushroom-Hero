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
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("cv2", types.SimpleNamespace())

from ws_token import codec  # noqa: E402
from ws_token import runner  # noqa: E402
from ws_token.client import (  # noqa: E402
    KICK_REASON_EXPLICIT,
    KICK_REASON_TRANSPORT_DROP,
    WSLoginError,
    WSTimeoutError,
)
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
                 kicked: bool = False, kick_reason: str | None = None):
        self._login = login if login is not None else {"code": 0, "role_id": 1, "serv_time": 99}
        self._connect_error = connect_error
        self._kicked = kicked
        self._kick_reason = kick_reason
        self.connected = False
        self.closed = False

    def connect(self) -> dict:
        if self._connect_error is not None:
            raise self._connect_error
        self.connected = True
        return self._login

    def is_kicked(self) -> bool:
        return self._kicked

    def get_kick_reason(self) -> str | None:
        return self._kick_reason

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
    monkeypatch.setattr(runner.main_tasks, "claim_marry_tasks",
                        lambda c, st, **k: (calls.append(("main_tasks", "claim_marry_tasks")) or {"claimed": 0}))
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

    # mail (每日領郵件附件; gated on mail_claim, OFF by default). Patch the runner's
    # _run_mail step directly so we don't need ws_state / mail_scheduler internals.
    monkeypatch.setattr(runner, "_run_mail",
                        lambda c, **k: (calls.append(("mail", "claim_all"))
                                        or {"claimed_run": True, "success": True,
                                            "claimed_count": 0}))

    # idle_reward (free; always runs). claim_* return a result with .success or None.
    monkeypatch.setattr(runner.idle_reward, "claim_online",
                        lambda c, **k: (calls.append(("idle_reward", "claim_online")) or _ClaimOK()))
    monkeypatch.setattr(runner.idle_reward, "claim_offline_from_push",
                        lambda c, b, **k: (calls.append(("idle_reward", "claim_offline")) or _ClaimOK()))
    monkeypatch.setattr(runner.idle_reward, "claim_quick_2h",
                        lambda c, **k: (calls.append(("idle_reward", "claim_quick_2h")) or _ClaimOK()))

    # turntable (free; always runs). run_daily = claim_ad(13) ad top-up + spin;
    # stub the ad top-up so the test doesn't hit a real ad_info round-trip.
    monkeypatch.setattr(runner.ad_reward, "claim_ad",
                        lambda c, cid, **k: {"name": "轉盤廣告次數", "claimed": 0,
                                             "skipped": "stub"})
    monkeypatch.setattr(runner.turntable, "spin_all_free",
                        lambda c, **k: (calls.append(("turntable", "spin_all_free"))
                                        or {"spun": 0, "results": []}))

    # farm (harvest free; plant/work/buy gated on farm_config).
    # _run_farm first reads 打工 status (worker module 73, reliable) to decide
    # whether manual home-module harvest is needed; default to NOT running so the
    # manual harvest/plant path runs (matches the pre-refactor behaviour).
    monkeypatch.setattr(runner.farm, "read_work_status",
                        lambda c, rid, **k: (calls.append(("farm", "read_work_status"))
                                             or {"running": False, "worker_status": 0,
                                                 "team_cfg_id": 7001, "found": True}))
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
    monkeypatch.setattr(runner.farm, "buy_farm_shop",
                        lambda c, bl, **k: (calls.append(("farm", "buy_farm_shop"))
                                            or [{"shop_id": 407, "bought": 0, "ok": True}]))
    monkeypatch.setattr(runner.farm, "run_harvest_card_cycle",
                        lambda c, rid, **k: (
                            calls.append(("harvest_card", "run_harvest_card_cycle"))
                            or {"ok": True, "cards_bought": k.get("num_cards", 0)}
                        ))

    # dungeon (掃蕩 only; gated on dungeon_sweeps)
    monkeypatch.setattr(runner.dungeon, "run_sweep",
                        lambda c, **k: (calls.append(("dungeon", "run_sweep")) or _SweepOK()))

    # rogue (萬神試煉 本周積分獎勵一鍵領取; free; always runs)
    monkeypatch.setattr(runner.rogue, "claim_week_reward",
                        lambda c, **k: (calls.append(("rogue", "claim_week_reward")) or _RogueOK()))
    monkeypatch.setattr(
        runner,
        "_run_ladder_reward",
        lambda c, **k: (
            calls.append(("ladder_reward", "apply"))
            or {"ok": True, "picks": 25}
        ),
    )
    monkeypatch.setattr(
        runner,
        "_run_cloud_ladder",
        lambda c, **k: (
            calls.append(("cloud_ladder", "fight"))
            or {"completed": True, "fights": 2}
        ),
    )

    # carpark (只停不收; gated on carpark_target)
    monkeypatch.setattr(runner.carpark, "auto_park_cross",
                        lambda c, **k: (calls.append(("carpark", "auto_park_cross"))
                                        or {"parked": True, "reason": "ok", "pos": 1,
                                            "mount_id": 11}))

    # lamp (opt-in via open_lamp; spends 神燈 items)
    fake_lamp = types.SimpleNamespace(
        open_lamp=lambda c, **k: (calls.append(("lamp", "open_lamp"))
                                  or {"opened": 0, "equipped": [], "sold": [],
                                      "left": [], "dry_run": k.get("dry_run", True)})
    )
    monkeypatch.setattr(runner, "_load_lamp", lambda: fake_lamp)

    # spirit (free draws; always runs)
    monkeypatch.setattr(runner.spirit, "draw_all_free",
                        lambda c, **k: (calls.append(("spirit", "draw_all_free"))
                                        or {"pools_drawn": 0, "rewards": {}, "results": []}))

    # workshop (12h 兩配方輪換; cadence state is handled by _run_workshop)
    monkeypatch.setattr(
        runner.workshop,
        "rotate_team_recipes",
        lambda c, **k: (calls.append(("workshop", "rotate_team_recipes"))
                        or {"parity": k.get("parity", 0), "switched": []}),
    )
    _mem_state: dict = {}
    monkeypatch.setattr(runner.ws_state, "load_state",
                        lambda device, **k: dict(_mem_state))
    monkeypatch.setattr(runner.ws_state, "save_state",
                        lambda device, data, **k: _mem_state.update(data))

    # couple (no partner by default -> gifts/ring skipped)
    monkeypatch.setattr(runner.couple, "read_favor_info",
                        lambda c, **k: (calls.append(("couple", "read_favor_info")) or []))
    monkeypatch.setattr(runner.couple, "read_partner",
                        lambda c, **k: (calls.append(("couple", "read_partner")) or 0))
    monkeypatch.setattr(runner.couple, "give_all_in_hand",
                        lambda c, **k: (calls.append(("couple", "give_all_in_hand"))
                                        or {"batches_ok": 0, "stopped_reason": ""}))
    monkeypatch.setattr(runner.couple, "forge_ring_until_empty",
                        lambda c, **k: (calls.append(("couple", "forge_ring_until_empty"))
                                        or {"forges": 0}))

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


class _RogueOK:
    """rogue.claim_week_reward result with the fields _run_rogue reads."""
    success = True
    claimed: tuple = ()
    rewards: dict = {}
    error_code = None


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
    assert "mail" not in task_order      # mail_claim OFF by default -> skipped
    # rogue (萬神試煉 本周積分獎勵) is Friday-gated, so it may or may not invoke
    # claim_week_reward depending on the real weekday — but the STEP always runs
    # and records a summary on the report.
    assert "rogue" in rep.tasks
    assert rep.login_ok is True


def test_run_device_main_tasks_collects_then_claims(patched):
    calls, _ = patched

    run_device("dev", spend=False)

    # main_tasks now runs TWICE per wake (early + 尾端二次領取 main_tasks_late);
    # both passes append under the "main_tasks" label. Assert the ordering on the
    # first pass (7 actions) and that the second pass also ran (4 collect_states).
    mt = [a for t, a in calls if t == "main_tasks"]
    assert mt[0] == "collect_state"
    first_pass = mt[:7]
    # 領完每日任務後重新快照，活躍度寶箱才看得到剛變可領的盒子。
    assert first_pass.index("claim_daily_box") > _last_index(first_pass, "collect_state")
    assert first_pass.count("collect_state") == 2
    assert mt.count("collect_state") == 4  # early pass + late pass
    assert set(mt) == {
        "collect_state", "claim_daily_tasks", "claim_marry_tasks",
        "claim_daily_box", "claim_weekly_box", "claim_achievement"
    }


def test_main_tasks_runs_every_wake(monkeypatch):
    """No once-per-day gate: two same-day wakes each snapshot + claim.

    Fixes the漏領: the old date gate blocked all later wakes once the first
    post-08:00 wake claimed, so daily tasks/活躍度寶箱 that only became claimable
    later in the day (arena 20:00, mining, ...) never got claimed.
    """
    from datetime import datetime

    from ws_token import runner

    collects = []
    monkeypatch.setattr(runner.main_tasks, "collect_state",
                        lambda c, col, **k: collects.append(1) or "STATE")
    monkeypatch.setattr(runner.main_tasks, "claim_daily_tasks", lambda c, s, **k: {})
    monkeypatch.setattr(runner.main_tasks, "claim_marry_tasks", lambda c, s, **k: {})
    monkeypatch.setattr(runner.main_tasks, "claim_daily_box", lambda c, s, **k: False)
    monkeypatch.setattr(runner.main_tasks, "claim_weekly_box", lambda c, s, **k: False)
    monkeypatch.setattr(runner.main_tasks, "claim_achievement", lambda c, **k: {})

    day = datetime(2026, 7, 5, 9, 0, 0)
    runner._run_main_tasks(object(), object(), now=day)
    runner._run_main_tasks(object(), object(), now=day.replace(hour=15))

    # each wake runs a full pass (2 collect_state); no date gate blocks the 2nd.
    assert len(collects) == 4


def test_main_tasks_before_8_skips(monkeypatch):
    """The >= 08:00 gate is retained: an early wake claims nothing."""
    from datetime import datetime

    from ws_token import runner

    collects = []
    monkeypatch.setattr(runner.main_tasks, "collect_state",
                        lambda c, col, **k: collects.append(1) or "STATE")

    out = runner._run_main_tasks(object(), object(),
                                 now=datetime(2026, 7, 5, 7, 0, 0))

    assert out == {"skipped": "before 08:00"}
    assert collects == []


def test_main_tasks_late_runs_after_mining_lamp(patched, monkeypatch):
    """尾端二次領取 main_tasks_late runs as a step AFTER mining and lamp."""
    calls, _ = patched
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(runner.mining_supervised, "mine_until_pickaxe_empty",
                        lambda c, t, **k: {"executed": [], "stopped_reason": "stub"})

    rep = run_device(
        "dev", spend=False, open_lamp=True,
        mining_config={"enabled": True},
        progress=lambda name, status, detail="": events.append((name, status)),
    )

    starts = [n for n, s in events if s == "start"]
    assert "main_tasks_late" in starts
    assert "main_tasks_late" in rep.tasks
    # late pass sits after mining and lamp in the run.
    assert starts.index("main_tasks_late") > starts.index("mining")
    assert starts.index("main_tasks_late") > starts.index("lamp")
    # and the early main_tasks pass still runs first.
    assert starts.index("main_tasks") < starts.index("main_tasks_late")


def test_weekly_ladder_uses_ws_except_emulator_5558(patched):
    calls, _ = patched

    report = run_device(
        "emulator-5556",
        cloud_ladder_enabled=True,
        ladder_reward_enabled=True,
    )

    assert ("ladder_reward", "apply") in calls
    assert ("cloud_ladder", "fight") in calls
    assert report.tasks["ladder_reward"]["ok"] is True
    assert report.tasks["cloud_ladder"]["completed"] is True

    calls.clear()
    report_5558 = run_device(
        "emulator-5558",
        cloud_ladder_enabled=True,
        ladder_reward_enabled=True,
    )
    assert ("ladder_reward", "apply") not in calls
    assert ("cloud_ladder", "fight") not in calls
    assert "ladder_reward" not in report_5558.tasks
    assert "cloud_ladder" not in report_5558.tasks


def test_ladder_reward_send_failure_raises_for_h5_fallback(monkeypatch):
    monkeypatch.setattr(
        runner.ladder_reward,
        "apply_if_due",
        lambda *a, **k: {"ok": False, "error": "send_failed"},
    )
    with pytest.raises(RuntimeError, match="send_failed"):
        runner._run_ladder_reward(object(), device="dev")


def test_main_chapter_kills_runs_after_primary_client_closes(
    patched, monkeypatch,
):
    _calls, holder = patched
    events = []
    from ws_token import main_chapter_kills

    def fake_kills(device, **kwargs):
        assert holder["client"].closed is True
        events.append((device, kwargs))
        return {"sent": 150, "target": 150}

    monkeypatch.setattr(main_chapter_kills, "run_daily", fake_kills)
    report = run_device(
        "dev",
        spend=False,
        main_chapter_kills_config={
            "enabled": True,
            "interval_sec": 2.5,
            "persist_every": 7,
        },
    )

    assert events[0][0] == "dev"
    assert events[0][1]["interval_sec"] == 2.5
    assert events[0][1]["persist_every"] == 7
    assert report.tasks["main_chapter_kills"]["sent"] == 150


def _last_index(seq, val):
    return len(seq) - 1 - seq[::-1].index(val)


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
    # caller-supplied chapter list; auto-derived lists are covered below.
    run_device("dev", spend=True, sweep_list=[(2, 150, 1, 1)])

    actions = {(t, a) for t, a in calls}
    assert ("guild", "donate_until_capped") in actions
    assert ("steward", "run_shopping") in actions
    assert ("steward", "run_dungeon_sweep") in actions


def test_spend_false_still_runs_active_housekeeper_sweep_each_wake(patched):
    calls, _ = patched

    run_device("dev", spend=False, sweep_list=[(7, 150, 1, 1)])

    actions = {(t, a) for t, a in calls}
    assert ("steward", "run_shopping") not in actions
    assert ("steward", "run_dungeon_sweep") in actions


# --- mail gate (opt-in via mail_claim; runs after redpack) ------------------

def test_mail_skipped_without_flag(patched):
    calls, _ = patched

    run_device("dev", spend=False)  # mail_claim defaults to False

    assert "mail" not in [t for t, _a in calls]


def test_mail_runs_after_redpack_when_enabled(patched):
    calls, _ = patched

    run_device("dev", spend=False, mail_claim=True)

    task_order = [t for t, _a in calls]
    assert "mail" in task_order
    # mail step sits between redpack and idle_reward (TASK_ORDER).
    assert task_order.index("redpack") < task_order.index("mail")
    assert task_order.index("mail") < task_order.index("idle_reward")


def test_spend_true_skips_sweep_without_chapter_list(patched):
    calls, _ = patched

    run_device("dev", spend=True)  # live setting probe fails on this minimal spy

    actions = {(t, a) for t, a in calls}
    # shopping still runs on spend, but the sweep is skipped (nothing configured)
    assert ("steward", "run_shopping") in actions
    assert ("steward", "run_dungeon_sweep") not in actions


def test_spend_true_auto_derives_housekeeper_sweep_list(patched, monkeypatch):
    calls, _ = patched
    captured = {}

    monkeypatch.setattr(
        runner.steward,
        "read_dungeon_setting",
        lambda _client: {1: {2: 1}, 2: {1: 1}, 7: {1: 1}},
    )
    monkeypatch.setattr(
        runner.steward,
        "read_dungeon_levels",
        lambda _client: {1: 639, 2: 150, 7: 49},
    )
    def fake_sweep(_client, sweep_list, **_kw):
        captured["sweep_list"] = list(sweep_list)
        calls.append(("steward", "run_dungeon_sweep"))
        return "SWEEP"

    monkeypatch.setattr(runner.steward, "run_dungeon_sweep", fake_sweep)

    run_device("dev", spend=True)

    assert captured["sweep_list"] == [
        (1, 639, 0, 0),
        (2, 150, 0, 1),
        (7, 49, 1, 0),
    ]


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
    per-call batch size plus a guarded total target."""
    captured: dict = {}

    def spy_open(c, **k):
        captured.update(k)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "dry_run": k.get("dry_run", True)}

    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(open_lamp=spy_open))

    run_device("dev", spend=False, open_lamp=True)

    assert captured.get("dry_run") is False          # REAL open, not simulated
    assert captured.get("batch_num") == runner._LAMP_BATCH_NUM  # API limit: 20 per call
    assert captured.get("max_batches") == runner._LAMP_MAX_BATCHES  # 20 * 500 = 10000
    assert captured.get("batch_delay") == runner._LAMP_BATCH_DELAY_SEC


def test_lamp_percent_min_keep_passed_with_initial_count_and_progress(
        patched, monkeypatch):
    """run_device(open_lamp=True, lamp_percent, lamp_min_keep) must forward the
    two knobs + the login-snapshot initial_count + a callable on_progress into
    lamp.open_lamp, and the lamp-count tee in _push must populate the holder."""
    from ws_token import lamp

    captured: dict = {}

    def spy_open(c, **k):
        captured.update(k)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "dry_run": k.get("dry_run", True),
                "target": 0, "initial_count": k.get("initial_count"),
                "remaining": None}

    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(
                            open_lamp=spy_open,
                            extract_lamp_count=lamp.extract_lamp_count))

    _calls, spy_holder = patched

    # A 0x0402 frame carrying item 1001 (神燈) with current qty 123. Mirrors the
    # consume push shape: f2 sub { f1=item_id, f3=current_count }.
    sub = codec.pb_uint(1, lamp.ITEM_LAMP) + codec.pb_uint(3, 123)
    frame_1001 = codec.pb_msg(2, sub)

    events: list[tuple[str, str, str]] = []

    rep = run_device(
        "dev", spend=False, open_lamp=True,
        lamp_percent=1.0, lamp_min_keep=500000, lamp_daily_min=60,
        progress=lambda name, status, detail="": events.append(
            (name, status, detail)))

    # The runner mounted a composite push handler before connect; feed the
    # login-time 0x0402 snapshot through it to prove the tee captured the count.
    push = spy_holder["push_handler"]
    assert push is not None
    push(0x0402, frame_1001)

    # NOTE: in this fixture the count is fed AFTER run_device returns (the spy
    # client does not emit pushes), so assert the holder mechanism directly via a
    # second, count-first scenario below; here assert the knobs + callable wiring.
    assert captured.get("lamp_percent") == 1.0
    assert captured.get("lamp_min_keep") == 500000
    assert captured.get("lamp_daily_min") == 60
    assert callable(captured.get("on_progress"))
    assert "initial_count" in captured

    # _lamp_progress routes through the progress callback as ("lamp","progress",..)
    captured["on_progress"](123, 456)
    assert ("lamp", "progress", "123/456") in events


def test_lamp_count_tee_populates_initial_count(monkeypatch):
    """When a 0x0402 snapshot with item 1001 lands BEFORE the lamp task runs, the
    runner passes that count as initial_count into open_lamp."""
    from ws_token import codec as _codec
    from ws_token import lamp

    captured: dict = {}

    def spy_open(c, **k):
        captured.update(k)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "dry_run": False, "target": 0,
                "initial_count": k.get("initial_count"), "remaining": None}

    sub = _codec.pb_uint(1, lamp.ITEM_LAMP) + _codec.pb_uint(3, 777)
    frame_1001 = _codec.pb_msg(2, sub)

    class _PushAtLoginClient:
        """Emits the 0x0402 lamp snapshot into the mounted push_handler on connect,
        exactly like the login-time inventory snapshot does in production."""

        def __init__(self, push_handler=None, **_kw):
            self._push = push_handler

        def connect(self):
            if self._push:
                self._push(0x0402, frame_1001)
            return {"code": 0, "role_id": 1, "serv_time": 99}

        def is_kicked(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(runner, "_make_client",
                        lambda creds, **k: _PushAtLoginClient(**k))
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)
    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(
                            open_lamp=spy_open,
                            extract_lamp_count=lamp.extract_lamp_count))

    run_device("dev", spend=False, open_lamp=True,
               lamp_percent=2.0, lamp_min_keep=10)

    assert captured.get("initial_count") == 777
    assert captured.get("lamp_percent") == 2.0
    assert captured.get("lamp_min_keep") == 10


def test_lamp_count_tee_not_loaded_when_open_lamp_false(monkeypatch):
    """Non-lamp devices must NOT import lamp: _load_lamp is never called and a
    0x0402 push is a harmless no-op (no lamp parse)."""
    load_calls: list[int] = []
    real_load = runner._load_lamp

    def counting_load():
        load_calls.append(1)
        return real_load()

    monkeypatch.setattr(runner, "_load_lamp", counting_load)

    class _NoLampClient:
        def __init__(self, push_handler=None, **_kw):
            self._push = push_handler

        def connect(self):
            # Even if a 0x0402 frame arrives, the non-lamp path must ignore it.
            if self._push:
                self._push(0x0402, b"\x12\x04\x08\xe9\x07\x18\x05")
            return {"code": 0, "role_id": 1, "serv_time": 99}

        def is_kicked(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(runner, "_make_client",
                        lambda creds, **k: _NoLampClient(**k))
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)

    rep = run_device("dev", spend=False, open_lamp=False)

    assert rep.login_ok is True
    assert load_calls == []  # lamp never imported for a non-lamp device


def test_lamp_failure_does_not_abort_report(patched, monkeypatch):
    calls, _ = patched

    def boom(c, **k):
        calls.append(("lamp", "open_lamp"))
        raise WSTimeoutError("lamp open timed out")

    monkeypatch.setattr(runner, "_load_lamp",
                        lambda: types.SimpleNamespace(open_lamp=boom))

    rep = run_device("dev", spend=False, open_lamp=True)

    assert "lamp" in rep.errors        # error recorded ...
    assert "lamp" not in rep.tasks     # ... and no bogus result
    assert rep.login_ok is True        # the rest of the run completed fine


# --- mining (opt-in; consumes mining tools) ---------------------------------

def test_mining_not_run_when_config_absent(patched):
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("mining", "mine_until_pickaxe_empty") not in {(t, a) for t, a in calls}
    assert "mining" not in rep.tasks


def test_mining_runs_when_config_enabled(patched, monkeypatch):
    calls, _ = patched
    captured = {}

    def fake_mine(client, tracker, **kwargs):
        calls.append(("mining", "mine_until_pickaxe_empty"))
        captured.update(kwargs)
        return {"executed": [{"goods_id": 4001}], "stopped_reason": "pickaxe_empty"}

    monkeypatch.setattr(runner.mining_supervised, "mine_until_pickaxe_empty", fake_mine)

    rep = run_device(
        "dev",
        spend=False,
        mining_config={
            "enabled": True,
            "allow_bomb": True,
            "allow_drill": True,
            "max_steps": 35,
        },
    )

    assert ("mining", "mine_until_pickaxe_empty") in {(t, a) for t, a in calls}
    assert captured["allow_bomb"] is True
    assert captured["allow_drill"] is True
    assert captured["max_steps"] == 35
    assert "mining" in rep.tasks


# --- relic (opt-in via relic_upgrade; SPENDS 遺物碎片) -----------------------

def test_relic_skipped_when_relic_upgrade_false(patched):
    """Default relic_upgrade=False must self-skip (no relics read, no spend)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert "relic" in rep.tasks                       # step always runs ...
    assert rep.tasks["relic"]["skipped"]              # ... but self-skips
    # even spend=True alone must not enable relic upgrades (independent flag)
    rep2 = run_device("dev", spend=True, sweep_list=[(1, 5, 10)])
    assert rep2.tasks["relic"]["skipped"]


def test_relic_runs_calls_plan_and_upgrade_bounded(patched, monkeypatch):
    """relic_upgrade=True reads relics, plans the balanced upgrade, and executes
    each planned uid via upgrade_relic — bounded by relic_max_steps."""
    from ws_token import relic

    seen: dict = {}

    class _Info:
        success = True
        response_cmd = relic.CMD_RELIC_INFO
        error_code = None
        # two equipped relics (location>0) at different levels + one unequipped
        equipped = (relic.Relic(uid=1, cfg_id=4001, quality=2, location=1, level=10),
                    relic.Relic(uid=2, cfg_id=4002, quality=2, location=2, level=12))

    def fake_plan(relics, fragments, *, cost_at, max_steps=None, **k):
        seen["relics"] = list(relics)
        seen["fragments"] = fragments
        seen["max_steps"] = max_steps
        # honour the cap the runner passes through
        return [1, 1, 2][:(max_steps if max_steps is not None else 3)]

    upgraded: list[int] = []

    def fake_upgrade(client, uid, **k):
        upgraded.append(uid)
        return types.SimpleNamespace(success=True, relic=None, response_cmd=relic.CMD_RELIC_UP,
                                     error_code=None)

    monkeypatch.setattr(runner.relic, "read_relics", lambda c, **k: _Info())
    monkeypatch.setattr(runner.relic, "plan_balanced_upgrades", fake_plan)
    monkeypatch.setattr(runner.relic, "upgrade_relic", fake_upgrade)

    rep = run_device("dev", spend=False, relic_upgrade=True, relic_max_steps=2)

    assert seen["relics"] == [(1, 10), (2, 12)]   # equipped only, (uid, level)
    assert seen["max_steps"] == 2                  # cap forwarded
    assert upgraded == [1, 1]                       # exactly the (bounded) plan
    assert rep.tasks["relic"]["upgraded"] == 2
    assert rep.tasks["relic"]["steps"] == [1, 1]


def test_relic_stops_on_0x0201(patched, monkeypatch):
    """A server rejection (0x0201) on a relic_up step stops the loop safely."""
    from ws_token import relic

    class _Info:
        success = True
        response_cmd = relic.CMD_RELIC_INFO
        error_code = None
        equipped = (relic.Relic(uid=1, cfg_id=4001, quality=2, location=1, level=10),)

    calls_made: list[int] = []

    def fake_upgrade(client, uid, **k):
        calls_made.append(uid)
        # first step ok, second rejected (out of fragments / cap)
        ok = len(calls_made) == 1
        return types.SimpleNamespace(
            success=ok, relic=None, response_cmd=(relic.CMD_RELIC_UP if ok else relic.CMD_ERROR),
            error_code=(None if ok else 25))

    monkeypatch.setattr(runner.relic, "read_relics", lambda c, **k: _Info())
    monkeypatch.setattr(runner.relic, "plan_balanced_upgrades",
                        lambda *a, **k: [1, 1, 1])
    monkeypatch.setattr(runner.relic, "upgrade_relic", fake_upgrade)

    rep = run_device("dev", spend=False, relic_upgrade=True, relic_max_steps=10)

    assert calls_made == [1, 1]                          # stopped after the rejection
    assert rep.tasks["relic"]["upgraded"] == 1
    assert rep.tasks["relic"]["stopped_reason"] == "error_code=25"


def test_relic_stops_at_fragment_floor(patched, monkeypatch):
    """When the live 遺物碎片 count (from the inventory tracker) drops below the
    configured floor, the relic loop stops before the next upgrade."""
    from ws_token import codec, relic
    from ws_token.mining import (
        CMD_INVENTORY_PUSH, INV_EVT_CONSUME, INV_EVT_SNAPSHOT,
    )

    class _Info:
        success = True
        response_cmd = relic.CMD_RELIC_INFO
        error_code = None
        equipped = (relic.Relic(uid=1, cfg_id=4001, quality=2, location=1, level=10),)

    # The runner threads its inventory_tracker into _run_relic. Seed the fragment
    # count to 100 at login, then have each upgrade push a 0x0402 dropping it to 40
    # (below the floor of 50) so the loop stops after one upgrade.
    upgraded: list[int] = []

    def _frag_push(count, evt_type):
        # one item sub {id=100022, count} under a recognised inventory evt type
        sub = codec.pb_uint(1, relic.RELIC_FRAGMENT_ITEM) + codec.pb_uint(3, count)
        return codec.pb_uint(1, evt_type) + codec.pb_msg(2, sub)

    def fake_upgrade(client, uid, **k):
        upgraded.append(uid)
        # simulate the server's post-upgrade 0x0402 consume push lowering the count
        client._push_handler(CMD_INVENTORY_PUSH, _frag_push(40, INV_EVT_CONSUME))
        return types.SimpleNamespace(success=True, relic=None,
                                     response_cmd=relic.CMD_RELIC_UP, error_code=None)

    monkeypatch.setattr(runner.relic, "read_relics", lambda c, **k: _Info())
    monkeypatch.setattr(runner.relic, "plan_balanced_upgrades",
                        lambda *a, **k: [1, 1, 1, 1])
    monkeypatch.setattr(runner.relic, "upgrade_relic", fake_upgrade)

    # Build a client whose push_handler is the runner's composite handler; the
    # login snapshot seeds 100 fragments before the relic task runs.
    class _FragClient:
        def __init__(self, push_handler=None, **_kw):
            self._push_handler = push_handler

        def connect(self):
            if self._push_handler:
                self._push_handler(CMD_INVENTORY_PUSH,
                                   _frag_push(100, INV_EVT_SNAPSHOT))
            return {"code": 0, "role_id": 1, "serv_time": 99}

        def is_kicked(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(runner, "_make_client", lambda creds, **k: _FragClient(**k))
    monkeypatch.setattr(runner, "load_creds", lambda device, **k: CREDS)

    rep = run_device("dev", spend=False, relic_upgrade=True,
                     relic_max_steps=10, relic_fragment_floor=50)

    assert upgraded == [1]                                   # stopped at the floor
    assert rep.tasks["relic"]["upgraded"] == 1
    assert rep.tasks["relic"]["stopped_reason"] == "fragments<50"


def test_relic_task_order_after_steward_before_kungfu():
    order = list(runner.TASK_ORDER)
    assert order.index("steward") < order.index("relic") < order.index("kungfu_store")


# --- tycoon (傳奇大亨 大富翁 auto-dice; opt-in via tycoon) --------------------

def test_tycoon_skipped_when_disabled(patched):
    """Default tycoon=False must self-skip (no auto_play, no rolls)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert "tycoon" in rep.tasks
    assert rep.tasks["tycoon"]["skipped"]


def test_tycoon_runs_calls_auto_play_with_max_rolls(patched, monkeypatch):
    """tycoon=True calls tycoon.auto_play with the configured max_rolls."""
    seen: dict = {}

    def fake_auto_play(client, *, max_rolls, **k):
        seen["max_rolls"] = max_rolls
        return {"rolls": 3, "total_rewards": {1401: 60}, "last_pos": 7,
                "last_circle": 0, "stopped_reason": "error_code=25"}

    monkeypatch.setattr(runner.tycoon, "auto_play", fake_auto_play)

    rep = run_device("dev", spend=False, tycoon=True, tycoon_max_rolls=12)

    assert seen["max_rolls"] == 12
    assert rep.tasks["tycoon"]["rolls"] == 3
    # a closed/exhausted activity stops on 0x0201 — surfaced as stopped_reason
    assert rep.tasks["tycoon"]["stopped_reason"] == "error_code=25"


def test_tycoon_closed_activity_is_safe_noop(patched, monkeypatch):
    """When the activity is closed the first roll returns 0x0201 and auto_play
    stops with rolls=0 — a safe no-op that records a summary, not an error."""
    def fake_auto_play(client, *, max_rolls, **k):
        return {"rolls": 0, "total_rewards": {}, "last_pos": 0,
                "last_circle": 0, "stopped_reason": "error_code=173"}

    monkeypatch.setattr(runner.tycoon, "auto_play", fake_auto_play)

    rep = run_device("dev", spend=False, tycoon=True)

    assert "tycoon" not in rep.errors
    assert rep.tasks["tycoon"]["rolls"] == 0


def test_tycoon_task_order_after_turntable():
    order = list(runner.TASK_ORDER)
    assert order.index("turntable") < order.index("tycoon")


def test_harvest_card_task_order_after_farm_before_dungeon():
    order = list(runner.TASK_ORDER)
    assert order.index("farm") < order.index("harvest_card") < order.index("dungeon")


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


def test_farm_does_not_run_harvest_card_cycle_inside_farm(patched):
    """豐收卡是獨立 tag，farm 子流程不得再內嵌執行，避免同輪重複買/用卡。"""
    calls, _ = patched

    rep = run_device("dev", spend=False,
                     farm_config={"harvest_card_cycle": {"enabled": True}})

    assert ("harvest_card", "run_harvest_card_cycle") in {(t, a) for t, a in calls}
    assert "harvest_card" in rep.tasks
    assert "harvest_card_cycle" not in rep.tasks["farm"]


def test_farm_reads_work_status_first(patched):
    """_run_farm probes 打工 status (worker module 73, reliable) before touching
    the flaky home module — recorded on the summary."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("farm", "read_work_status") in {(t, a) for t, a in calls}
    assert rep.tasks["farm"]["work_status"]["running"] is False


def test_farm_skips_manual_harvest_when_worker_running(patched, monkeypatch):
    """打工 running -> 管家 auto-harvests; skip the flaky/racy manual home-module
    read + harvest entirely (avoids the cmd=3081 no-reply timeout)."""
    calls, _ = patched

    monkeypatch.setattr(runner.farm, "read_work_status",
                        lambda c, rid, **k: (calls.append(("farm", "read_work_status"))
                                             or {"running": True, "worker_status": 1,
                                                 "team_cfg_id": 7001, "found": True}))

    rep = run_device("dev", spend=False,
                     farm_config={"seed_id": 102, "buy": [{"shop_id": 407, "target": 4}]})

    actions = {(t, a) for t, a in calls}
    assert ("farm", "read_farm") not in actions      # home module not touched
    assert ("farm", "harvest_ready") not in actions
    assert ("farm", "plant_empty") not in actions    # 管家 auto-plants too
    assert "farm" not in rep.errors
    assert "skipped" in rep.tasks["farm"]["harvest"]
    # buy (shop module, reliable) still runs independent of 打工 status
    assert ("farm", "buy_farm_shop") in actions


def test_farm_read_timeout_still_runs_buy(patched, monkeypatch):
    """home_farm_info (3077) timing out must NOT fail the whole farm task — the
    reliable 莊園購買 (shop module) still runs. Regression: read_farm raised and
    the buy was silently lost on ~50% of wakes (5554 live, 2026-06-14)."""
    calls, _ = patched

    def boom(c, rid, **k):
        calls.append(("farm", "read_farm"))
        raise WSTimeoutError("no response for cmd=3077 (expected one of (3077,))")

    monkeypatch.setattr(runner.farm, "read_farm", boom)

    rep = run_device("dev", spend=False,
                     farm_config={"buy": [{"shop_id": 407, "target": 4}]})

    actions = {(t, a) for t, a in calls}
    assert ("farm", "read_farm") in actions          # attempted ...
    assert ("farm", "harvest_ready") not in actions   # ... but the read failed
    assert "farm" not in rep.errors                   # task did NOT abort
    assert "skipped" in rep.tasks["farm"]["harvest"]  # harvest degraded gracefully
    assert ("farm", "buy_farm_shop") in actions       # the buy was NOT lost


def test_dungeon_sweep_skipped_without_config(patched):
    """No dungeon_sweeps -> dungeon task is skipped (run_sweep never called)."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("dungeon", "run_sweep") not in {(t, a) for t, a in calls}
    assert rep.tasks["dungeon"]["skipped"]


def test_harvest_card_skipped_without_config(patched):
    """No harvest_card_cycle config -> harvest_card task is present but skipped."""
    calls, _ = patched

    rep = run_device("dev", spend=False)

    assert ("harvest_card", "run_harvest_card_cycle") not in {(t, a) for t, a in calls}
    assert rep.tasks["harvest_card"]["skipped"]


def test_harvest_card_runs_three_cards_with_config(patched):
    """harvest_card_cycle.enabled -> independent tag runs the existing 3-card cycle."""
    calls, _ = patched

    rep = run_device("dev", spend=False,
                     farm_config={"harvest_card_cycle": {"enabled": True}})

    assert ("harvest_card", "run_harvest_card_cycle") in {(t, a) for t, a in calls}
    assert rep.tasks["harvest_card"]["cards_bought"] == 3


def test_harvest_card_custom_num_cards_passed(patched, monkeypatch):
    """num_cards remains configurable while defaulting to weekly 3 cards."""
    seen = {}

    def fake_cycle(c, rid, **k):
        seen.update(k)
        return {"ok": True, "cards_bought": k.get("num_cards")}

    monkeypatch.setattr(runner.farm, "run_harvest_card_cycle", fake_cycle)

    rep = run_device("dev", spend=False,
                     farm_config={"harvest_card_cycle": {
                         "enabled": True, "num_cards": 2, "fertilizer_id": 222
                     }})

    assert rep.tasks["harvest_card"]["cards_bought"] == 2
    assert seen["num_cards"] == 2
    assert seen["fertilizer_id"] == 222


def test_harvest_card_weekly_gate_records_success(monkeypatch, tmp_path):
    """同一 ISO week 只跑一次；成功才寫 gate，避免每次喚醒都用豐收卡。"""
    from datetime import datetime
    from ws_token import runner

    calls = []
    monkeypatch.setattr(
        runner.farm, "run_harvest_card_cycle",
        lambda c, rid, **k: calls.append(k) or {"ok": True, "cards_bought": 3},
    )
    cfg = {"harvest_card_cycle": {"enabled": True}}
    now = datetime(2026, 6, 22, 9, 0, 0)

    first = runner._run_harvest_card(
        object(), role_id=1, farm_config=cfg, inventory_tracker=None,
        device="dev", state_dir=tmp_path, now=now,
    )
    second = runner._run_harvest_card(
        object(), role_id=1, farm_config=cfg, inventory_tracker=None,
        device="dev", state_dir=tmp_path, now=now.replace(hour=15),
    )

    assert first["cards_bought"] == 3
    assert second["skipped"] == "already done 2026-W26"
    assert len(calls) == 1


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


def test_login_failure_preserves_explicit_kick_metadata(patched, monkeypatch):
    """A cmd-259 arriving before login reply still reaches cooldown routing."""
    _calls, spy_holder = patched

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(
            connect_error=WSLoginError("timed out waiting for role_login_s2c"),
            kicked=True,
            kick_reason=KICK_REASON_EXPLICIT,
        )
        spy.close_reason = "explicit_login_conflict"
        spy.close_detail = "cmd=259 reason=20"
        spy_holder["client"] = spy
        return spy

    monkeypatch.setattr(runner, "_make_client", fake_make_client)

    rep = run_device("dev", spend=False)

    assert rep.login_ok is False
    assert rep.kicked is True
    assert rep.kick_reason == KICK_REASON_EXPLICIT
    assert rep.close_reason == "explicit_login_conflict"
    assert rep.close_detail == "cmd=259 reason=20"


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


@pytest.mark.parametrize("reason", [KICK_REASON_EXPLICIT, KICK_REASON_TRANSPORT_DROP])
def test_report_preserves_kick_reason(patched, monkeypatch, reason):
    """The runner exposes the client reason so runtime layers can classify it."""
    _calls, spy_holder = patched

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(kicked=True, kick_reason=reason)
        spy_holder["client"] = spy
        return spy

    monkeypatch.setattr(runner, "_make_client", fake_make_client)

    rep = run_device("dev", spend=False)

    assert rep.kicked is True
    assert rep.kick_reason == reason


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
    assert rep.close_reason == "explicit_login_conflict"
    assert rep.close_detail == "cmd=259 reason=20"


def test_report_propagates_transport_close_reason(patched, monkeypatch):
    """Hybrid callers receive the reason instead of inferring it from kicked."""
    _calls, spy_holder = patched

    def fake_make_client(creds, **kwargs):
        spy = _SpyClient(kicked=True)
        spy.close_reason = "transport_drop"
        spy.close_detail = "recv error WebSocketConnectionClosedException: socket is already closed"
        spy_holder["client"] = spy
        return spy

    monkeypatch.setattr(runner, "_make_client", fake_make_client)

    rep = run_device("dev", spend=False)

    assert rep.kicked is True
    assert rep.close_reason == "transport_drop"
    assert "socket is already closed" in (rep.close_detail or "")


# --- end-to-end over the real task code + FakeTransport responder ----------

def test_run_device_end_to_end_over_fake_transport(monkeypatch):
    """Drive the REAL task orchestrators against a scripted responder to prove
    the wiring (cmd ids, body building, push collection) is sound end-to-end."""
    from ws_token import (
        ad_reward, couple, farm, idle_reward, league_solo, main_tasks, redpack,
        spirit, steward, turntable, workshop,
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
        # turntable ad top-up: ad_info -> config 13 already maxed (2/2) so
        # claim_ad skips WITHOUT sending 0x1602; then info -> num=0 (no spins)
        ad_reward.CMD_AD_INFO: lambda b: [
            s2c(ad_reward.CMD_AD_INFO,
                codec.pb_msg(1, codec.pb_uint(1, 13) + codec.pb_uint(2, 2)))],
        turntable.CMD_INFO: lambda b: [
            s2c(turntable.CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 0))],
        # farm: 打工偵測 -> empty team_list (worker NOT running -> manual path)
        farm.CMD_GET_OTHER_WORKER: lambda b: [s2c(farm.CMD_GET_OTHER_WORKER, b"")],
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
        # spirit: empty pool list -> no free draws
        spirit.CMD_DRAW_INFO: lambda b: [s2c(spirit.CMD_DRAW_INFO, b"")],
    # workshop: empty info -> no team workshops to rotate
        workshop.CMD_INFO: lambda b: [s2c(workshop.CMD_INFO, b"")],
        # couple: empty favor list + lover_id=0 -> no partner, skipped
        couple.CMD_FAVOR_INFO: lambda b: [s2c(couple.CMD_FAVOR_INFO, b"")],
        couple.CMD_STATUS: lambda b: [s2c(couple.CMD_STATUS, b"")],
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
    # sandbox the workshop rotation cadence state (no writes to repo ws_state/)
    _state: dict = {}
    monkeypatch.setattr(runner.ws_state, "load_state",
                        lambda device, **k: dict(_state))
    monkeypatch.setattr(runner.ws_state, "save_state",
                        lambda device, data, **k: _state.update(data))

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
    # spirit / workshop / couple ran end-to-end over the real code
    assert rep.tasks["spirit"]["pools_drawn"] == 0
    # workshop: empty info -> no team workshops to rotate -> empty result
    assert rep.tasks["workshop"]["switched"] == []
    assert rep.tasks["couple"]["skipped"] == "no partner"
    # lamp is opt-in (open_lamp defaults False) so it must NOT have run.
    assert "lamp" not in rep.tasks
    # 競猜商店 is opt-in (kungfu_guess defaults False) so it must NOT have run —
    # this also proves the gated task never sends a stray shop_buy on a free pass.
    assert "kungfu_store" not in rep.tasks


# --- spirit / workshop / couple wiring ---------------------------------------

def test_task_order_has_home_features_before_lamp():
    from ws_token import runner
    order = list(runner.TASK_ORDER)
    assert order.index("spirit") > order.index("carpark")
    assert order.index("mining") > order.index("couple")
    assert order.index("mining") < order.index("lamp")
    # 守護靈→秘寶→工坊→伴侶 home-feature tail sits before mining/lamp; 秘寶(尋寶)
    # runs right after 守護靈(spirit). (robust relative order — tolerates the
    # dragon_realm/sea_season tasks that sit between couple and mining.)
    assert order.index("spirit") < order.index("secret_jewel") < order.index("workshop")
    assert order.index("workshop") < order.index("couple") < order.index("mining")
    # 主線擊殺另開 WS，排在主連線尾端領取與關閉之後。
    assert order[-1] == "main_chapter_kills"
    assert order.index("lamp") < order.index("main_tasks_late")
    assert order.index("main_tasks_late") < order.index("main_chapter_kills")
    # 萬神試煉 本周積分獎勵 sits in the free group, after dungeon and before guild.
    assert order.index("dungeon") < order.index("rogue") < order.index("guild")
    assert order.index("rogue") < order.index("ladder_reward")
    assert order.index("ladder_reward") < order.index("cloud_ladder") < order.index("arena")
    # 競猜商店 (粉鑽 買競猜幣) sits with the shopping/cost group: after steward,
    # before the spirit/workshop/couple/mining/lamp tail.
    assert order.index("steward") < order.index("kungfu_store") < order.index("spirit")


class _FakeKungfuClient:
    """Scripts shop_buy success for the 競猜商店 wiring test."""

    def __init__(self, accept_all=True):
        self.accept_all = accept_all
        self.calls: list = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        from ws_token import kungfu_store as ks
        self.calls.append((cmd, bytes(body)))
        if self.accept_all:
            return ks.CMD_SHOP_BUY, b""
        return ks.CMD_ERR, b""


class _FakeKungfuWorshipClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        from ws_token import kungfu_race as kr
        self.calls.append((cmd, bytes(body), tuple(expect_cmds), timeout))
        return self.reply


def test_run_kungfu_worship_helper_sends_empty_body():
    from ws_token import kungfu_race as kr
    from ws_token import runner

    client = _FakeKungfuWorshipClient((kr.CMD_WORSHIP, codec.pb_uint(1, 21312)))
    summary = runner._run_kungfu_worship(client)

    assert summary == {"worship": 21312, "response_cmd": kr.CMD_WORSHIP}
    assert client.calls == [
        (kr.CMD_WORSHIP, b"", (kr.CMD_WORSHIP, kr.CMD_ERROR), 6.0)
    ]


def test_run_kungfu_worship_helper_marks_server_rejection_skipped():
    from ws_token import kungfu_race as kr
    from ws_token import runner

    client = _FakeKungfuWorshipClient((kr.CMD_ERROR, codec.pb_uint(1, 173)))
    summary = runner._run_kungfu_worship(client)

    assert summary == {"skipped": "server_rejected", "error_code": 173}


def test_run_kungfu_store_helper_buys_to_cap():
    from ws_token import runner
    client = _FakeKungfuClient(accept_all=True)
    summary = runner._run_kungfu_store(client)
    # all four tiers to cap: 100 + 200 + 300*2 + 500*3 = 2400 競猜幣
    assert summary["coins"] == 2400
    assert summary["diamonds_spent"] == 12600
    assert summary["bought"] == {15001: 1, 15002: 1, 15003: 2, 15004: 3}


def test_run_kungfu_store_helper_noop_when_event_closed():
    from ws_token import runner
    client = _FakeKungfuClient(accept_all=False)
    summary = runner._run_kungfu_store(client)
    assert summary["coins"] == 0
    assert summary["bought"] == {}
    # one probe per tier then stop -> exactly 4 calls, never over-tries
    assert len(client.calls) == 4


# --- rogue (萬神試煉 本周積分獎勵) Friday gate --------------------------------

class _FakeRogueClient:
    """Minimal client: records call_for and returns a scripted (cmd, body)."""

    def __init__(self, reply):
        self._reply = reply
        self.calls: list = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.calls.append((cmd, bytes(body)))
        return self._reply


def _a_friday():
    from datetime import datetime, timedelta
    d = datetime(2026, 6, 1, 9, 0, 0)
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)
    return d


def _reward_reply():
    from ws_token import rogue
    body = b"".join(codec.pb_uint(1, i) for i in (1, 2, 3))
    body += codec.pb_msg(2, codec.pb_uint(1, 1501) + codec.pb_uint(2, 200))
    return (rogue.CMD_WEEK_REWARD, body)


def test_run_rogue_claims_once_on_friday(tmp_path):
    from ws_token import runner
    client = _FakeRogueClient(_reward_reply())
    out = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=_a_friday())
    assert out["claimed_run"] is True
    assert out["success"] is True
    assert out["claimed"] == [1, 2, 3]
    assert out["rewards"] == {1501: 200}
    # the request is cmd 19482 with an EMPTY body
    assert client.calls == [(runner.rogue.CMD_WEEK_REWARD, b"")]


def test_run_rogue_skips_when_not_friday(tmp_path):
    from datetime import timedelta
    from ws_token import runner
    client = _FakeRogueClient(_reward_reply())
    saturday = _a_friday() + timedelta(days=1)
    out = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=saturday)
    assert out["claimed_run"] is False
    assert "not Friday" in out["reason"]
    assert client.calls == []  # nothing sent on a non-Friday


def test_run_rogue_same_friday_claims_only_once(tmp_path):
    from ws_token import runner
    client = _FakeRogueClient(_reward_reply())
    friday = _a_friday()
    first = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    second = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    assert first["claimed_run"] is True
    assert second["claimed_run"] is False
    assert "already claimed" in second["reason"]
    assert len(client.calls) == 1  # persisted last_date gates the 2nd hourly wake


def test_run_rogue_failure_not_persisted_so_retries(tmp_path):
    from ws_token import rogue, runner
    client = _FakeRogueClient((rogue.CMD_ERROR, codec.pb_uint(1, 173)))
    friday = _a_friday()
    out = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    assert out["claimed_run"] is True
    assert out["success"] is False
    assert out["error_code"] == 173
    # no last_date written on failure -> the next Friday wake retries (sends again).
    runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    assert len(client.calls) == 2


def test_run_rogue_dormant_timeout_is_benign(tmp_path):
    """A dormant 萬神試煉 週積分 event never answers 19482 — and sends NO 0x0201
    error frame either — so the call times out (WSTimeoutError). _run_rogue must
    treat that as a benign skip (returns a dict, NOT re-raised) so _safe records it
    under tasks not errors, must probe with the SHORT timeout (not the 15s default),
    and must NOT persist last_date so a later Friday wake (event opened) re-probes.
    Mirrors guild treasure's dormant-event handling.
    """
    from ws_token import runner
    from ws_token.client import WSTimeoutError

    class _TimeoutRogueClient:
        def __init__(self):
            self.calls: list = []

        def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
            self.calls.append((cmd, bytes(body), timeout))
            raise WSTimeoutError(
                "no response for cmd=19482 (expected one of (19482, 513))")

    client = _TimeoutRogueClient()
    friday = _a_friday()
    out = runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    assert out["claimed_run"] is False
    assert "dormant" in out["reason"]
    # short probe timeout used (fail fast), not the 15s default call_timeout
    assert client.calls[0][2] == runner._ROGUE_PROBE_S
    # not persisted -> a later Friday wake re-probes (sends again, no permanent skip)
    runner._run_rogue(client, device="dev", state_dir=tmp_path, now=friday)
    assert len(client.calls) == 2


def test_run_couple_no_partner_skips(monkeypatch):
    from ws_token import runner
    monkeypatch.setattr(runner.couple, "read_favor_info", lambda c: [])
    monkeypatch.setattr(runner.couple, "read_partner", lambda c: 0)
    out = runner._run_couple(object(), gifts=True, forge_ring=False)
    assert out["skipped"] == "no partner"


def test_run_couple_gifts_milk_tea_then_flower(monkeypatch, tmp_path):
    from datetime import datetime

    from ws_token import couple, runner

    sent = []
    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, *, friend_id, flower_id: sent.append((friend_id, flower_id))
        or {"batches_ok": 1, "stopped_reason": "error_code=3"})
    out = runner._run_couple(object(), gifts=True, forge_ring=False,
                             device="dev", state_dir=tmp_path,
                             now=datetime(2026, 6, 14, 9, 0, 0))
    assert sent == [(111, couple.MILK_TEA), (111, couple.FLOWER)]
    assert out["ring"] is None
    assert out.get("gifts_skipped") is None


def test_run_couple_gifts_once_per_calendar_day(monkeypatch, tmp_path):
    # 使用者 2026-06-14: 花跟奶茶 一天只送一次。同一天第二次喚醒不再送。
    from datetime import datetime

    from ws_token import couple, runner

    sent = []
    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, *, friend_id, flower_id: sent.append((friend_id, flower_id))
        or {"batches_ok": 1, "stopped_reason": "error_code=3"})
    day = datetime(2026, 6, 14, 9, 0, 0)
    runner._run_couple(object(), gifts=True, forge_ring=False,
                       device="dev", state_dir=tmp_path, now=day)
    second = runner._run_couple(object(), gifts=True, forge_ring=False,
                                device="dev", state_dir=tmp_path,
                                now=day.replace(hour=15))
    # only the FIRST wake sent the two gifts; the 2nd same-day wake skipped them
    assert sent == [(111, couple.MILK_TEA), (111, couple.FLOWER)]
    assert "already gifted 2026-06-14" in second["gifts_skipped"]


def test_run_couple_gifts_resend_next_calendar_day(monkeypatch, tmp_path):
    from datetime import datetime

    from ws_token import couple, runner

    sent = []
    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, *, friend_id, flower_id: sent.append((friend_id, flower_id))
        or {"batches_ok": 1, "stopped_reason": "error_code=3"})
    runner._run_couple(object(), gifts=True, forge_ring=False,
                       device="dev", state_dir=tmp_path,
                       now=datetime(2026, 6, 14, 9, 0, 0))
    runner._run_couple(object(), gifts=True, forge_ring=False,
                       device="dev", state_dir=tmp_path,
                       now=datetime(2026, 6, 15, 9, 0, 0))
    # two gifts each day -> 4 sends across two calendar days
    assert sent == [(111, couple.MILK_TEA), (111, couple.FLOWER),
                    (111, couple.MILK_TEA), (111, couple.FLOWER)]


def test_run_couple_no_partner_does_not_write_gate(monkeypatch, tmp_path):
    # No partner -> skip BEFORE the gate; nothing persisted (so a partner that
    # appears later the same day still gets that day's gift).
    from datetime import datetime

    from ws_token import runner
    from ws_token import state as ws_state

    monkeypatch.setattr(runner.couple, "read_favor_info", lambda c: [])
    monkeypatch.setattr(runner.couple, "read_partner", lambda c: 0)
    out = runner._run_couple(object(), gifts=True, forge_ring=False,
                             device="dev", state_dir=tmp_path,
                             now=datetime(2026, 6, 14, 9, 0, 0))
    assert out["skipped"] == "no partner"
    assert ws_state.load_state("dev", state_dir=tmp_path).get("couple") is None


def test_run_couple_forge_ring_independent_of_gift_gate(monkeypatch, tmp_path):
    # forge_ring (戒指錘鍊) is NOT day-gated: even when the gifts are gate-skipped
    # for today, an opted-in forge still runs.
    from datetime import datetime

    from ws_token import couple, runner

    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, **kw: {"batches_ok": 0, "stopped_reason": "error_code=3"})
    called = []
    monkeypatch.setattr(runner.couple, "forge_ring_until_empty",
                        lambda c: called.append(1) or {"forges": 2})
    day = datetime(2026, 6, 14, 9, 0, 0)
    runner._run_couple(object(), gifts=True, forge_ring=False,
                       device="dev", state_dir=tmp_path, now=day)
    assert called == []
    # same day: gifts gate-skipped, but forge_ring still runs when opted in
    out = runner._run_couple(object(), gifts=True, forge_ring=True,
                             device="dev", state_dir=tmp_path, now=day)
    assert called == [1]
    assert "already gifted" in out["gifts_skipped"]


class _FakeTracker:
    """Minimal InventoryTracker stand-in exposing only ``counts``."""

    def __init__(self, counts):
        self.counts = dict(counts)


def test_run_workshop_passes_tracker_counts_as_materials(monkeypatch, tmp_path):
    # _run_workshop threads inventory_tracker.counts into rotate_team_recipes
    # and writes the first parity after a confirmed target.
    from ws_token import runner
    seen = {}
    monkeypatch.setattr(
        runner.workshop,
        "rotate_team_recipes",
        lambda c, *, materials, parity: seen.update(materials)
        or {"parity": parity, "switched": [
            {"team_cfg_id": 6002, "action": "switched",
             "food_id": 8005, "chosen": {"ok": True}}]},
    )
    tracker = _FakeTracker({6017: 7, 6019: 118, 6020: 118, 6021: 1138})
    out = runner._run_workshop(object(), tracker, device="devA",
                               state_dir=tmp_path, now=1000)
    assert seen == {6017: 7, 6019: 118, 6020: 118, 6021: 1138}
    assert out["rotated"] is True
    assert out["switched"][0]["food_id"] == 8005
    assert "missing_materials" not in out


def test_run_workshop_rotates_only_after_12h(monkeypatch, tmp_path):
    # The first pass uses parity 0, the next pass inside 12h is gated, and the
    # following pass uses parity 1.
    from ws_token import runner
    calls = []

    def fake_rotate(_client, *, materials, parity):
        calls.append((dict(materials), parity))
        return {"parity": parity, "switched": [
            {"team_cfg_id": 6002, "reason": "already_selected",
             "chosen": {"ok": True}}]}

    monkeypatch.setattr(runner.workshop, "rotate_team_recipes", fake_rotate)
    tracker = _FakeTracker({6017: 4, 6019: 4, 6020: 4, 6021: 4})
    first = runner._run_workshop(object(), tracker, device="devB",
                                 state_dir=tmp_path, now=1000)
    gated = runner._run_workshop(object(), tracker, device="devB",
                                 state_dir=tmp_path, now=1000 + 11 * 3600)
    second = runner._run_workshop(object(), tracker, device="devB",
                                  state_dir=tmp_path, now=1000 + 12 * 3600 + 1)
    assert first["rotated"] is True
    assert gated["rotated"] is False
    assert second["rotated"] is True
    assert [parity for _materials, parity in calls] == [0, 1]


def test_run_workshop_warns_and_reports_missing_materials(monkeypatch):
    # 防呆: a recipe material absent from the 0x0402 snapshot -> reported in
    # missing_materials; that material is still passed as absent (producible=0
    # downstream), never forged into a count.
    from ws_token import runner
    seen = {}
    monkeypatch.setattr(
        runner.workshop,
        "rotate_team_recipes",
        lambda c, *, materials, parity: seen.update(materials)
        or {"parity": parity, "switched": []},
    )
    # only 6017 present; 6019/6020/6021 (needed by 8005) are missing
    tracker = _FakeTracker({6017: 10})
    out = runner._run_workshop(object(), tracker, device="devC")
    assert out["missing_materials"] == [6019, 6020, 6021]
    assert seen == {6017: 10}  # missing keys NOT fabricated


def test_run_workshop_no_missing_when_all_materials_present(monkeypatch):
    from ws_token import runner
    monkeypatch.setattr(
        runner.workshop,
        "rotate_team_recipes",
        lambda c, *, materials, parity: {"parity": parity, "switched": []},
    )
    tracker = _FakeTracker({6017: 1, 6019: 1, 6020: 1, 6021: 1})
    out = runner._run_workshop(object(), tracker, device="devD")
    assert "missing_materials" not in out


def test_run_spirit_draws_free(monkeypatch):
    from ws_token import runner
    monkeypatch.setattr(runner.spirit, "draw_all_free",
                        lambda c: {"pools_drawn": 2, "rewards": {}, "results": []})
    assert runner._run_spirit(object())["pools_drawn"] == 2


# --- progress callback --------------------------------------------------------

def test_progress_callback_reports_each_task(patched):
    """progress(name, status, detail) fires start+ok per task, in TASK_ORDER."""
    _calls, _ = patched
    events: list[tuple[str, str]] = []

    run_device("dev", spend=False,
               progress=lambda name, status, detail="": events.append((name, status)))

    started = [n for n, s in events if s == "start"]
    assert started == [n for n in runner.TASK_ORDER
                       if n in started]  # follows TASK_ORDER
    assert ("main_tasks", "start") in events
    assert ("main_tasks", "ok") in events
    assert ("couple", "ok") in events


def test_progress_callback_reports_error_and_never_breaks_run(patched, monkeypatch):
    """A failing task reports 'error'; a raising callback must not abort tasks."""
    _calls, _ = patched
    events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(runner.redpack, "grab_claimable",
                        lambda c, **k: (_ for _ in ()).throw(WSTimeoutError("boom")))

    def cb(name, status, detail=""):
        events.append((name, status, detail))
        if name == "guild":
            raise RuntimeError("callback bug")  # must be swallowed

    rep = run_device("dev", spend=False, progress=cb)

    assert any(n == "redpack" and s == "error" and "boom" in d
               for n, s, d in events)
    assert "redpack" in rep.errors
    assert "couple" in rep.tasks  # tasks after the raising callback still ran

# -*- coding: utf-8 -*-
from __future__ import annotations

from ws_token.arena_fight import (
    ArenaFightReport,
    DailyFoughtBlacklist,
    FightOutcome,
    resolve_b_cdp_port,
    run_daily_challenges,
)
from ws_token import arena as arena_mod


def test_resolve_b_prefers_calc_cdp():
    assert resolve_b_cdp_port(device_cdp=9226, calc_cdp=9240) == 9240
    assert resolve_b_cdp_port(device_cdp=9226, calc_cdp=None) == 9226
    assert resolve_b_cdp_port(device_cdp=None, calc_cdp=None) is None


def test_open_b_runtime_ephemeral(monkeypatch):
    from ws_token import arena_fight as af

    calls = {}

    def fake_launch(**kwargs):
        calls["kwargs"] = kwargs
        return ("pw", "browser", "page")

    monkeypatch.setattr(
        "battle_calc.ephemeral_b.launch_ephemeral_b", fake_launch
    )
    pw, browser, page, kind = af.open_b_runtime(prefer_ephemeral=True, headless=True)
    assert kind == "ephemeral"
    assert (pw, browser, page) == ("pw", "browser", "page")
    assert calls["kwargs"]["headless"] is True


def test_fight_once_happy(monkeypatch):
    from ws_token import arena_fight as af

    class FakeClient:
        pass

    monkeypatch.setattr(
        af.arena_mod,
        "fetch_info",
        lambda c: arena_mod.ArenaInfo(
            success=True,
            enemies=(arena_mod.ArenaEnemy(id=99, power=1, name="e"),),
        ),
    )
    monkeypatch.setattr(
        af.arena_mod,
        "start_combat",
        lambda c, eid: arena_mod.ArenaCombat(
            success=True, eid=eid, vid=7, seed=3, body=b"\x08\x00"
        ),
    )
    monkeypatch.setattr(
        af,
        "simulate_combat_body",
        lambda page, mode, body: {"ok": True, "wid": 100, "result": 0, "ms": 1.5},
    )
    monkeypatch.setattr(
        af.arena_mod,
        "report_result",
        lambda c, vid, wid: arena_mod.ArenaResult(
            success=True, is_win=1, my_score_change=10
        ),
    )
    out = af.fight_once(FakeClient(), page=object())
    assert out.ok and out.wid == 100 and out.is_win == 1


def test_run_daily_challenges_stops_on_fail(monkeypatch):
    from ws_token import arena_fight as af

    calls = {"n": 0}

    def once(client, page, enemy_id=None, blacklist=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return FightOutcome(ok=True, is_win=1, my_score_change=5)
        return FightOutcome(ok=False, error="boom")

    monkeypatch.setattr(af, "fight_once", once)
    monkeypatch.setattr(af, "enforce_gap", lambda last, gap: 0.0)
    report = run_daily_challenges(object(), object(), fights=3, gap_sec=7)
    assert report.fought == 1
    assert not report.success
    assert report.error == "boom"
    assert calls["n"] == 2


def test_report_as_dict():
    r = ArenaFightReport(success=True, fought=1, wins=1, fights=[
        FightOutcome(ok=True, wid=1, is_win=1)
    ])
    d = r.as_dict()
    assert d["success"] and d["fights"][0]["wid"] == 1


# ─── DailyFoughtBlacklist ────────────────────────────────────────────────────


def test_blacklist_add_contains(tmp_path, monkeypatch):
    bl = DailyFoughtBlacklist(device="dev1", state_dir=tmp_path)
    assert not bl.contains(99)
    bl.add(99)
    assert bl.contains(99)
    assert 99 in bl.as_set()
    assert bl.count() == 1


def test_blacklist_persists_and_loads(tmp_path):
    bl = DailyFoughtBlacklist(device="dev1", state_dir=tmp_path)
    bl.add(99)
    bl2 = DailyFoughtBlacklist(device="dev1", state_dir=tmp_path)
    assert bl2.contains(99)


def test_blacklist_old_date_is_ignored(tmp_path):
    bl = DailyFoughtBlacklist(device="dev1", state_dir=tmp_path)
    bl.add(99)
    import json

    (tmp_path / "dev1.json").write_text(
        json.dumps({"arena_fought": {"date": "2000-01-01", "eids": [1, 2]}}),
        encoding="utf-8",
    )
    bl2 = DailyFoughtBlacklist(device="dev1", state_dir=tmp_path)
    assert not bl2.contains(1) and not bl2.contains(2)
    assert not bl2.contains(99)


def test_blacklist_skip_persist_without_device(tmp_path):
    bl = DailyFoughtBlacklist(device=None, state_dir=tmp_path)
    bl.add(99)
    assert bl.contains(99)
    assert not (tmp_path / "None.json").exists()


# ─── runner 跨輪補打與每日上限 ───


def test_runner_arena_only_fights_remaining_to_target(monkeypatch):
    from ws_token import arena_fight as af
    from ws_token import runner

    seen = {}
    monkeypatch.setattr(af, "daily_fight_plan", lambda device, target: (6, 3))

    def fake_run_with_b(_client, **kwargs):
        seen.update(kwargs)
        return ArenaFightReport(success=True, fought=3, wins=2)

    monkeypatch.setattr(af, "run_with_b", fake_run_with_b)
    result = runner._run_arena(
        object(),
        arena_config={"enabled": True, "fights": 9, "b_mode": "ephemeral"},
        device="dev",
    )

    assert seen["fights"] == 3
    assert result["success"] is True
    assert result["fought_today"] == result["target"] == 9


def test_runner_arena_reaching_target_skips_b_page(monkeypatch):
    from ws_token import arena_fight as af
    from ws_token import runner

    monkeypatch.setattr(af, "daily_fight_plan", lambda device, target: (13, 0))
    monkeypatch.setattr(
        af,
        "run_with_b",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("已達標不應開啟 B 頁")
        ),
    )

    result = runner._run_arena(
        object(),
        arena_config={"enabled": True, "fights": 9},
        device="dev",
    )

    assert result == {
        "success": True,
        "fought": 0,
        "fought_today": 13,
        "target": 9,
        "already_done": True,
    }


# ─── fight_once 黑名單 ───────────────────────────────────────────────────────


def test_fight_once_all_blacklisted(monkeypatch):
    from ws_token import arena_fight as af

    monkeypatch.setattr(
        af.arena_mod,
        "fetch_info",
        lambda c: arena_mod.ArenaInfo(
            success=True,
            enemies=(arena_mod.ArenaEnemy(id=99, power=1, name="e"),),
        ),
    )
    bl = DailyFoughtBlacklist(device=None)
    bl.add(99)
    out = af.fight_once(object(), page=object(), blacklist=bl)
    assert not out.ok
    assert out.error == "all enemies blacklisted"


def test_fight_once_skips_blacklisted_picks_other(monkeypatch):
    from ws_token import arena_fight as af

    monkeypatch.setattr(
        af.arena_mod,
        "fetch_info",
        lambda c: arena_mod.ArenaInfo(
            success=True,
            enemies=(
                arena_mod.ArenaEnemy(id=1, power=5, name="strong"),
                arena_mod.ArenaEnemy(id=2, power=1, name="weak"),
            ),
        ),
    )
    monkeypatch.setattr(
        af.arena_mod,
        "start_combat",
        lambda c, eid: arena_mod.ArenaCombat(
            success=True, eid=eid, vid=7, seed=3, body=b"\x08\x00"
        ),
    )
    monkeypatch.setattr(
        af,
        "simulate_combat_body",
        lambda page, mode, body: {"ok": True, "wid": 100, "result": 0, "ms": 1.5},
    )
    monkeypatch.setattr(
        af.arena_mod,
        "report_result",
        lambda c, vid, wid: arena_mod.ArenaResult(success=True, is_win=1),
    )
    bl = DailyFoughtBlacklist(device=None)
    bl.add(2)
    out = af.fight_once(object(), page=object(), blacklist=bl)
    assert out.ok and out.eid == 1


def test_run_daily_challenges_refresh_once_on_all_blacklisted(monkeypatch):
    from ws_token import arena_fight as af

    calls = {"n": 0, "refresh": 0}

    def fake_fight(client, page, blacklist=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return FightOutcome(ok=False, error="all enemies blacklisted")
        return FightOutcome(ok=True, is_win=1, my_score_change=5)

    def fake_refresh(client, *, timeout=None):
        calls["refresh"] += 1
        return arena_mod.ArenaInfo(success=True)

    monkeypatch.setattr(af, "fight_once", fake_fight)
    monkeypatch.setattr(af.arena_mod, "refresh_info", fake_refresh)
    monkeypatch.setattr(af, "enforce_gap", lambda last, gap: 0.0)
    report = run_daily_challenges(
        object(), object(), fights=3, gap_sec=7, blacklist=DailyFoughtBlacklist(device=None)
    )
    assert report.fought == 3 and report.success
    assert calls["refresh"] == 1
    assert calls["n"] == 4


def test_run_daily_challenges_refresh_fail_aborts(monkeypatch):
    from ws_token import arena_fight as af

    def fake_fight(client, page, blacklist=None):
        return FightOutcome(ok=False, error="all enemies blacklisted")

    monkeypatch.setattr(af, "fight_once", fake_fight)
    monkeypatch.setattr(
        af.arena_mod, "refresh_info", lambda client, *, timeout=None: None
    )
    monkeypatch.setattr(af, "enforce_gap", lambda last, gap: 0.0)
    report = run_daily_challenges(
        object(), object(), fights=3, gap_sec=7, blacklist=DailyFoughtBlacklist(device=None)
    )
    assert report.fought == 0
    assert not report.success
    assert report.error == "refresh failed, all enemies blacklisted"

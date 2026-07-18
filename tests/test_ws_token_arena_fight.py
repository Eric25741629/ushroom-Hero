# -*- coding: utf-8 -*-
from __future__ import annotations

from ws_token.arena_fight import (
    ArenaFightReport,
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

    def once(client, page, enemy_id=None):
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

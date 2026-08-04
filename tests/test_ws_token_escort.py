"""賞金之路純 WS 協定與執行 gate 的單元測試。"""
from __future__ import annotations

import datetime

from ws_token import codec
from ws_token import escort
from ws_token import escort_fight


def test_build_start_and_result_bodies():
    assert codec.walk_dict(escort.build_battle_start_body(2, 123)) == {1: 2, 2: 123}
    assert codec.walk_dict(escort.build_battle_result_body(2, 123, 0)) == {
        1: 2, 2: 123, 3: 0,
    }


def test_parse_info_collects_repeated_monsters():
    monster = (
        codec.pb_uint(1, 101)
        + codec.pb_uint(2, 7)
        + codec.pb_uint(3, 8)
        + codec.pb_uint(4, 9)
        + codec.pb_uint(5, 10)
        + codec.pb_uint(6, 1111)
        + codec.pb_uint(7, 2222)
    )
    body = codec.pb_uint(1, 3) + codec.pb_uint(2, 123456)
    body += codec.pb_msg(6, monster) + codec.pb_msg(6, monster)
    info = escort.parse_info(escort.CMD_INFO, body)
    assert info.success is True
    assert info.robbing_count == 3
    assert [m.id for m in info.monsters] == [101]
    assert info.monsters[0].power == 1111


def test_parse_start_and_result():
    start_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 2)
        + codec.pb_uint(3, 101)
        + codec.pb_uint(4, 999)
    )
    started = escort.parse_battle_start(escort.CMD_BATTLE_START, start_body)
    assert started.success is True
    assert (started.type, started.target_id, started.seed) == (2, 101, 999)

    result_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 2)
        + codec.pb_uint(3, 101)
        + codec.pb_uint(4, 0)
        + codec.pb_uint(5, 90)
    )
    result = escort.parse_battle_result(escort.CMD_BATTLE_RESULT, result_body)
    assert result.success is True
    assert (result.target_id, result.result, result.energy) == (101, 0, 90)


def test_info_and_start_use_call_for():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            self.calls.append((cmd, body, expect_cmds, timeout))
            if cmd == escort.CMD_INFO:
                return escort.CMD_INFO, codec.pb_uint(1, 0)
            return escort.CMD_BATTLE_START, (
                codec.pb_uint(1, 0)
                + codec.pb_uint(2, 2)
                + codec.pb_uint(3, 101)
                + codec.pb_uint(4, 999)
            )

    client = FakeClient()
    assert escort.fetch_info(client).success is True
    assert escort.start_battle(client, 101).seed == 999
    assert [call[0] for call in client.calls] == [escort.CMD_INFO, escort.CMD_BATTLE_START]


def test_run_with_b_does_not_touch_ws_outside_window(monkeypatch):
    monkeypatch.setattr(escort_fight, "in_window", lambda: False)
    client = object()
    report = escort_fight.run_with_b(client, device="dev1")
    assert report.success is True
    assert report.skipped == "outside weekend window"


def test_run_with_b_completes_ws_start_sim_result(monkeypatch):
    monster = escort.EscortMonster(id=101, power=10)
    calls = []
    recorded = []

    monkeypatch.setattr(escort_fight, "in_window", lambda: True)
    monkeypatch.setattr(escort_fight, "is_due", lambda device: True)
    monkeypatch.setattr(
        escort_fight.escort_mod,
        "fetch_info",
        lambda client: escort.EscortInfo(success=True, monsters=(monster,)),
    )
    monkeypatch.setattr(
        escort_fight.escort_mod,
        "start_battle",
        lambda client, target_id: (
            calls.append(("start", target_id))
            or escort.EscortBattleStart(
                success=True, target_id=target_id, seed=9, body=b"start"
            )
        ),
    )
    monkeypatch.setattr(
        escort_fight,
        "simulate_combat_body",
        lambda page, mode, body: {"ok": True, "result": 0, "ms": 1.0},
    )
    monkeypatch.setattr(
        escort_fight.escort_mod,
        "report_result",
        lambda client, target_id, result: (
            calls.append(("result", target_id, result))
            or escort.EscortBattleResult(
                success=True, target_id=target_id, result=result
            )
        ),
    )
    monkeypatch.setattr(
        escort_fight,
        "open_b_runtime",
        lambda **kwargs: ("pw", "browser", "page", "ephemeral"),
    )
    monkeypatch.setattr(escort_fight, "close_b_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        escort_fight.json_manager,
        "time_recording",
        lambda device, name="": recorded.append((device, name)),
    )

    report = escort_fight.run_with_b(object(), device="dev1", gap_sec=0)
    assert report.success is True
    assert report.fought == 1 and report.wins == 1
    assert calls == [("start", 101), ("result", 101, 0)]
    assert recorded == [("dev1", escort_fight.RECORD_KEY)]


def test_in_window_uses_weekend_after_11():
    sat = datetime.datetime(2026, 7, 11, 11, 0)
    fri = datetime.datetime(2026, 7, 10, 11, 0)
    assert escort_fight.in_window(sat) is True
    assert escort_fight.in_window(fri) is False

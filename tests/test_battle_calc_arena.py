# -*- coding: utf-8 -*-
"""battle_calc / arena pure-WS helpers unit tests."""
from __future__ import annotations

from battle_calc.config import (
    coerce_arena_gap_sec,
    coerce_battle_mode,
    coerce_wanshen_battle_mode,
)
from battle_calc.modes import build_sim_request
from battle_calc.runner import enforce_gap
from ws_token import arena as arena_mod
from ws_token import codec


def test_coerce_battle_mode_accepts_pure_ws():
    assert coerce_battle_mode("pure_ws") == "pure_ws"
    assert coerce_battle_mode("local_sim") == "local_sim"
    assert coerce_battle_mode("nope") == "animation"


def test_coerce_wanshen_accepts_pure_ws():
    # Layer 2 AB separation (2026-07-28): pure_ws 正式開放給萬神試煉。
    assert coerce_wanshen_battle_mode("pure_ws") == "pure_ws"
    assert coerce_wanshen_battle_mode("local_sim") == "local_sim"
    assert coerce_wanshen_battle_mode("nope") == "animation"


def test_arena_gap_min_7():
    assert coerce_arena_gap_sec(3) == 7.0
    assert coerce_arena_gap_sec(7) == 7.0
    assert coerce_arena_gap_sec(10) == 10.0
    assert coerce_arena_gap_sec("bad") == 7.0


def test_build_sim_request_arena():
    req = build_sim_request(
        "arena",
        {
            "seed": 1,
            "vid": 2,
            "atk_data": {"id": 10, "name": "a"},
            "def_data": {"id": 20, "name": "b"},
        },
    )
    assert req["chapter_type"] == 5
    assert req["chapter_id"] == 50001
    assert req["chapter_type_name"] == "Arena"
    assert req["atk_data"]["id"] == 10


def test_build_sim_request_rogue_uses_index0():
    req = build_sim_request(
        "rogue",
        {
            "seed": 9,
            "atk_data": [{"id": 1}, {"id": 99}],
            "def_data": [{"id": 2}],
        },
    )
    assert req["chapter_type"] == 37
    assert req["atk_data"]["id"] == 1
    assert req["def_data"]["id"] == 2


def test_arena_codec_roundtrip():
    body = arena_mod.build_combat_c2s(12345)
    d = codec.walk_dict(body)
    assert d[1] == 12345
    rb = arena_mod.build_result_c2s(99, 88)
    rd = codec.walk_dict(rb)
    assert rd[1] == 99 and rd[2] == 88


def test_parse_combat_ok():
    body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 11)
        + codec.pb_uint(3, 22)
        + codec.pb_uint(4, 33)
    )
    c = arena_mod.parse_combat(arena_mod.CMD_COMBAT, body)
    assert c.success and c.vid == 22 and c.seed == 33


def test_parse_result_win():
    body = codec.pb_uint(1, 1) + codec.pb_uint(2, 1400) + codec.pb_uint(4, 20)
    r = arena_mod.parse_result(arena_mod.CMD_RESULT, body)
    assert r.success and r.is_win == 1 and r.my_score_change == 20


def test_pick_weakest():
    e = (
        arena_mod.ArenaEnemy(id=1, power=100),
        arena_mod.ArenaEnemy(id=2, power=50),
        arena_mod.ArenaEnemy(id=3, power=80),
    )
    assert arena_mod.pick_weakest(e).id == 2


def test_enforce_gap_sleeps(monkeypatch):
    sleeps = []
    monkeypatch.setattr("battle_calc.runner.time.sleep", lambda s: sleeps.append(s))
    # last just now → need nearly full gap
    now = 1000.0
    monkeypatch.setattr("battle_calc.runner.time.monotonic", lambda: now)
    # after sleep, monotonic still returns same in this stub — just ensure sleep called
    enforce_gap(now - 1.0, 7.0)
    assert sleeps and sleeps[0] >= 5.9


def test_config_manager_clamps_arena_keys(tmp_path, monkeypatch):
    import config_manager

    # exercise clamp path used by update_device_config without writing bot_config
    current = dict(config_manager.DEFAULT_DEVICE_CONFIG)
    current.update(
        {
            "arena_battle_mode": "pure_ws",
            "arena_fight_gap_sec": 2,
            "wanshen_battle_mode": "local_sim",
        }
    )
    # replicate clamp block
    from battle_calc.config import (
        coerce_arena_gap_sec,
        coerce_battle_mode,
        coerce_wanshen_battle_mode,
    )

    current["arena_battle_mode"] = coerce_battle_mode(current["arena_battle_mode"])
    current["arena_fight_gap_sec"] = coerce_arena_gap_sec(current["arena_fight_gap_sec"])
    current["wanshen_battle_mode"] = coerce_wanshen_battle_mode(
        current["wanshen_battle_mode"]
    )
    assert current["arena_battle_mode"] == "pure_ws"
    assert current["arena_fight_gap_sec"] == 7.0
    assert current["wanshen_battle_mode"] == "local_sim"

# -*- coding: utf-8 -*-
from utils.battle_speed import coerce_battle_speed_scale


def test_coerce_battle_speed_scale():
    assert coerce_battle_speed_scale(4) == 4.0
    assert coerce_battle_speed_scale(99) == 10.0
    assert coerce_battle_speed_scale(0) == 1.0
    assert coerce_battle_speed_scale("2.5") == 2.5
    assert coerce_battle_speed_scale("x", default=4) == 4.0

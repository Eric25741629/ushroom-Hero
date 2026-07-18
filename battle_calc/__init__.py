# -*- coding: utf-8 -*-
"""A 打 / B 算：競技場 / 萬神 BattleMainServer 模擬。"""
from .config import (
    ARENA_MODES,
    DEFAULT_ARENA_GAP_SEC,
    MIN_ARENA_GAP_SEC,
    coerce_arena_gap_sec,
    coerce_battle_mode,
    get_battle_calc_global,
)
from .page_hooks import install_hooks, set_block_result, take_combat, send_result
from .simulate import simulate_on_page, simulate_remote

__all__ = [
    "ARENA_MODES",
    "DEFAULT_ARENA_GAP_SEC",
    "MIN_ARENA_GAP_SEC",
    "coerce_arena_gap_sec",
    "coerce_battle_mode",
    "get_battle_calc_global",
    "install_hooks",
    "set_block_result",
    "take_combat",
    "send_result",
    "simulate_on_page",
    "simulate_remote",
]

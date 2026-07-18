# -*- coding: utf-8 -*-
"""各玩法 BattleMainServer 參數（對齊官方 Control）。"""
from __future__ import annotations

from typing import Any, Dict

# chapterType 數值（client ChapterType enum）
CHAPTER = {
    "arena": 5,
    "rogue": 37,
}

CHAPTER_ID = {
    "arena": 50001,
    "rogue": 50001,
}

# JS 內用 ChapterType 屬性名
CHAPTER_TYPE_NAME = {
    "arena": "Arena",
    "rogue": "Rogue",
}


def normalize_role_blob(mode: str, side: Any) -> Any:
    """arena: 單物件；rogue: 陣列取 [0]。"""
    if mode == "rogue":
        if isinstance(side, list):
            return side[0] if side else None
        return side
    return side


def build_sim_request(mode: str, combat: Dict[str, Any]) -> Dict[str, Any]:
    if mode not in CHAPTER:
        raise ValueError(f"unknown battle mode: {mode}")
    atk = normalize_role_blob(mode, combat.get("atk_data"))
    def_ = normalize_role_blob(mode, combat.get("def_data"))
    return {
        "mode": mode,
        "seed": combat.get("seed"),
        "vid": combat.get("vid"),
        "eid": combat.get("eid"),
        "chapter_type": CHAPTER[mode],
        "chapter_id": CHAPTER_ID[mode],
        "chapter_type_name": CHAPTER_TYPE_NAME[mode],
        "atk_data": atk,
        "def_data": def_,
    }

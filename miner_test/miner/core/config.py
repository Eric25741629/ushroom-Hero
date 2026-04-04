"""集中管理挖礦模組會共用的常數設定與表格。

透過統一的設定檔可以避免魔法數字散落在各個檔案，
也讓日後調整遊戲座標或成本獎勵時只需要修改此處。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

# =========================
# 路徑設定
# =========================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CNN_MODEL = os.path.join(os.path.dirname(__file__), "..", "models", "checkpoints", "best.pth")

# =========================
# 盤面與顏色檢查設定
# =========================
GRID_CFG = {
    "H": 7,
    "W": 6,
    "x0": 6,
    "y0": 227,
    "x1": 535,
    "y1": 852,
}

# 盤面上用來判定是否被 UI 遮擋的取樣點（BGR）
expected_points: List[Tuple[Tuple[int, int], Tuple[int, int, int]]] = [
    ((382, 79), (17, 20, 28)),
    ((307, 151), (19, 25, 48)),
    ((397, 180), (20, 16, 22)),
    ((315, 72), (21, 20, 24)),
    ((350, 117), (22, 24, 42)),
    ((429, 138), (33, 36, 64)),
    ((289, 189), (14, 21, 48)),
    ((132, 56), (11, 18, 38)),
    ((500, 216), (35, 42, 51)),
]
MIN_REQUIRED = 5  # 至少需要匹配的顏色點數量
TOL = 3  # 顏色容忍度：逐通道允許 ±TOL

# =========================
# 成本與獎勵表
# =========================
COST_TABLE: Dict[str, Optional[int]] = {
    # pass-through / 等價：不消耗鏟子
    "empty": 0,
    "void": 0,
    "pit": 0,        # 與需求：pit 視為等同 empty
    "dug_pit": 0,    # 已挖過
    # 可直接鏟：
    "dirt": 1,
    "unreachable_dirt": 1,
    "one_hit_rock": 1,
    "rock": 2,
    "unreachable_rock": 2,
    # 礦：reachable_pit 目前仍需 1 次鏟子，unreachable_pit 不能直接進入（由工具或打通後再算）
    "reachable_pit": 1,
    # unreachable_pit 不可直接鏟，用工具才有意義；讓 enter_cost 回傳 None（透過特別處理）
    # unreachable_empty 保留 0 以便路徑成本計算時不增額，但仍需通達判斷
    "unreachable_pit": None,
    "unreachable_empty": 0,
}

REWARD_TABLE: Dict[str, int] = {
    # pit 無收益；僅 reachable/unreachable_pit 有獎勵
    "pit": 0,
    "reachable_pit": 50,
    "unreachable_pit": 50,
    "dug_pit": 0,
}

MINE_LABELS = set(REWARD_TABLE.keys())

DEFAULT_CLASSES: Sequence[str] = (
    "dirt",
    "dug_pit",
    "empty",
    "one_hit_rock",
    "reachable_pit",
    "rock",
    "unreachable_dirt",
    "unreachable_pit",
    "unreachable_rock",
    "unreachable_empty",
)

# =========================
# 點擊次數表
# =========================
HIT_TABLE = {
    "empty": 0,
    "dirt": 1,
    "unreachable_dirt": 1,
    "rock": 2,
    "unreachable_rock": 2,
    "one_hit_rock": 1,
    "pit": 1,
    "reachable_pit": 1,
    "unreachable_pit": 1,
    "dug_pit": 0,
    "unreachable_empty": 0, # 本身是空地，不需要點擊
}

# =========================
# OCR 伺服器
# =========================
OCR_SERVER_URL = "http://127.0.0.1:5001"

__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_CNN_MODEL",
    "GRID_CFG",
    "expected_points",
    "MIN_REQUIRED",
    "TOL",
    "COST_TABLE",
    "REWARD_TABLE",
    "MINE_LABELS",
    "DEFAULT_CLASSES",
    "HIT_TABLE",
    "OCR_SERVER_URL",
]

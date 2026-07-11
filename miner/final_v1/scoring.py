"""final_v1 objective: one shared scoring function for ordering and terminal ranking.

優先級（高到低）：完成 cluster > 收礦 > 鎬/道具成本 > 漏礦懲罰 > 半挖懲罰 > 低權重下潛。
炸彈與鑽頭同基礎成本（取得機率相同，庫存差是歷史演算法偏好造成）。
"""
from __future__ import annotations

from collections import deque
from typing import FrozenSet, List, Sequence, Tuple

from miner.v3.board import is_pit
from miner.final_v1.types import ScoreBreakdown, SearchUsage

Coordinate = Tuple[int, int]
PIT_VALUE = 10.0
CLUSTER_COMPLETION_MULTIPLIER = 2.0
SHOVEL_COST = 1.0
ITEM_COST = {"bomb": 3.0, "drill": 3.0}
LOST_PIT_PENALTY = 40.0
UNFINISHED_CLUSTER_PENALTY = 4.0
DESCENT_BONUS = 0.5
PATH_BONUS = 0.25


def pit_clusters(board: Sequence[Sequence[str]]) -> List[FrozenSet[Coordinate]]:
    pending = {(r, c) for r, row in enumerate(board) for c, cell in enumerate(row) if is_pit(cell)}
    groups: List[FrozenSet[Coordinate]] = []
    while pending:
        seed = pending.pop()
        group = {seed}
        queue = deque([seed])
        while queue:
            r, c = queue.popleft()
            for pos in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if pos in pending:
                    pending.remove(pos)
                    group.add(pos)
                    queue.append(pos)
        groups.append(frozenset(group))
    return groups


def evaluate_state(
    original_board,
    board,
    usage: SearchUsage,
    *,
    scrolled: bool = False,
    descent_rows: int = 0,
    opened_path_cells: int = 0,
) -> ScoreBreakdown:
    clusters = pit_clusters(original_board)
    collected = 0
    completed_bonus = 0.0
    unfinished = 0
    for cluster in clusters:
        remaining = sum(1 for r, c in cluster if r < len(board) and is_pit(board[r][c]))
        collected += len(cluster) - remaining
        if remaining == 0:
            completed_bonus += len(cluster) * max(0, len(cluster) - 1) * CLUSTER_COMPLETION_MULTIPLIER
        elif remaining < len(cluster):
            unfinished += 1
    row_zero_lost = sum(1 for cell in original_board[0] if is_pit(cell)) if scrolled else 0
    return ScoreBreakdown(
        cluster_gain=completed_bonus,
        pit_gain=collected * PIT_VALUE,
        shovel_cost=usage.shovels * SHOVEL_COST,
        item_cost=(usage.bombs + usage.drills) * ITEM_COST["bomb"],
        lost_pit_penalty=row_zero_lost * LOST_PIT_PENALTY,
        unfinished_cluster_penalty=unfinished * UNFINISHED_CLUSTER_PENALTY,
        descent_bonus=descent_rows * DESCENT_BONUS,
        path_bonus=opened_path_cells * PATH_BONUS,
    )

"""final_v1 objective: one shared scoring function for ordering and terminal ranking.

優先級（高到低）：完成 cluster > 收礦 > 鎬/道具成本 > 漏礦懲罰 > 半挖懲罰 > 位能導向 > 低權重下潛。
炸彈與鑽頭同基礎成本（取得機率相同，庫存差是歷史演算法偏好造成）。

位能場（pit_potential）：從所有未採集礦坑做 terrain-cost Dijkstra（生產環境
pit_directed_next 的同款思路，內建進 planner 核心）；無礦時以盤面底緣為虛擬
礦源，場梯度自然指向「最低成本下潛」。挖掘沿位能下降方向前進時給 PIT_PULL
獎勵，讓 beam search 朝礦（或朝下）走而不是靠固定 path bonus 亂逛。
"""
from __future__ import annotations

import heapq
from collections import deque
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from miner.v3.actions import dig_cost
from miner.v3.board import is_pit
from miner.final_v1.types import ScoreBreakdown, SearchUsage

Coordinate = Tuple[int, int]
PIT_VALUE = 10.0
CLUSTER_COMPLETION_MULTIPLIER = 2.0
SHOVEL_COST = 1.0
# 3.0 為掃描後最優（4.0/5.0 全 regime 總產出掉到 v1 之下）；道具會從完成
# cluster 回補，過度節省 = 白白少挖
ITEM_COST = {"bomb": 3.0, "drill": 3.0}
# 略高於單顆礦值：優先收礦，但不為守一顆 row0 礦犧牲 4+ 步進度
# （40 時實測 scarce 盤 depth 172 vs v1 220，過度保護反而總產出更低）
LOST_PIT_PENALTY = 12.0
UNFINISHED_CLUSTER_PENALTY = 4.0
# 每捲動一列的期望收益：3.6% 密度 x 6 col x 礦值 10 ≈ 2.16，取 2.0。
# 0.5 時搜尋嚴重低估下潛、寧可橫向啃礦（trace: horiz 138 vs v1 32）
DESCENT_BONUS = 2.0
PATH_BONUS = 0.25
# 真實獎勵是「整簇挖完才發」：半挖格只給部分信用維持梯度，完成時跳到全值。
# 1.0 時規劃器會橫向啃單格就走（拿不到完成獎勵、又犧牲下潛）
PARTIAL_PIT_CREDIT = 0.3
PIT_PULL = 0.8  # 每縮短 1 單位挖掘成本距離的獎勵；< SHOVEL_COST，只影響排序不鼓勵空挖


def pit_potential(board: Sequence[Sequence[str]]) -> Dict[Coordinate, float]:
    """每格到最近未採集礦坑的最便宜挖掘成本（multi-source Dijkstra）。

    進入一格的成本 = 該格 dig_cost（air 0 / dirt 與 pit 1 / rock 2；
    unreachable_* 視同其本體地形）。無礦盤面以底緣為虛擬源，
    讓場梯度指向最低成本的下潛路線。
    """
    rows = len(board)
    cols = len(board[0]) if rows else 0
    dist: Dict[Coordinate, float] = {}
    heap: List[Tuple[float, int, int]] = []
    pits = [(r, c) for r in range(rows) for c in range(cols) if is_pit(board[r][c])]
    if pits:
        for r, c in pits:
            dist[(r, c)] = 0.0
            heapq.heappush(heap, (0.0, r, c))
    elif rows:
        for c in range(cols):
            d = float(dig_cost(board[rows - 1][c]))
            dist[(rows - 1, c)] = d
            heapq.heappush(heap, (d, rows - 1, c))
    while heap:
        d, r, c = heapq.heappop(heap)
        if d > dist.get((r, c), float("inf")):
            continue
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            nd = d + float(dig_cost(board[nr][nc]))
            if nd < dist.get((nr, nc), float("inf")):
                dist[(nr, nc)] = nd
                heapq.heappush(heap, (nd, nr, nc))
    return dist


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
    clusters: List[FrozenSet[Coordinate]] = None,
    lost_pits: Optional[int] = None,
    pull_progress: float = 0.0,
) -> ScoreBreakdown:
    if clusters is None:
        clusters = pit_clusters(original_board)
    collected_value = 0.0
    completed_bonus = 0.0
    unfinished = 0
    for cluster in clusters:
        remaining = sum(1 for r, c in cluster if r < len(board) and is_pit(board[r][c]))
        dug = len(cluster) - remaining
        if remaining == 0:
            collected_value += len(cluster) * PIT_VALUE
            completed_bonus += len(cluster) * max(0, len(cluster) - 1) * CLUSTER_COMPLETION_MULTIPLIER
        elif dug:
            collected_value += dug * PIT_VALUE * PARTIAL_PIT_CREDIT
            unfinished += 1
    if lost_pits is None:
        # 舊介面相容：沒有精確損失數時以原始盤 row0 估計
        row_zero_lost = sum(1 for cell in original_board[0] if is_pit(cell)) if scrolled else 0
    else:
        # 精確歸因：捲動當下 row0 仍未採集的礦才算損失（先收礦再捲動 = 0）
        row_zero_lost = int(lost_pits)
    return ScoreBreakdown(
        cluster_gain=completed_bonus,
        pit_gain=collected_value,
        shovel_cost=usage.shovels * SHOVEL_COST,
        item_cost=(usage.bombs + usage.drills) * ITEM_COST["bomb"],
        lost_pit_penalty=row_zero_lost * LOST_PIT_PENALTY,
        unfinished_cluster_penalty=unfinished * UNFINISHED_CLUSTER_PENALTY,
        descent_bonus=descent_rows * DESCENT_BONUS,
        path_bonus=opened_path_cells * PATH_BONUS,
        pull_bonus=max(0.0, float(pull_progress)) * PIT_PULL,
    )

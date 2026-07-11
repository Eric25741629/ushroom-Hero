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
from miner.v3.board import is_pit, normalize_label
from miner.final_v1.types import ScoreBreakdown, SearchUsage

Coordinate = Tuple[int, int]
PIT_VALUE = 10.0
CLUSTER_COMPLETION_MULTIPLIER = 2.0
SHOVEL_COST = 1.0
# 道具搜尋內部「影子價」，依 exec_profile 分開（分級成本已廢除——「命中幾礦」不等於
# 「對 KPI 的邊際貢獻」，deep bridge 收益靠 action_cost/branch quota 呈現）。KPI 對外
# 兌換率仍是 3，這兩個值只是搜尋內部排序用，經 40 tune-seed 掃描定案：
#   plan（ADB 整批路徑）3.0 最優：223.4 pits / 0.202 eff vs v1 91.6 / 0.185
#   step（WS 每步重規劃取第一步）3.6 才產出+效率雙贏：80.6 / 0.205 vs v1 65.7 / 0.185
ITEM_COST_PLAN = 3.0 * SHOVEL_COST
ITEM_COST_STEP = 3.6 * SHOVEL_COST
# 對外預設 alias = plan 值（維持模組常數供掃描 monkeypatch / 舊呼叫端相容）
ITEM_COST = ITEM_COST_PLAN
# KPI 對齊：完成「下一簇」的動作數下界懲罰係數（單位 pit/action，J 內乘 PIT_VALUE）。
# step profile（WS 每步重規劃、只取第一步）用 RHO_ACTION_STEP 讓「離下一簇更近」的
# 分支勝出；plan/ADB profile（整批路徑）用 RHO_ACTION_PLAN=0 不改既有排名。
RHO_ACTION_STEP = 0.15
RHO_ACTION_PLAN = 0.0


def item_use_cost(item: str, unit_cost: float = ITEM_COST) -> float:
    """單次道具的搜尋內部影子價（bomb/drill 同價）；unit_cost 依 exec_profile 選定
    （plan=ITEM_COST_PLAN 3.0 / step=ITEM_COST_STEP 3.6）。對外 KPI 兌換率仍是 3。"""
    return unit_cost
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


def pit_potential(
    board: Sequence[Sequence[str]],
    clusters: Optional[List[FrozenSet[Coordinate]]] = None,
) -> Tuple[Dict[Coordinate, float], Dict[Coordinate, int]]:
    """每格到最近未採集礦坑的最便宜挖掘成本 + 該最近礦源所屬 cluster id
    （multi-source Dijkstra 帶 source id，一次掃描同時得距離與歸屬）。

    進入一格的成本 = 該格 dig_cost（air 0 / dirt 與 pit 1 / rock 2；
    unreachable_* 視同其本體地形）。無礦盤面以底緣為虛擬源（cluster id = -1），
    讓場梯度指向最低成本的下潛路線。回傳 (dist, source)：source[cell] 為最近
    礦源所屬 cluster 於 clusters 列表的索引，虛擬底緣源為 -1。
    """
    rows = len(board)
    cols = len(board[0]) if rows else 0
    dist: Dict[Coordinate, float] = {}
    source: Dict[Coordinate, int] = {}
    heap: List[Tuple[float, int, int]] = []
    if clusters is None:
        clusters = pit_clusters(board)
    pit_to_cluster: Dict[Coordinate, int] = {}
    for idx, cluster in enumerate(clusters):
        for r, c in cluster:
            if 0 <= r < rows and 0 <= c < cols and is_pit(board[r][c]):
                pit_to_cluster[(r, c)] = idx
    if pit_to_cluster:
        for (r, c), cid in pit_to_cluster.items():
            dist[(r, c)] = 0.0
            source[(r, c)] = cid
            heapq.heappush(heap, (0.0, r, c))
    elif rows:
        for c in range(cols):
            d = float(dig_cost(board[rows - 1][c]))
            dist[(rows - 1, c)] = d
            source[(rows - 1, c)] = -1
            heapq.heappush(heap, (d, rows - 1, c))
    while heap:
        d, r, c = heapq.heappop(heap)
        if d > dist.get((r, c), float("inf")):
            continue
        cid = source[(r, c)]
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            nd = d + float(dig_cost(board[nr][nc]))
            if nd < dist.get((nr, nc), float("inf")):
                dist[(nr, nc)] = nd
                source[(nr, nc)] = cid
                heapq.heappush(heap, (nd, nr, nc))
    return dist, source


def pit_clusters(board: Sequence[Sequence[str]]) -> List[FrozenSet[Coordinate]]:
    """在「未採集礦坑 ∪ 已挖礦坑(dug_pit)」的聯集上做連通分量。

    保存 cluster 身分：半挖簇不會因中間格已變 dug_pit 而被重分群 → 不會誤發
    完成獎勵、完成判定與 bonus 尺寸以整簇原始尺寸計。盤面若無 dug 標籤則聯集
    退回純礦坑，行為與舊版一致（向下相容）。
    """
    def _is_cluster_cell(cell: str) -> bool:
        return is_pit(cell) or normalize_label(cell) == "dug_pit"

    pending = {
        (r, c)
        for r, row in enumerate(board)
        for c, cell in enumerate(row)
        if _is_cluster_cell(cell)
    }
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
    best_dist: float = float("inf"),
    best_cluster: int = -1,
    rho_action: float = 0.0,
) -> ScoreBreakdown:
    if clusters is None:
        clusters = pit_clusters(original_board)
    collected_value = 0.0
    completed_bonus = 0.0
    unfinished = 0
    nearest_remaining = 0
    for idx, cluster in enumerate(clusters):
        remaining = sum(1 for r, c in cluster if r < len(board) and is_pit(board[r][c]))
        dug = len(cluster) - remaining
        if idx == best_cluster:
            nearest_remaining = remaining
        if remaining == 0:
            collected_value += len(cluster) * PIT_VALUE
            completed_bonus += len(cluster) * max(0, len(cluster) - 1) * CLUSTER_COMPLETION_MULTIPLIER
        elif dug:
            collected_value += dug * PIT_VALUE * PARTIAL_PIT_CREDIT
            unfinished += 1
    # KPI 對齊：完成「下一簇」的動作數下界 = 前緣到最近礦源的挖掘距離 + 該簇剩餘未挖格。
    # 只在 step profile（rho>0）生效；離下一簇越近、剩越少 → 懲罰越小 → 分數越高，
    # 讓深層才回收的 bridge 分支在第一層就有梯度可循（codex 指出扁平 per-action 不改排名）。
    action_cost = 0.0
    if rho_action and best_dist != float("inf"):
        action_cost = rho_action * PIT_VALUE * (best_dist + nearest_remaining)
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
        item_cost=usage.item_cost_units,
        lost_pit_penalty=row_zero_lost * LOST_PIT_PENALTY,
        unfinished_cluster_penalty=unfinished * UNFINISHED_CLUSTER_PENALTY,
        descent_bonus=descent_rows * DESCENT_BONUS,
        path_bonus=opened_path_cells * PATH_BONUS,
        pull_bonus=max(0.0, float(pull_progress)) * PIT_PULL,
        action_cost=action_cost,
    )

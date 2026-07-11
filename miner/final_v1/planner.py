"""final_v1 bounded beam search.

重用 v3 board/actions 力學與 core.mechanics footprint；輸出完整最佳路徑
（至捲動點為止）：ADB 後端整批執行、攤平每步截圖+分類成本（與 v1 同等
待遇），WS supervised 迴圈本來就每步重規劃、只取第一步。捲動點結束深搜
（不猜下一個 viewport）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Collection, Dict, List, Optional, Set, Tuple

from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
from miner.v3.actions import apply_dig, dig_cost
from miner.v3.board import (
    floor7_open,
    is_frontier_diggable,
    is_reachable_air,
    normalize_board,
    open_cell,
    promote_after_dig,
)
from miner.final_v1.scoring import evaluate_state, pit_clusters, pit_potential
from miner.final_v1.types import ActionKey, PlannerAction, PlannerConfig, SearchUsage

_INF = float("inf")


@dataclass(frozen=True)
class _State:
    board: Tuple[Tuple[str, ...], ...]
    pickaxes: float
    bombs: int
    drills: int
    usage: SearchUsage
    path: Tuple[PlannerAction, ...]
    scrolled: bool = False
    opened_path_cells: int = 0
    lost_pits: int = 0          # 捲動當下 row0 仍未採集的礦（精確歸因，可跨多次捲動累計）
    best_dist: float = _INF     # 已挖開格中最小的位能（距最近礦坑的挖掘成本）
    # 逐步成本（dig=當下格材質成本、道具=1.0）：emit 多步 plan 時直接取用，
    # 不能用初始盤面回推第 N 步的 dig_cost（材質可能已被前面步驟改變）
    path_costs: Tuple[float, ...] = tuple()


def _candidate_actions(board, pickaxes, bombs, drills, visible_rows) -> List[PlannerAction]:
    rows = min(int(visible_rows), len(board))
    actions: List[PlannerAction] = []
    for r in range(rows):
        for c in range(len(board[r])):
            cell = board[r][c]
            cost = dig_cost(cell)
            if 0 < cost <= pickaxes and is_frontier_diggable(board, r, c):
                actions.append(PlannerAction("dig", "pickaxe", r, c))
            if is_reachable_air(cell):
                if bombs > 0:
                    actions.append(PlannerAction("use", "bomb", r, c))
                if drills > 0:
                    actions.append(PlannerAction("use", "drill", r, c))
    return actions


def _affected(action: PlannerAction, board, visible_rows) -> Set[Tuple[int, int]]:
    rows, cols = len(board), len(board[0])
    if action.item == "bomb":
        # 炸彈可計入已知畫面外收益；鑽頭/鎬只算畫面內
        return set(get_bomb_affected_cells(action.row, action.col, rows, cols))
    if action.item == "drill":
        return {
            pos for pos in get_drill_affected_cells(action.row, action.col, rows, cols)
            if pos[0] < visible_rows
        }
    return {(action.row, action.col)}


def _lost_row0_pits(work: List[List[str]]) -> int:
    """捲動當下 row0 仍未採集的礦坑數（is_pit 標籤 = 尚未挖開）。"""
    from miner.v3.board import is_pit
    return sum(1 for cell in work[0] if is_pit(cell))


def _apply(state: _State, action: PlannerAction, visible_rows: int,
           dist: Optional[Dict[Tuple[int, int], float]] = None) -> Optional[_State]:
    work = [list(row) for row in state.board]
    visible = work[:visible_rows]
    before_scroll_open = floor7_open(visible)
    if action.kind == "dig":
        cost = float(dig_cost(work[action.row][action.col]))
        if cost <= 0 or cost > state.pickaxes:
            return None
        apply_dig(work, (action.row, action.col))
        scrolled = (not before_scroll_open) and floor7_open(work[:visible_rows])
        lost = state.lost_pits + (_lost_row0_pits(work) if scrolled else 0)
        best_dist = state.best_dist
        if dist is not None:
            best_dist = min(best_dist, dist.get((action.row, action.col), _INF))
        return _State(
            tuple(tuple(row) for row in work), state.pickaxes - cost, state.bombs, state.drills,
            SearchUsage(state.usage.shovels + cost, state.usage.bombs, state.usage.drills),
            state.path + (action,), state.scrolled or scrolled, state.opened_path_cells + 1,
            lost, best_dist, state.path_costs + (cost,),
        )
    affected = _affected(action, work, visible_rows)
    changed = [(r, c) for r, c in affected if not is_reachable_air(work[r][c])]
    if not changed:
        return None  # no-op item branch is never free progress
    for r, c in changed:
        open_cell(work, r, c)
    promote_after_dig(work, changed)
    scrolled = (not before_scroll_open) and floor7_open(work[:visible_rows])
    lost = state.lost_pits + (_lost_row0_pits(work) if scrolled else 0)
    best_dist = state.best_dist
    if dist is not None:
        for pos in changed:
            best_dist = min(best_dist, dist.get(pos, _INF))
    bomb_delta = 1 if action.item == "bomb" else 0
    drill_delta = 1 if action.item == "drill" else 0
    return _State(
        tuple(tuple(row) for row in work), state.pickaxes,
        state.bombs - bomb_delta, state.drills - drill_delta,
        SearchUsage(state.usage.shovels, state.usage.bombs + bomb_delta, state.usage.drills + drill_delta),
        # path bonus 以「動作」計，不以「格」計：炸彈開 8 格空地不是 8 倍路徑價值，
        # 每格計會讓道具靠 path+partial credit 刷分（實測 scarce 盤道具 44 vs v1 27 卻完成更少）
        state.path + (action,), state.scrolled or scrolled, state.opened_path_cells + 1,
        lost, best_dist, state.path_costs + (1.0,),
    )


def plan_final_v1(
    board,
    shovels,
    items,
    *,
    visible_rows: int = 7,
    known_pits=None,
    valid_targets: Optional[Collection[ActionKey]] = None,
    time_budget_ms: float = 250.0,
) -> Dict:
    started = time.perf_counter()
    config = PlannerConfig(time_budget_ms=float(time_budget_ms))
    work = normalize_board(board)
    # 大盤（21 列已知地形）自適應縮 beam：單子節點展開成本 ~3x，
    # 收窄寬度把 p99 壓回預算內，深度不減
    if len(work) > 10:
        config = PlannerConfig(
            max_depth=config.max_depth,
            beam_width=14,
            branch_width=8,
            time_budget_ms=config.time_budget_ms,
        )
    for row, col in known_pits or ():
        r, c = int(row), int(col)
        if (
            0 <= r < len(work)
            and 0 <= c < len(work[r])
            and work[r][c] in {"dirt", "rock", "unreachable_dirt", "unreachable_rock"}
        ):
            work[r][c] = "unreachable_pit"
    # 15% 安全邊際：deadline 檢查粒度 + GC/OS 抖動實測可超時 ~30-80ms，
    # 提早收手讓回報的 max 仍守住呼叫端的 time_budget_ms
    deadline = started + (config.time_budget_ms * 0.85) / 1000.0
    valid = set(valid_targets) if valid_targets is not None else None
    clusters = pit_clusters(work)
    # 位能場：距最近礦坑的挖掘成本（無礦時指向底緣 = 最低成本下潛）。
    # baseline = 根盤面可挖前緣的最小位能；挖到更低位能的格 = 朝礦實質推進。
    dist = pit_potential(work)
    baseline = _INF
    for r in range(min(int(visible_rows), len(work))):
        for c in range(len(work[r])):
            if is_frontier_diggable(work, r, c):
                d = dist.get((r, c), _INF)
                if d < baseline:
                    baseline = d

    def _pull_progress(best_dist: float) -> float:
        if baseline == _INF or best_dist == _INF:
            return 0.0
        return max(0.0, baseline - best_dist)
    initial = _State(
        tuple(tuple(row) for row in work), float(shovels),
        int((items or {}).get("bomb", 0)), int((items or {}).get("drill", 0)),
        SearchUsage(), tuple(), False, 0,
    )
    beam = [initial]
    # best 只從展開的子狀態選：無礦時所有動作分數為負，若拿初始狀態當 best
    # 會回空步；planner 合約是「有合法動作就出一步」，停不停由 runtime 決定
    best: Optional[_State] = None
    best_score = None
    expanded = 0
    reached_depth = 0
    budget_hit = False

    for depth in range(config.max_depth):
        next_states = []
        dominance = {}
        for state in beam:
            if time.perf_counter() >= deadline:
                budget_hit = True
                break
            # 捲動後 runtime 會取得新盤面再規劃；深搜在捲動點結束，避免猜下一個 viewport
            if state.scrolled:
                continue
            state_board = [list(row) for row in state.board]
            actions = _candidate_actions(
                state_board, state.pickaxes, state.bombs, state.drills, visible_rows
            )
            if not state.path and valid is not None:
                actions = [action for action in actions if action.key in valid]
            ranked = []
            for action_index, action in enumerate(actions):
                # 細粒度 deadline：單一狀態展開也可能數十 ms（21 列大盤），
                # 只在 state 邊界檢查會超時數十 ms（實測 max 325ms 根因）
                if (action_index & 7) == 0 and time.perf_counter() >= deadline:
                    budget_hit = True
                    break
                child = _apply(state, action, visible_rows, dist)
                if child is None:
                    continue
                score = evaluate_state(
                    work, child.board, child.usage,
                    scrolled=child.scrolled,
                    descent_rows=1 if child.scrolled else 0,
                    opened_path_cells=child.opened_path_cells,
                    clusters=clusters,
                    lost_pits=child.lost_pits,
                    pull_progress=_pull_progress(child.best_dist),
                )
                ranked.append((score.total, action.row, action.col, child, score))
            ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
            for _total, _row, _col, child, score in ranked[: config.branch_width]:
                expanded += 1
                signature = (child.board, child.bombs, child.drills, round(child.pickaxes, 3))
                if dominance.get(signature, float("-inf")) >= score.total:
                    continue
                dominance[signature] = score.total
                next_states.append((score.total, child, score))
                if best_score is None or score.total > best_score.total:
                    best, best_score = child, score
            if budget_hit:
                break
        if budget_hit or not next_states:
            break
        next_states.sort(key=lambda row: (-row[0], row[1].path[0].row, row[1].path[0].col))
        beam = [row[1] for row in next_states[: config.beam_width]]
        reached_depth = depth + 1

    if best is None:
        best = initial
        best_score = evaluate_state(work, work, initial.usage, clusters=clusters)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    steps = [
        action.to_step(cost) for action, cost in zip(best.path, best.path_costs)
    ]
    return {
        "ok": True,
        "message": "final_v1 plan" if steps else "final_v1 no legal step",
        "steps": steps,
        "preview_steps": steps,
        "score_breakdown": best_score.to_dict(),
        "objective_score": best_score.total,
        "elapsed_ms": elapsed_ms,
        "explored_nodes": expanded,
        "search_depth": reached_depth,
        "budget_hit": budget_hit,
        "stats": {
            "ending_inventory": {
                "pickaxe": best.pickaxes, "bomb": best.bombs, "drill": best.drills,
            },
            "score": best_score.to_dict(),
        },
    }

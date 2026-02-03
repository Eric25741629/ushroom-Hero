import os
import json
from typing import List, Dict, Any, Iterable, Tuple

from miner.Mining import (
    plan_with_items_ev,
    plan_collect_all_mines_then_descend_v2,
    print_plan_result,
)

H, W = 7, 6


def pretty_board(board: List[List[str]], col_width: int = 16) -> str:
    lines: List[str] = []
    for row in board:
        parts = [f"{cell:>{col_width}}" for cell in row]
        lines.append(" ".join(parts))
    return "\n".join(lines)


def iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def guess_log_paths(root: str) -> List[str]:
    candidates: List[str] = []
    # 1) root/events.jsonl
    p = os.path.join(root, "events.jsonl")
    if os.path.isfile(p):
        candidates.append(p)
    # 2) root/*/events.jsonl (per-device)
    for name in os.listdir(root):
        sub = os.path.join(root, name)
        if os.path.isdir(sub):
            p2 = os.path.join(sub, "events.jsonl")
            if os.path.isfile(p2):
                candidates.append(p2)
    return candidates


def extract_boards_from_log(path: str, max_samples: int = 5) -> List[List[List[str]]]:
    boards: List[List[List[str]]] = []
    for ev in iter_jsonl(path):
        if not isinstance(ev, dict):
            continue
        b = ev.get("board_before")
        if b and isinstance(b, list) and len(b) == H and all(isinstance(row, list) and len(row) == W for row in b):
            boards.append(b)
            if len(boards) >= max_samples:
                break
    return boards


def run_on_boards(boards: List[List[List[str]]]) -> None:
    for idx, board in enumerate(boards, 1):
        print("\n" + "=" * 80)
        print(f"[RL Replay] Case #{idx}")
        print("Board (7x6):")
        print(pretty_board(board))

        # 先跑基線規劃，若總成本<=1，則跳過道具建議（沒必要比較）
        baseline_plan = plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
        print_plan_result("基線規劃 (執行)", baseline_plan, board)

        total_cost = baseline_plan.get("total_cost", 0)
        if total_cost is not None and total_cost <= 1:
            print("[Skip] 基線成本<=1，跳過道具建議計算。")
            continue

        items_available = {"drill": 1, "bomb": 1}
        item_plan = plan_with_items_ev(board, items_available, drill_threshold=2.0, bomb_threshold=3.0)
        print_plan_result("道具建議 (不執行)", item_plan, board)


def main():
    here = os.path.dirname(__file__)
    rl_root = os.path.join(here, "rl_logs")
    paths = guess_log_paths(rl_root)
    if not paths:
        print(f"找不到任何 RL 日誌於 {rl_root}")
        return
    print("找到以下日誌：")
    for p in paths:
        print(" -", os.path.relpath(p, here))

    total = 0
    for p in paths:
        boards = extract_boards_from_log(p, max_samples=3)
        if boards:
            print(f"\n從 {os.path.relpath(p, here)} 擷取 {len(boards)} 筆樣本")
            run_on_boards(boards)
            total += len(boards)
    if total == 0:
        print("未能從日誌擷取到有效的 board_before 事件。")


if __name__ == "__main__":
    main()

"""Run a single screenshot through the v3 planner — dry-run, no device."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from miner.v2.classifier import BoardClassifierV2, load_board_model
from .planner import plan_v3


def _render_board(board):
    symbols = {
        "empty": ".",
        "void": ".",
        "dug_pit": ".",
        "unreachable_empty": "_",
        "dirt": "D",
        "unreachable_dirt": "d",
        "rock": "R",
        "unreachable_rock": "r",
        "one_hit_rock": "O",
        "reachable_pit": "*",
        "unreachable_pit": "X",
        "pit": "*",
    }
    cols = len(board[0]) if board else 0
    header = "   " + " ".join(str(c) for c in range(cols))
    out = [header]
    for r, row in enumerate(board):
        out.append(f"{r:2d} " + " ".join(symbols.get(cell, cell[:1]) for cell in row))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Miner V3 planner dry-run")
    parser.add_argument("image", type=Path)
    parser.add_argument("--shovels", type=float, default=100.0)
    parser.add_argument("--drill", type=int, default=0)
    parser.add_argument("--bomb", type=int, default=0)
    parser.add_argument("--max-nodes", type=int, default=6000)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()

    model, classes, device = load_board_model()
    classifier = BoardClassifierV2(model=model, classes=classes, device=device)
    snapshot = classifier.classify_snapshot(str(args.image), verify_screen=False)
    plan = plan_v3(
        snapshot.board,
        shovels=args.shovels,
        items={"drill": args.drill, "bomb": args.bomb},
        max_nodes=args.max_nodes,
    )

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=list))
        return

    print(_render_board(snapshot.board))
    print()
    print(plan["message"])
    print(f"strategy={plan['strategy_class']} floor7_open={plan['floor7_open']}")
    print(f"stats={plan['stats']}")
    for i, step in enumerate(plan["steps"], 1):
        kind = step.get("type")
        item = step.get("item")
        pos = step.get("pos")
        cost = step.get("step_cost")
        label = f"{kind}/{item}" if item else kind
        print(f"  {i:2d}. {label:>10s} @ {pos}  cost={cost}")


if __name__ == "__main__":
    main()

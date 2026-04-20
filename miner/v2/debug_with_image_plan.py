from __future__ import annotations

import argparse
import json
from pathlib import Path

from .classifier import BoardClassifierV2, load_board_model
from .planner import plan_v2
from .visualization import format_board


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Miner V2 planning on one screenshot.")
    parser.add_argument("image_path", help="Path to the screenshot to classify and plan.")
    parser.add_argument("--shovels", type=float, default=100.0, help="Available shovel budget.")
    parser.add_argument("--drill", type=int, default=0, help="Available drill count.")
    parser.add_argument("--bomb", type=int, default=0, help="Available bomb count.")
    parser.add_argument("--skip-screen-check", action="store_true", help="Skip mining-screen verification.")
    parser.add_argument("--save-json", help="Optional path to save the combined result JSON.")
    args = parser.parse_args()

    image_path = Path(args.image_path)
    if not image_path.exists():
        raise SystemExit(f"image not found: {image_path}")

    model, classes, device = load_board_model()
    classifier = BoardClassifierV2(model=model, classes=classes, device=device)
    snapshot = classifier.classify_snapshot(
        str(image_path),
        verify_screen=not args.skip_screen_check,
    )
    plan = plan_v2(
        snapshot.board,
        shovels=args.shovels,
        items={"drill": args.drill, "bomb": args.bomb},
    )

    payload = {
        "image_path": str(image_path),
        "screen_check": None if snapshot.screen_check is None else {
            "passed": bool(snapshot.screen_check.passed),
            "matched_points": int(snapshot.screen_check.matched_points),
        },
        "avg_confidence": round(snapshot.avg_confidence, 4),
        "min_confidence": round(snapshot.min_confidence, 4),
        "board_visual": format_board(snapshot.board),
        "plan": plan,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.save_json:
        output_path = Path(args.save_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from .classifier import BoardClassifierV2, load_board_model
from .visualization import format_board


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug Miner V2 board classification with a single image.")
    parser.add_argument("image_path", help="Path to the screenshot to classify.")
    parser.add_argument(
        "--save-samples",
        action="store_true",
        help="Save low-confidence cell crops to the dataset root when dataset_root is configured.",
    )
    parser.add_argument(
        "--skip-screen-check",
        action="store_true",
        help="Skip mining-screen point verification.",
    )
    args = parser.parse_args()

    image_path = Path(args.image_path)
    if not image_path.exists():
        raise SystemExit(f"image not found: {image_path}")

    model, classes, device = load_board_model()
    classifier = BoardClassifierV2(model=model, classes=classes, device=device)
    snapshot = classifier.classify_snapshot(
        str(image_path),
        verify_screen=not args.skip_screen_check,
        save_samples=args.save_samples,
    )

    print(f"[Miner V2] image={image_path}")
    print(f"[Miner V2] captured_at={snapshot.captured_at}")
    print(f"[Miner V2] image_shape={snapshot.image_shape}")
    print(f"[Miner V2] avg_confidence={snapshot.avg_confidence:.4f}")
    print(f"[Miner V2] min_confidence={snapshot.min_confidence:.4f}")
    if snapshot.screen_check is not None:
        print(
            "[Miner V2] screen_check="
            f"passed={snapshot.screen_check.passed}, matched_points={snapshot.screen_check.matched_points}"
        )

    print("\n[Miner V2] board")
    print(format_board(snapshot.board))

    print("\n[Miner V2] confidences")
    for row_index, row in enumerate(snapshot.confidences):
        rounded = ", ".join(f"{value:.3f}" for value in row)
        print(f"row {row_index}: {rounded}")


if __name__ == "__main__":
    main()

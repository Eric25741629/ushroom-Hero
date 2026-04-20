from __future__ import annotations

import argparse
import json
from pathlib import Path

from .classifier import BoardClassifierV2, load_board_model
from .llm_judge import judge_snapshot, resolve_endpoint_and_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Miner V2 board classification and LLM judgment on one image.")
    parser.add_argument("image_path", help="Path to the screenshot to classify.")
    parser.add_argument("--endpoint", help="OpenAI-compatible chat completions endpoint.")
    parser.add_argument("--model", help="Model id. If omitted, auto-detect from /v1/models.")
    parser.add_argument("--device-id", default="debug_image", help="Logical device id for the prompt.")
    parser.add_argument("--timeout", type=int, default=120, help="LLM request timeout in seconds.")
    parser.add_argument("--max-tokens", type=int, default=500, help="Max tokens for the LLM response.")
    parser.add_argument("--skip-screen-check", action="store_true", help="Skip mining-screen point verification.")
    parser.add_argument("--with-image", action="store_true", help="Also attach the source image for vision-capable models.")
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

    probe, resolved_model = resolve_endpoint_and_model(
        preferred_endpoint=args.endpoint,
        preferred_model=args.model,
        timeout=min(args.timeout, 15),
    )

    result = judge_snapshot(
        snapshot=snapshot,
        endpoint=probe.endpoint,
        model=resolved_model,
        device_id=args.device_id,
        image_path=str(image_path),
        include_image=args.with_image,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
    )

    combined = {
        "image_path": str(image_path),
        "endpoint": probe.endpoint,
        "model": resolved_model,
        "probe_models": probe.model_ids,
        "screen_check": None if snapshot.screen_check is None else {
            "passed": bool(snapshot.screen_check.passed),
            "matched_points": int(snapshot.screen_check.matched_points),
        },
        "avg_confidence": round(snapshot.avg_confidence, 4),
        "min_confidence": round(snapshot.min_confidence, 4),
        "judgment": result.judgment,
        "next_action": result.next_action,
        "confidence": result.confidence,
        "reason": result.reason,
        "suspect_cells": result.suspect_cells,
        "raw_content": result.raw_content,
    }

    print(json.dumps(combined, ensure_ascii=False, indent=2))

    if args.save_json:
        output_path = Path(args.save_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

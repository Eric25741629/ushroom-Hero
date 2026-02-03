"""即時檢查盤面與規劃（單次執行）
用法:
  python -m miner.inspect_board --device <device_serial>

會輸出：
- 盤面標籤矩陣
- 每格信心
- find_tool_candidate 候選（若有）
- plan_collect_all_mines_then_descend_v2 規劃結果
"""
from __future__ import annotations

import argparse
import pprint
import uiautomator2 as u2

from .classifier import load_cnn_model, ClassifierCNN
from .Mining import find_tool_candidate, print_plan_result
from .planner import plan_collect_all_mines_then_descend_v2
import cv2


def main(device_serial: str):
    print(f"Connecting to device: {device_serial}")
    d = u2.connect(device_serial)
    img = d.screenshot(format='opencv')

    print("Loading CNN model...")
    model, classes, dev = load_cnn_model()
    clf = ClassifierCNN(model=model, classes=classes, device=dev)

    print("Classifying board...")
    board, confidences = clf.classify_board(img, save_samples=False, save_conf_threshold=0.8)

    print("Detected board:")
    for r,row in enumerate(board):
        print(f"R{r}: ", row)
    print("Confidences:")
    for r,row in enumerate(confidences):
        print(f"R{r}: ", [round(x,3) for x in row])

    print("\nChecking tool candidate...")
    candidate = find_tool_candidate(board)
    if candidate:
        pprint.pprint(candidate)
    else:
        print("No tool candidate found.")

    print("\nCompute baseline plan (collect all then descend)...")
    plan = plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
    print_plan_result("Baseline Plan", plan, board)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', '-d', default='emulator-5554', help='device serial (adb)')
    args = parser.parse_args()
    main(args.device)

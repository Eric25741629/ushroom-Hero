"""最小化除錯腳本：檢查 get_stage 判定流程
Usage:
  # 使用真機截圖
  python test_stage_debug.py --device
  # 或使用本地截圖檔
  python test_stage_debug.py --img screenshots/main_page.jpg

會輸出遠端回傳結果、本地 OCR 結果、合併文字、main_count 以及 stage_by_str 結果，並儲存 debug 圖片與 ocr json。
"""
import argparse
import os
import json
import time
import logging
from pprint import pprint

import cv2

# 專案內模組
import uiautomator2 as u2
import img_tools
from game_state.detector import stage_by_str, get_stage

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def save_img(img, name="debug_stage.jpg"):
    path = os.path.abspath(name)
    cv2.imwrite(path, img)
    logger.info(f"Saved debug image: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--device', action='store_true', help='從真機擷取截圖')
    group.add_argument('--img', type=str, help='使用本地截圖檔')
    parser.add_argument('--serial', type=str, default=None, help='真機序號或 ip:port (可搭配 --device)')
    args = parser.parse_args()

    d = None
    img = None

    if args.device:
        try:
            if args.serial:
                d = u2.connect(args.serial)
            else:
                d = u2.connect()
            img = d.screenshot(format='opencv')
        except Exception as e:
            logger.error(f"連線真機失敗: {e}")
            return
    else:
        imgpath = args.img
        img = cv2.imread(imgpath)
        if img is None:
            logger.error(f"無法讀取圖片: {imgpath}")
            return

    save_img(img, name='debug_stage.jpg')

    # 1) 呼叫遠端舞台分析（如果有回應，會顯示）
    try:
        remote = img_tools.analyze_stage_via_server(img)
        logger.info("Remote analyze_stage_via_server result:")
        pprint(remote)
    except Exception as e:
        logger.exception('呼叫 analyze_stage_via_server 發生例外')
        remote = None

    # 2) 本地 OCR（文字清單）
    try:
        texts = img_tools.get_all_text(img)
        logger.info("Local OCR texts:")
        pprint(texts)
    except Exception as e:
        logger.exception('呼叫 get_all_text 發生例外')
        texts = []

    # 儲存 OCR 結果
    with open('debug_ocr.json', 'w', encoding='utf-8') as f:
        json.dump({'texts': texts, 'remote': remote}, f, ensure_ascii=False, indent=2)
        logger.info('Saved debug_ocr.json')

    # 3) 顯示合併文字與 main_count
    full_text = ''.join(texts)
    logger.info(f'Full text: {full_text}')
    main_page_features = ["方案", "副本", "家園", "試煉", "戰鬥"]
    main_count = sum(1 for feature in main_page_features if feature in full_text)
    logger.info(f'main_count: {main_count} (features matched)')

    # 4) 呼叫 stage_by_str
    try:
        mapped = stage_by_str(d, texts, img)
        logger.info(f'stage_by_str -> {mapped}')
    except Exception as e:
        logger.exception('呼叫 stage_by_str 發生例外')

    # 5) 若有連到真機，同時執行 get_stage 觀察最終行為
    if d is not None:
        try:
            final = get_stage(d, Cnn_model=None, easyocr_reader=None)
            logger.info(f'get_stage -> {final}')
        except Exception as e:
            logger.exception('呼叫 get_stage 發生例外')

    logger.info('Done.')


if __name__ == '__main__':
    main()

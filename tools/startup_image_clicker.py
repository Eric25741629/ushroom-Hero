import os
import sys
import json
import cv2
import time
import logging
import opencc
import numpy as np
from typing import Union, List, Dict, Any

# Ensure project root is on sys.path so imports like `import img_tools` work
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import img_tools
from game_state.detector import stage_by_str

logger = logging.getLogger(__name__)


def _load_image(img: Union[str, np.ndarray]) -> np.ndarray:
    if isinstance(img, str):
        if not os.path.exists(img):
            raise FileNotFoundError(img)
        arr = cv2.imread(img)
        if arr is None:
            raise ValueError(f"Cannot read image: {img}")
        return arr
    else:
        return img


def analyze_startup_image(img: Union[str, np.ndarray], use_remote_stage: bool = False) -> Dict[str, Any]:
    """Analyze a single screenshot and return suggested click coordinates.

    Returns a dict with keys: `stage`, `ocr_preview`, `clicks`.
    - `clicks` is a list of {x,y,label,method,note}.
    """
    img_cv = _load_image(img)

    # 1) get OCR texts (list of strings) for stage detection
    try:
        ocr_texts = img_tools.get_all_text(img_cv)
        print(f"OCR Texts: {ocr_texts}")
    except Exception as e:
        logger.warning(f"local OCR failed: {e}")
        ocr_texts = []

    # 2) determine stage using existing detector logic
    # Special-case override: if OCR contains '車位倉庫', '自動收車...' and '領取',
    # it's definitely the parking/warehouse screen -> force '車位倉庫'.
    if (any('車位倉庫' in t for t in ocr_texts)
            and any(("自動收車" in t or "自動收車記" in t) for t in ocr_texts)
            and any('領取' in t for t in ocr_texts)):
        stage = '車位倉庫'
    else:
        stage = stage_by_str(None, ocr_texts, img_cv)

    # 3) prepare preview
    ocr_preview = "|".join(ocr_texts[:12])

    # 4) request detailed OCR with bboxes (use remote analyzer which returns bbox)
    clicks: List[Dict[str, Any]] = []
    try:
        detailed = img_tools.analyze_skill_via_http(img_cv)
    except Exception as e:
        logger.warning(f"analyze_skill_via_http failed: {e}")
        detailed = {'success': False}

    ocr_results = detailed.get('ocr_results', []) if detailed.get('success', True) else []
    converter = opencc.OpenCC('s2t')

    def find_text_bbox(target: str):
        t = converter.convert(target)
        for item in ocr_results:
            txt = converter.convert(item.get('text', ''))
            if t in txt:
                x1, y1, x2, y2 = item.get('bbox', [0, 0, 0, 0])
                cx = int((x1 + x2) // 2)
                cy = int((y1 + y2) // 2)
                return cx, cy, txt
        return None

    # Mapping stages -> target texts or actions
    if stage == "隱藏":
        res = find_text_bbox('隱藏')
        if res:
            x, y, found = res
            clicks.append({"x": x, "y": y, "label": found, "method": "text_bbox", "note": "點擊隱藏按鈕"})
        else:
            # fallback: click center-top area
            h, w = img_cv.shape[:2]
            clicks.append({"x": w//2, "y": 100, "label": "click_white_top", "method": "fallback", "note": "嘗試點擊上方空白"})

    elif stage in ("離線獎勵", "放置獎勵"):
        # find text like '領取' or '領取獎勵'
        for t in ('領取', '領取獎勵', '領取獎勵!'):
            res = find_text_bbox(t)
            if res:
                x, y, found = res
                clicks.append({"x": x, "y": y, "label": found, "method": "text_bbox", "note": "點擊領取"})
                break
        if not clicks:
            # generic fallback: center
            h, w = img_cv.shape[:2]
            clicks.append({"x": w//2, "y": h//2, "label": "center", "method": "fallback", "note": "leader fallback"})

    elif stage == "公告":
        # try find '同意' or '跳過' or a close button
        for t in ('同意', '點擊繼續', '跳過', '關閉'):
            res = find_text_bbox(t)
            if res:
                x, y, found = res
                clicks.append({"x": x, "y": y, "label": found, "method": "text_bbox", "note": "關閉公告/同意"})
                break
        if not clicks:
            # click near top-right assumed close
            h, w = img_cv.shape[:2]
            clicks.append({"x": w-50, "y": 50, "label": "close_top_right", "method": "fallback", "note": "關閉彈窗候補位置"})

    elif stage == "前往活動":
        # click a blank area to dismiss
        h, w = img_cv.shape[:2]
        clicks.append({"x": w//2, "y": h//2, "label": "dismiss_blank", "method": "fallback", "note": "點擊空白關閉前往活動"})

    elif stage == "購物管家":
        # try to find '採購' or '副本管家' or '掃蕩'
        for t in ('採購', '副本管家', '掃蕩'):
            res = find_text_bbox(t)
            if res:
                x, y, found = res
                clicks.append({"x": x, "y": y, "label": found, "method": "text_bbox", "note": "購物管家操作"})
                break
        if not clicks:
            h, w = img_cv.shape[:2]
            clicks.append({"x": w//2, "y": h//2, "label": "shopping_fallback", "method": "fallback", "note": "購物管家候補"})

    elif stage == "車位倉庫":
        res = find_text_bbox('領取')
        if res:
            x, y, found = res
            clicks.append({"x": x, "y": y, "label": found, "method": "text_bbox", "note": "領取車位獎勵"})
        else:
            h, w = img_cv.shape[:2]
            clicks.append({"x": w//2, "y": h//2, "label": "parking_fallback", "method": "fallback", "note": "候補領取位置"})

    elif stage == "主頁面":
        # no startup click needed
        clicks = []

    else:
        # 未知：給出 OCR 最常見文字位置作為提示
        if ocr_results:
            item = ocr_results[0]
            x1, y1, x2, y2 = item.get('bbox', [0, 0, 0, 0])
            clicks.append({"x": int((x1 + x2)//2), "y": int((y1 + y2)//2), "label": converter.convert(item.get('text','')), "method": "ocr_suggestion", "note": "建議點擊第一個辨識到的文字"})
        else:
            h, w = img_cv.shape[:2]
            clicks.append({"x": w//2, "y": h//2, "label": "center_suggestion", "method": "fallback", "note": "無 OCR，可點中間"})

    return {
        "stage": stage,
        "ocr_preview": ocr_preview,
        "clicks": clicks,
    }


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('image', help='path to screenshot image')
    p.add_argument('--json', action='store_true', help='print JSON')
    args = p.parse_args()

    out = analyze_startup_image(args.image)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"stage: {out['stage']}")
        for c in out['clicks']:
            print(f"click -> x={c['x']} y={c['y']} label={c['label']} note={c.get('note','')}")

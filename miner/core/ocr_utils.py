"""與 OCR 伺服器互動、解析鏟子數量的輔助函式。"""
from __future__ import annotations

import base64
import os
import time
import re
from typing import Optional

import cv2
import requests
try:
    # 先嘗試直接匯入（作為 package 時通常可行）
    import img_tools
except Exception:
    # 若直接執行 script（工作目錄為本模組）或在不同匯入情境，嘗試多種回退
    try:
        # 當作 package 時由上層匯入
        from .. import img_tools
    except Exception:
        # 最後嘗試把專案根目錄加入 sys.path 再匯入
        import sys
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        import img_tools
try:
    # 正常作為 package 被 import 時使用相對匯入
    from .config import OCR_SERVER_URL
except Exception:
    # 若直接以 script 執行 (沒有 parent package)，嘗試絕對匯入或從環境變數回退
    try:
        from miner.core.config import OCR_SERVER_URL  # 當在專案根目錄執行時
    except Exception:
        try:
            from config import OCR_SERVER_URL  # 最後嘗試直接匯入
        except Exception:
            import os
            OCR_SERVER_URL = os.environ.get("OCR_SERVER_URL", "http://127.0.0.1:5001")


def _extract_inventory_number(result: Optional[dict]) -> int:
    """從 OCR 回傳結果中擷取庫存數量，支援 '0/2' 格式。"""
    if not result or result == 404:
        return 0
    if isinstance(result, dict) and result.get("success") is False:
        return 0
    ocr_results = result.get("ocr_results", []) if isinstance(result, dict) else []
    for res in ocr_results:
        text = str(res.get("text", "")).strip()
        
        # 優先處理 '0/2' 或 '5/10' 這種帶斜線的格式
        if "/" in text:
            parts = text.split("/")
            # 取斜線前的數字作為目前庫存
            stock_nums = re.findall(r'\d+', parts[0])
            if stock_nums:
                return int(stock_nums[-1])
        
        # 一般數字提取
        nums = re.findall(r'\d+', text)
        if nums:
            # 取最後一個數字
            return int(nums[-1])
    return 0


def check_pickaxe_count(d) -> int:
    """截圖後送進 OCR 服務，解析目前剩餘鏟子數。"""
    try:
        img = d.screenshot(format="opencv")[13:40, 148:251]
        img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, img_bin = cv2.threshold(img_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(img_bin)
        print("OCR 服務回傳結果:", result)
        if result is None:
            return 20
        ocr_results = result.get("ocr_results", [])
        if not ocr_results:
            save_ocr_error_image(img)
            return 20
        text = ocr_results[0].get("text", "20").split("/")[0]
        # 提取數字
        nums = re.findall(r'\d+', text)
        try:
            return int(nums[0]) if nums else 20
        except ValueError:
            save_ocr_error_image(img)
            return 20
    except Exception:
        print("OCR 未返回有效結果或解析失敗，回傳預設值 20")
        return 20


def check_drill_num(d) -> int:
    """解析畫面右下角的鑽頭庫存數量，失敗時回傳 0。"""
    try:
        img = d.screenshot(format="opencv")
        # 廣域 ROI 以支援 4 位數，並使用二值化預處理
        roi = img[910:950, 140:210]
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, roi_bin = cv2.threshold(roi_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(roi_bin)
        return _extract_inventory_number(result)
    except Exception as exc:  # pragma: no cover - 依賴實機
        print(f"鑽頭數量解析失敗: {exc}")
        return 0


def check_boom_num(d) -> int:
    """解析畫面右下角的炸彈庫存數量，失敗時回傳 0。"""
    try:
        img = d.screenshot(format="opencv")
        # 廣域 ROI 以支援 4 位數，並使用二值化預處理
        roi = img[910:950, 350:420]
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, roi_bin = cv2.threshold(roi_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(roi_bin)
        return _extract_inventory_number(result)
    except Exception as exc:  # pragma: no cover - 依賴實機
        print(f"炸彈數量解析失敗: {exc}")
        return 0


def save_ocr_error_image(img) -> None:
    """若 OCR 結果異常，將截圖存檔以供手動檢查。"""
    os.makedirs("ocr_errors", exist_ok=True)
    timestamp = int(time.time())
    cv2.imwrite(os.path.join("ocr_errors", f"ocr_error_{timestamp}.png"), img)


__all__ = [
    "check_pickaxe_count",
    "check_drill_num",
    "check_boom_num",
    "save_ocr_error_image",
]
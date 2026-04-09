from typing import Optional

import numpy as np
from utils.logging_utils import logger

def new_stage_check(img):
    if [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]:
        return True
    return False

def stage_by_str(d, ocr_str: list, img: np.ndarray) -> str:
    """
    Determines the current game stage based on OCR text.
    """
    full_text = "".join(ocr_str)

    # Login-conflict popup text varies across builds/OCR results.
    if (
        ("另一個地方" in full_text and ("登入" in full_text or "登錄" in full_text))
        or "被迫下線" in full_text
    ):
        return "異地登錄"

    # 0. 優先判定「車位倉庫」：此頁常疊在主頁元素上，若先判主頁面容易誤判
    if ("車位倉庫" in full_text and
            ("自動收車" in full_text or "自動收車記" in full_text or "已自動收車" in full_text)):
        return "車位倉庫"
    if "離線獎勵" in full_text:
        return "放置獎勵"
    if '本場個人擊敗' in full_text:
            return "家族戰"
    # 1. 主頁面判定
    # 保留較保守的條件，避免把彈窗/覆蓋頁誤判成主頁面
    main_page_features = ["方案", "副本", "家園", "試煉", "戰鬥"]
    main_count = sum(1 for feature in main_page_features if feature in full_text)
    if main_count >= 2 or ("方案" in full_text and "戰鬥" in full_text):
        return "主頁面"

    # 2. 其他頁面判定
    stage_keywords = {
        "你的帳號在另一個地方登錄": "異地登錄",
        "你的帳號在另一個地方登入": "異地登錄",
        "您的帳號在另一個地方登錄": "異地登錄",
        "您的帳號在另一個地方登入": "異地登錄",
        "您已被迫下線": "異地登錄",
        "被迫下線": "異地登錄",
        "退出遊戲": "異地登錄",
        "隱藏": "隱藏",
        "前往活動": "前往活動",
        "車位倉庫": "車位倉庫",
        "購物管家":"購物管家",
        "放置獎勵": "放置獎勵",
        "離線獎勵": "放置獎勵",
        "家族商店": "家族",
        "家族亂鬥": "家族",
        "征戰熔岩巨獸": "征戰熔岩巨獸",
    }

    # 3. 公告判定 (通常是彈窗)
    # 使用遠端 OCR 的 bbox 資訊來決定是否為可操作的公告彈窗
    if "公告" in full_text:
        has_valid_announcement = False
        try:
            ocr_full = img_tools.analyze_skill_via_http(img)
            if ocr_full.get('success') and ocr_full.get('ocr_results'):
                for item in ocr_full.get('ocr_results', []):
                    text = item.get('text', '')
                    if '公告' not in text:
                        continue
                    bbox = item.get('bbox')
                    x_coord = None
                    # 常見格式：[[x0,y0], [x1,y1], ...] 或 [x, y, w, h] 或 直接 x
                    if isinstance(bbox, (list, tuple)) and len(bbox) > 0:
                        first = bbox[0]
                        if isinstance(first, (list, tuple)) and len(first) > 0:
                            x_coord = int(first[0])
                        elif isinstance(first, (int, float)):
                            x_coord = int(first)
                    elif isinstance(bbox, (int, float)):
                        x_coord = int(bbox)

                    if x_coord is None:
                        logger.debug(f"無法解析公告座標格式: {bbox}")
                        continue

                    if x_coord > 155:
                        has_valid_announcement = True
                        logger.info(f"偵測到公告（X={x_coord} > 155），視為可關閉公告")
                        break
                    else:
                        logger.debug(f"公告座標 X={x_coord} ≤ 155，視為不可操作公告，忽略")
        except Exception as e:
            logger.debug(f"遠端公告 bbox 判定失敗: {e}")

        # 若遠端有回傳可操作的公告，回傳 '公告'
        if has_valid_announcement:
            return "公告"

        # 若遠端未能判定為可操作公告，保留原始文字回退邏輯
        if any(k in full_text for k in ["同意", "點擊繼續", "跳過"]):
            return "公告"
        if main_count == 0:
            return "公告"

    for keyword, stage_name in stage_keywords.items():
        if keyword in full_text:
            return stage_name
            
    return "未知"


import img_tools

def get_stage(d, Cnn_model, easyocr_reader=None, img: Optional[np.ndarray] = None):
    """ 
    截圖並判斷目前所在的頁面 (優先使用遠端大腦判定)。
    """
    if img is None:
        img = d.screenshot(format='opencv')
    
    # --- 本地優先判定 (Local first) ---
    # 1. 優先檢查特定區域 (ROI) 的彈窗標題
    roi_announcement = img[170:220, 210:350]
    if any("公告" in t for t in img_tools.get_all_text(roi_announcement)):
        logger.info("偵測到公告彈窗 (ROI 判定)")
        return "公告"

    roi_parking = img[250:300, 200:350]
    if any("車位倉庫" in t for t in img_tools.get_all_text(roi_parking)):
        logger.info("偵測到車位倉庫 (ROI 判定)")
        return "車位倉庫"

    # 2. 使用本地 OCR 取得全螢幕文字並判定
    try:
        local_texts = img_tools.get_all_text(img)
    except Exception as e:
        logger.warning(f"本地 OCR 發生例外: {e}")
        local_texts = []

    stage_withocr = stage_by_str(d, local_texts, img)
    if stage_withocr == "異地登錄":
        logger.error("異地登錄，請檢查帳號密碼安全性")
        return "異地登錄"

    if stage_withocr != "未知":
        logger.info(f"本地 OCR 辨識結果: {stage_withocr}")
        return stage_withocr

    # --- 若本地無法判定，嘗試遠端備援 (Remote fallback) ---
    try:
        remote_result = img_tools.analyze_stage_via_server(img)
        if remote_result.get("success"):
            stage = remote_result.get("stage", "未知")
            logger.info(f"遠端大腦辨識結果: {stage}")
            if stage != "未知":
                return stage
            else:
                logger.debug("遠端也回傳 '未知'，最終回傳本地結果 (未知)")
    except Exception as e:
        logger.debug(f"遠端判定不可用: {e}")

    logger.info(f"OCR辨識結果: {stage_withocr}")
    return stage_withocr

import numpy as np
from utils.logging_utils import logger

def new_stage_check(img):
    if [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]:
        return True
    return False

def stage_by_str(d, ocr_str: list, img: np.ndarray) -> str:
    """
    Determines the current game stage based on OCR text and saves a debug image.

    Args:
        d: The uiautomator2 device object (remains for potential future use).
        ocr_str: A list of strings detected by OCR.
        img: The OpenCV image corresponding to the OCR text.

    Returns:
        The name of the detected stage or "未知".
    """
    # Combine list of strings into a single string for easier searching
    full_text = "".join(ocr_str)
    # print("OCR偵測文字:", full_text)
    # Mapping of keywords to stage names and their properties
    stage_keywords = {
        "隱藏": "隱藏",
        "車位倉庫": "車位倉庫",
        "購物管家":"購物管家",
        "你的帳號在另一個地方登錄": "異地登錄",
        "退出遊戲": "異地登錄",
        "公告": "公告",
        "方案": "主頁面",
        "放置獎勵": "放置獎勵",
        "離線獎勵": "放置獎勵",
        "家族商店": "家族",
        "家族亂鬥": "家族",
        "征戰熔岩巨獸": "征戰熔岩巨獸",
        
    }

    for keyword, stage_name in stage_keywords.items():
        # Special condition for "征戰熔岩巨獸"
        if  "購物管家" in full_text and "離線獎勵" in full_text:
            return "放置獎勵"
        if  "購物管家" in full_text and "車位倉庫" in full_text:
            return "車位倉庫"
        if stage_name == "征戰熔岩巨獸":
            if "征戰熔岩巨獸" in full_text and "掃蕩" in full_text:
                # img_tools.save_stage_debug_image(stage_name, img)
                return stage_name
        # General condition for other stages
        elif keyword in full_text:
            # img_tools.save_stage_debug_image(stage_name, img)
            return stage_name
            
    return "未知"


def get_stage(d, Cnn_model, easyocr_reader):
    """ 截圖並判斷目前所在的頁面 """
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img, detail=0)
    stage_withocr = stage_by_str(d, result, img)
    if stage_withocr == "異地登錄":
        logger.error("異地登錄，請檢查帳號密碼安全性")
        return "異地登錄"
    logger.info(f"OCR辨識結果: {stage_withocr}")
    return stage_withocr

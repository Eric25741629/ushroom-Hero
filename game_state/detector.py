from typing import Optional

import numpy as np
from utils.logging_utils import logger

def _announcement_is_actionable(ocr_full) -> bool:
    """True iff some OCR result whose (raw) text contains 「公告」 has an x-coord > 155.

    Operates on the raw server json (``analyze_skill_via_http`` shape) so the
    text comparison matches the un-converted server text exactly as the legacy
    inline check did. Tolerates the common bbox formats:
    ``[[x0,y0], ...]`` (polygon), ``[x, y, w, h]``, or a scalar x.
    """
    if not ocr_full or not ocr_full.get('success') or not ocr_full.get('ocr_results'):
        return False
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
            logger.info(f"偵測到公告（X={x_coord} > 155），視為可關閉公告")
            return True
        else:
            logger.debug(f"公告座標 X={x_coord} ≤ 155，視為不可操作公告，忽略")
    return False


def stage_by_str(d, ocr_str: list, img: np.ndarray, ocr_full=None) -> str:
    """
    Determines the current game stage based on OCR text.

    ocr_full: optional pre-fetched raw OCR response (analyze_skill_via_http
    shape). When provided, the 公告 bbox branch reuses it instead of issuing a
    second OCR call. Standalone callers that omit it keep the legacy behavior of
    fetching bbox data on demand.
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
            # 重用呼叫端傳入的 OCR 結果；僅在未提供時才再打一次 OCR（保留獨立呼叫端行為）。
            if ocr_full is None:
                ocr_full = img_tools.analyze_skill_via_http(img)
            has_valid_announcement = _announcement_is_actionable(ocr_full)
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
    """取得目前 stage；Web H5 僅接受 Cocos 狀態，ADB 維持 OCR。

    單次 OCR：整幀只打一次 OCR endpoint，texts 與 bbox 同源重用，取代過去同一幀
    重複 3-4 次的 ROI / 全幀 OCR 呼叫。優先序與舊版一致：
    公告(可操作) > 車位倉庫 > 異地登錄 > 主頁面 > ... > 未知。
    """
    # Web H5 的正式流程不能把 Cocos 的 None 當成 OCR 安全網。未知或探測
    # 失敗直接回傳明確狀態，讓 stage guard 停止目前任務；ADB 才走舊 OCR。
    if getattr(d, "backend_kind", None) == "web_h5":
        from utils.page_detector import probe_h5_state

        h5_result = probe_h5_state(d, getattr(d, "device_id", None))
        stage = h5_result.legacy_stage()
        logger.info(
            "Web H5 Cocos state=%s page=%s reason=%s，跳過 OCR",
            h5_result.state.value,
            getattr(h5_result.page_state, "value", None),
            h5_result.reason or "-",
        )
        return stage

    if img is None:
        img = d.screenshot(format='opencv')

    try:
        local_texts, ocr_full = img_tools.get_all_text_with_results(img)
    except Exception as e:
        logger.warning(f"OCR 發生例外: {e}")
        local_texts, ocr_full = [], None

    full_text = "".join(local_texts)

    # 公告 優先（取代原 roi_announcement 的獨立 OCR）：同一幀、bbox x>155 守門
    if "公告" in full_text and _announcement_is_actionable(ocr_full):
        logger.info("偵測到公告彈窗")
        return "公告"

    # 車位倉庫 優先（取代原 roi_parking 的獨立 OCR）：此頁疊在主頁元素上，
    # 故在 full_text 上即早判定以維持其對主頁面的優先序。
    if "車位倉庫" in full_text:
        logger.info("偵測到車位倉庫")
        return "車位倉庫"

    stage_withocr = stage_by_str(d, local_texts, img, ocr_full=ocr_full)
    if stage_withocr == "異地登錄":
        logger.error("異地登錄，請檢查帳號密碼安全性")
        return "異地登錄"

    logger.info(f"OCR辨識結果: {stage_withocr}")
    return stage_withocr

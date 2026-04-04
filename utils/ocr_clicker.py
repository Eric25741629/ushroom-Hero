import img_tools
import logging

logger = logging.getLogger(__name__)

def click_str(str1: str, d, easyocr_reader=None):
    """
    點擊指定文字 (已遷移至 PaddleOCR)。
    原本的 easyocr_reader 已不再使用，保留參數僅為向後相容。
    """
    return img_tools.click_str_by_server(d, str1, wait_timeout=5)

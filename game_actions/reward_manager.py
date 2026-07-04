import time
import numpy as np
import os
import img_tools
from tools import click_white
from utils.logging_utils import logger

def reward(d, easyocr_reader=None):
    """
    領取獎勵邏輯 (維持硬座標，使用 PaddleOCR/大腦判定)
    """
    # 使用原本的硬座標點擊進入
    d.click(162, 725)
    time.sleep(3)
    
    img = d.screenshot(format='opencv')
    
    # 這裡可以選擇用 OCR 判定或維持原本的顏色判定
    # 既然您希望保留前後端，我們可以用 OCR 判定是否出現領獎字樣
    # 但點擊位置維持硬座標
    result = img_tools.wait_for_any_text(d, ["領取", "放置獎勵"], timeout=2, click_if_found=False)
    
    if result:
        logger.info(f"偵測到獎勵介面: {result}")
        # 原本的硬座標顏色採樣點
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            click_white(d)
            time.sleep(1)
            
        # 使用原本的硬座標點擊領取
        d.click(330, 725)
        time.sleep(2)
        click_white(d)
        time.sleep(1)

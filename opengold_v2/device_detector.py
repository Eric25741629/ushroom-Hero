"""
裝備偵測器 - 自動偵測是否擁有連閃裝備

透過分析階段名稱來判斷
"""

from typing import List, Optional
from .config import OpenGoldConfig


class DeviceDetector:
    """裝備偵測器"""
    
    def __init__(self, config: Optional[OpenGoldConfig] = None):
        self.config = config or OpenGoldConfig()
    
    def detect_lian_shan_equip(self, stage_texts: List[str]) -> bool:
        """從階段名稱偵測是否擁有連閃裝備
        
        檢查階段名稱中是否包含 '連閃' 或 '爆閃' 關鍵字
        
        參數:
        - stage_texts: 階段名稱列表，如 ['技回', '連閃', '反爆', ...]
        
        回傳:
        - True: 偵測到連閃裝備
        - False: 未偵測到
        """
        if not stage_texts:
            return False
        
        for text in stage_texts:
            for keyword in self.config.lian_shan_keywords:
                if keyword in text:
                    print(f"[DeviceDetector] 偵測到連閃裝備關鍵字: '{keyword}' 在 '{text}'")
                    return True
        
        return False
    
    def detect_lian_shan_from_ocr_result(self, stage_result: Optional[dict]) -> bool:
        """從 OCR 結果偵測連閃裝備
        
        參數:
        - stage_result: analyze_stage_via_http 的回傳結果
        
        回傳:
        - True: 偵測到連閃裝備
        - False: 未偵測到或結果無效
        """
        if not stage_result or not isinstance(stage_result, dict):
            return False
        
        stage_texts = stage_result.get('stage_texts', [])
        if not stage_texts:
            return False
        
        return self.detect_lian_shan_equip(stage_texts)

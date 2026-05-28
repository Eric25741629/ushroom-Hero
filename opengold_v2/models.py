"""
資料模型 - 定義核心資料結構
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple


@dataclass
class SkillEntry:
    """單一詞條資料"""
    code: str                           # 技能代碼：技、反、爆、連、暈、閃、回
    probability: Optional[float] = None # 機率值，可能為 None（OCR 未辨識到）
    
    def is_valid(self) -> bool:
        """檢查是否為有效詞條"""
        return bool(self.code) and len(self.code.strip()) > 0
    
    def get_prob_or_zero(self) -> float:
        """取得機率值，None 時回傳 0.0"""
        return self.probability if self.probability is not None else 0.0
    
    def __repr__(self) -> str:
        return f"SkillEntry({self.code}={self.probability}%)"


@dataclass
class Equipment:
    """裝備資料（包含兩個詞條）"""
    entries: List[SkillEntry] = field(default_factory=list)
    
    def __post_init__(self):
        """確保最多只有兩個詞條"""
        if len(self.entries) > 2:
            self.entries = self.entries[:2]
    
    def get_combo(self) -> str:
        """取得組合字串，如 '技回'、'連閃'"""
        valid_codes = [e.code for e in self.entries if e.is_valid()]
        return ''.join(valid_codes)
    
    def to_map(self) -> Dict[str, float]:
        """轉為 skill_code -> prob 映射，None 視為 0.0"""
        return {e.code: e.get_prob_or_zero() for e in self.entries if e.is_valid()}
    
    def is_complete(self) -> bool:
        """檢查是否完整（有兩個有效詞條）"""
        valid_entries = [e for e in self.entries if e.is_valid()]
        return len(valid_entries) >= 2
    
    def get_entry_by_code(self, code: str) -> Optional[SkillEntry]:
        """依技能代碼取得詞條"""
        for entry in self.entries:
            if entry.code == code:
                return entry
        return None
    
    @classmethod
    def from_pairs(cls, pairs: List[Tuple[str, Optional[float]]]) -> 'Equipment':
        """從 (code, prob) 列表建立 Equipment"""
        entries = [SkillEntry(code=c, probability=p) for c, p in pairs if c]
        return cls(entries=entries)


@dataclass
class ComparisonDetail:
    """單一技能比較詳情"""
    skill_code: str
    rolled_prob: Optional[float]
    orig_prob: Optional[float]
    better: Optional[bool] = None      # None 表示無法比較
    rolled_unknown: bool = False
    orig_unknown: bool = False
    note: str = ""


@dataclass
class ComparisonResult:
    """詞條比較結果"""
    should_replace: bool               # 是否建議換裝
    reason: str                        # 決策原因說明
    details: Dict[str, ComparisonDetail] = field(default_factory=dict)
    
    def get_detail(self, skill_code: str) -> Optional[ComparisonDetail]:
        """取得特定技能的比較詳情"""
        return self.details.get(skill_code)
    
    def __repr__(self) -> str:
        decision = "換裝" if self.should_replace else "保留"
        return f"ComparisonResult({decision}: {self.reason})"


@dataclass
class OCRItem:
    """OCR 辨識項目"""
    text: str
    bbox: Optional[Tuple[int, int, int, int]] = None  # (x1, y1, x2, y2)
    score: float = 1.0
    
    def __repr__(self) -> str:
        return f"OCRItem('{self.text}', score={self.score:.2f})"


@dataclass
class ParsedOCRResult:
    """OCR 解析結果"""
    panel: Dict[str, int] = field(default_factory=dict)           # 面板數值：生命、攻擊、防禦
    entries: List[Dict[str, Any]] = field(default_factory=list)   # 詞條列表
    combo_raw: str = ""                                            # 原始組合
    combo_norm: str = ""                                           # 正規化後組合
    unwanted: bool = False                                         # 是否為不要的组合
    debug_items: List[OCRItem] = field(default_factory=list)       # 除錯資訊


@dataclass
class LampState:
    """神燈狀態"""
    gold_num: Optional[int] = None      # 神燈數量
    last_gold_num: Optional[int] = None # 上次記錄的數量
    ocr_skip_count: int = 0             # 連續 OCR 不完整次數
    no_decrease_streak: int = 0         # 連續使用後神燈數未減少的次數（卡死訊號）
    is_running: bool = False            # 是否正在執行

    def should_stop_for_incomplete_ocr(self, limit: int) -> bool:
        """檢查是否應因 OCR 不完整而停止"""
        return self.ocr_skip_count >= limit

    def record_ocr_success(self):
        """記錄 OCR 成功，重置跳過計數"""
        self.ocr_skip_count = 0

    def record_ocr_incomplete(self) -> int:
        """記錄 OCR 不完整，回傳新的計數"""
        self.ocr_skip_count += 1
        return self.ocr_skip_count

    def record_lamp_decrease(self) -> None:
        """神燈數有減少（健康），重置 stuck-streak。"""
        self.no_decrease_streak = 0

    def record_lamp_no_change(self) -> int:
        """神燈數未減少，streak +1，回傳新值。"""
        self.no_decrease_streak += 1
        return self.no_decrease_streak

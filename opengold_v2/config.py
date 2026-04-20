"""
配置管理 - 集中管理所有可配置參數
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Set, Dict, FrozenSet
import json
import os


@dataclass
class OpenGoldConfig:
    """神燈開裝備配置"""
    
    # ========== OCR 不完整處理配置 ==========
    # 遇到 OCR 不完整時，最多跳過次數 (達上限則停止整個開裝流程)
    skip_incomplete_limit: int = 3
    
    # ========== 截圖記錄配置 ==========
    # 截圖記錄資料夾
    screenshot_folder: str = 'opengold_v2/screenshots'
    # 截圖記錄上限
    screenshot_max_files: int = 100
    
    # ========== 像素比對容差 ==========
    lamp_pixel_sum_tolerance: int = 12
    
    # ========== 比較邏輯閾值 ==========
    # 回復門檻 (用於特殊判斷)
    recovery_threshold: float = 0.25
    # 擊暈門檻
    stun_threshold: float = 3.0
    # 技能暴擊門檻
    skill_crit_threshold: float = 3.6
    # 比較浮點誤差
    eps: float = 1e-6
    
    # ========== ROI 座標配置 ==========
    # 開出詞條 ROI (相對座標，適用所有裝置)
    rolled_roi_coords: List[Tuple[int, int, int, int]] = field(default_factory=lambda: [
        (645, 675, 295, 439),   # 第一個詞條 (y1, y2, x1, x2)
        (696, 724, 295, 439),   # 第二個詞條
    ])
    
    # 原有詞條 ROI - 手機
    orig_roi_phone: List[Tuple[int, int, int, int]] = field(default_factory=lambda: [
        (400, 430, 292, 439),
        (450, 480, 292, 439),
    ])
    
    # 原有詞條 ROI - 電腦
    orig_roi_computer: List[Tuple[int, int, int, int]] = field(default_factory=lambda: [
        (420, 450, 292, 439),
        (460, 490, 292, 439),
    ])
    
    # 神燈數量顯示區域
    gold_num_roi: Tuple[int, int, int, int] = (802, 821, 210, 335)
    
    # 技能資訊區域
    skill_roi: Tuple[int, int, int, int] = (634, 744, 291, 367)
    
    # 階段列表區域
    stage_roi: Tuple[int, int, int, int] = (328, 938, 147, 371)
    
    # 重新識別階段區域 (較小)
    stage_roi_recheck: Tuple[int, int, int, int] = (328, 832, 147, 371)
    
    # ========== 像素配置檔案 ==========
    # 全部出售頁面像素特徵
    lamp_sell_page_profiles: Tuple = (
        (
            ((211, 585), (58, 65, 198)),
            ((332, 585), (58, 65, 198)),
        ),
        (
            ((211, 585), (58, 65, 198)),
            ((332, 573), (58, 65, 197)),
        ),
    )
    
    # 神燈就緒頁面像素特徵
    lamp_ready_page_profiles: Tuple = (
        (
            ((121, 700), (178, 209, 218)),
            ((408, 795), (42, 155, 111)),
            ((217, 790), (58, 65, 198)),
        ),
        (
            ((121, 700), (90, 111, 132)),
            ((408, 795), (32, 32, 43)),
            ((217, 790), (132, 104, 99)),
        ),
    )
    
    # 確認按鈕像素特徵
    confirm_button_pixels: List[Tuple[Tuple[int, int], Tuple[int, int, int]]] = field(
        default_factory=lambda: [
            ((221, 554), (58, 65, 198)),   # 左側按鈕
            ((424, 550), (42, 154, 112)),  # 右側按鈕
        ]
    )
    
    # ========== 技能相關配置 ==========
    # 不要的技能組合（使用 frozenset 做「無順序」判斷）
    unwanted_combos: Set[FrozenSet[str]] = field(default_factory=lambda: {
        frozenset(('技', '反')), frozenset(('技', '連')), frozenset(('技', '爆')), frozenset(('技', '閃')),
        frozenset(('連', '暈')), frozenset(('連', '反')), frozenset(('連', '回')),
        frozenset(('暈', '閃')), frozenset(('暈', '爆')), frozenset(('暈', '反')),
        frozenset(('反', '閃')), frozenset(('爆', '回')), frozenset(('爆', '暈'))
    })
    
    # CAP7 技能集合（上限 7% 的詞條）
    cap7_skills: Set[str] = field(default_factory=lambda: {'閃', '連', '爆', '反'})
    
    # 連閃裝備關鍵字（用於自動偵測）
    lian_shan_keywords: List[str] = field(default_factory=lambda: ['連閃', '爆閃'])
    
    # ========== OCR 文字替換規則 ==========
    replacements: List[Tuple[str, str]] = field(default_factory=lambda: [
        (" ", ""),
        ("\n", ""),
        ("\t", ""),
        ("攻擎", "攻擊"),
        ("擎量", "擊暈"),
        ("眩量", "暈眩"),
        ("擊量", "擊暈"),
        ("普攻使方", "普攻使敵方"),
        ("额外", "額外"),
        ("额", "額"),
        ("永恒", "永恆"),
        ("暴馨", "暴擊"),
        ("暴撃", "暴擊"),
        ("技能爆擊", "技能暴擊"),
        ("爆擊", "暴擊"),
        ("连撃", "連擊"),
        ("連撃", "連擊"),
        ("回复", "回復"),
        ("撃", "擊"),
        ("學", "擊"),
        ("舉", "擊"),
        ("量", "暈"),
    ])
    
    # ========== 詞條字典 ==========
    affix_dict: Dict[str, Dict] = field(default_factory=lambda: {
        "技能暴擊": {"code": "技", "aliases": ["技能暴擊", "技能爆擊", "技暴", "技能暴"]},
        "反擊":     {"code": "反", "aliases": ["反擊", "反"]},
        "暴擊":     {"code": "爆", "aliases": ["暴擊", "爆擊", "暴", "爆"]},
        "連擊":     {"code": "連", "aliases": ["連擊", "連"]},
        "擊暈":     {"code": "暈", "aliases": ["擊暈", "暈眩", "眩暈", "暈", "眩"]},
        "閃避":     {"code": "閃", "aliases": ["閃避", "閃"]},
        "回復":     {"code": "回", "aliases": ["回復", "回血", "回"]},
    })
    
    # ========== Combo 正規化 ==========
    canonical_pair: Dict[FrozenSet[str], str] = field(default_factory=lambda: {
        frozenset({"閃", "爆"}): "連閃",
        frozenset({"連", "暈"}): "連暈",
        frozenset({"技", "回"}): "技回",
        frozenset({"反", "爆"}): "反爆",
    })
    
    pair_rewrite: Dict[str, str] = field(default_factory=lambda: {
        '爆閃': '連閃', '閃閃': '連閃', '閃爆': '連閃', '閃連': '連閃',
        '爆連': '連爆', '回技': '技回', '回閃': '閃回', '爆反': '反爆',
        '回暈': '暈回', '回反': '反回', '暈技': '技暈'
    })
    
    # ========== 階段名稱正規化 ==========
    stage_replacements: List[Tuple[str, str]] = field(default_factory=lambda: [
        ("罩眩回", "暈回"),
        ("暈眩回", "暈回"),
        ("技暈眩", "技暈"),
    ])
    
    def __post_init__(self):
        """初始化後處理：建立 alias -> code 映射表"""
        self._alias_to_code: List[Tuple[str, str]] = []
        for canon, info in self.affix_dict.items():
            for alias in info["aliases"]:
                self._alias_to_code.append((alias, info["code"]))
        # 長字串優先匹配
        self._alias_to_code.sort(key=lambda x: len(x[0]), reverse=True)
    
    def get_alias_to_code(self) -> List[Tuple[str, str]]:
        """取得 alias 到 code 的映射表"""
        return self._alias_to_code.copy()
    
    def get_roi_for_device(self, device_ip: str = None) -> List[Tuple[int, int, int, int]]:
        """根據裝置 IP 取得對應的原有詞條 ROI"""
        if device_ip and 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp' in device_ip:
            return self.orig_roi_phone
        return self.orig_roi_computer
    
    @classmethod
    def from_file(cls, filepath: str) -> 'OpenGoldConfig':
        """從 JSON 檔案載入配置"""
        if not os.path.exists(filepath):
            return cls()
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 過濾掉不存在的欄位
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        
        return cls(**filtered_data)
    
    def save_to_file(self, filepath: str):
        """儲存配置到 JSON 檔案"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.__dict__, f, ensure_ascii=False, indent=2)

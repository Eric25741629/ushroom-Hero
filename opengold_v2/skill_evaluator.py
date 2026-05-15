"""
技能評估器 - 詞條比較邏輯

保留原有比較規則：
1. 回復優先比較
2. 技能暴擊 vs 擊暈
3. CAP7 總和比較（連閃裝備特殊處理）
4. 逐項比較
"""

from typing import Dict, Optional, Set
from .models import Equipment, ComparisonResult, ComparisonDetail
from .config import OpenGoldConfig


class SkillEvaluator:
    """技能詞條比較評估器"""
    
    def __init__(self, config: Optional[OpenGoldConfig] = None, has_lian_shan_equip: bool = True):
        self.config = config or OpenGoldConfig()
        self.has_lian_shan_equip = has_lian_shan_equip
    
    def _gt(self, a: Optional[float], b: Optional[float]) -> bool:
        """Helper: compare numeric with EPS"""
        return (a is not None and b is not None and (a - b) > self.config.eps) or (a is not None and b is None)
    
    def _cap7_sum(self, skill_map: Dict[str, float]) -> float:
        """計算 CAP7 詞條總和
        
        特殊處理：若擁有連閃裝備，則 `連` 與 `爆` 視為一個整體（相加）
        """
        s = 0.0
        if self.has_lian_shan_equip:
            # 連閃裝備：將 `連` 與 `爆` 視為一個整體（相加）
            lian_shan_sum = float(skill_map.get('連', 0.0)) + float(skill_map.get('爆', 0.0))
            s += lian_shan_sum
            # 加上其他上限7%的詞條
            for k in self.config.cap7_skills:
                if k not in ('連', '爆'):
                    s += float(skill_map.get(k, 0.0))
        else:
            # 普通模式：直接相加所有上限7%的詞條
            for k in self.config.cap7_skills:
                s += float(skill_map.get(k, 0.0))
        return s
    
    def _build_details(
        self, 
        rolled_map: Dict[str, float], 
        orig_map: Dict[str, float]
    ) -> Dict[str, ComparisonDetail]:
        """建立比較詳情"""
        details = {}
        all_skills = set(list(rolled_map.keys()) + list(orig_map.keys()))
        
        for s in all_skills:
            r = rolled_map.get(s)
            o = orig_map.get(s)
            
            detail = ComparisonDetail(
                skill_code=s,
                rolled_prob=r,
                orig_prob=o,
                rolled_unknown=(r is None),
                orig_unknown=(o is None),
            )
            
            # 嘗試轉為 float
            try:
                detail.rolled_prob = None if r is None else float(r)
            except Exception:
                detail.rolled_prob = None
                detail.rolled_unknown = True
            
            try:
                detail.orig_prob = None if o is None else float(o)
            except Exception:
                detail.orig_prob = None
                detail.orig_unknown = True
            
            details[s] = detail
        
        return details
    
    def compare_skill_pairs(
        self, 
        rolled: Equipment, 
        original: Equipment, 
        is_compare: bool = True
    ) -> ComparisonResult:
        """比較兩組詞條，依規則決策是否要換裝
        
        規則摘要：
        1) 若有 `回`（回復）則優先比較回復
        2) 對於 `閃, 連, 爆, 反` 這類上限為 7% 的詞條，把該集合內的數值相加再比較總和
           - 特殊：若擁有連閃裝備（has_lian_shan_equip=True），則將 `連` 與 `爆` 的值相加比較
        3) 若涉及 `技`（技能暴擊）與 `暈`（擊暈），優先看 `技`
        
        參數：
        - is_compare: 是否進行機率比對，False 時強制更換
        
        回傳 ComparisonResult，包含 should_replace, reason, details
        """
        rolled_map = rolled.to_map()
        orig_map = original.to_map()
        
        # 建立 details
        details = self._build_details(rolled_map, orig_map)
        
        # 若不進行比對，強制更換
        if not is_compare:
            for k in details:
                details[k].better = True
            return ComparisonResult(
                should_replace=True,
                reason='跳過機率比對 -> 強制更換',
                details=details
            )
        
        # ========== 規則 1: 回復優先 ==========
        rolled_rec = rolled_map.get('回', 0.0)
        orig_rec = orig_map.get('回', 0.0)
        
        if '回' in rolled_map or '回' in orig_map:
            # 先單純比較回復大小
            if self._gt(rolled_rec, orig_rec):
                for k in details:
                    r = details[k].rolled_prob
                    o = details[k].orig_prob
                    r_val = 0.0 if r is None else float(r)
                    o_val = 0.0 if o is None else float(o)
                    details[k].better = r_val > o_val
                return ComparisonResult(
                    should_replace=True,
                    reason='rolled recovery higher than original',
                    details=details
                )
            
            if self._gt(orig_rec, rolled_rec):
                return ComparisonResult(
                    should_replace=False,
                    reason='original recovery higher than rolled',
                    details=details
                )
            
            # 回復幾乎一樣時，才看另一個詞條
            if abs(rolled_rec - orig_rec) <= self.config.eps:
                def other_skill_sum(m):
                    for k in m:
                        if k != '回':
                            return k, m[k]
                    return None, 0.0
                
                r_skill, r_val = other_skill_sum(rolled_map)
                o_skill, o_val = other_skill_sum(orig_map)
                
                if r_skill and not o_skill:
                    return ComparisonResult(
                        should_replace=True,
                        reason='rolled has non-recovery skill while original lacks it',
                        details=details
                    )
                if not r_skill and o_skill:
                    return ComparisonResult(
                        should_replace=False,
                        reason='original has non-recovery skill while rolled lacks it',
                        details=details
                    )
                # 若兩邊都有第二詞條且回復一樣，就交給後續規則
        
        # ========== 規則 2: 技能暴擊優先於擊暈 ==========
        all_skills = set(list(rolled_map.keys()) + list(orig_map.keys()))
        if '技' in all_skills or '暈' in all_skills:
            tech_r = rolled_map.get('技', 0.0)
            tech_o = orig_map.get('技', 0.0)
            if self._gt(tech_r, tech_o):
                return ComparisonResult(
                    should_replace=True,
                    reason='rolled has higher skill-crit (技)',
                    details=details
                )
            elif tech_r == tech_o and tech_r > 0:
                # equal 技, fallback: compare 暈
                stun_r = rolled_map.get('暈', 0.0)
                stun_o = orig_map.get('暈', 0.0)
                if self._gt(stun_r, stun_o):
                    return ComparisonResult(
                        should_replace=True,
                        reason='技 equal, but rolled has higher 暈',
                        details=details
                    )
        
        # ========== 規則 3: CAP7 總和比較 ==========
        rolled_cap7 = self._cap7_sum(rolled_map)
        orig_cap7 = self._cap7_sum(orig_map)
        
        if rolled_cap7 > orig_cap7 + self.config.eps:
            return ComparisonResult(
                should_replace=True,
                reason=f'rolled cap7 sum {rolled_cap7:.2f} > orig {orig_cap7:.2f}',
                details=details
            )
        elif orig_cap7 > rolled_cap7 + self.config.eps:
            return ComparisonResult(
                should_replace=False,
                reason=f'orig cap7 sum {orig_cap7:.2f} > rolled {rolled_cap7:.2f}',
                details=details
            )
        
        # ========== 規則 4: 逐項比較 ==========
        any_better = False
        for s, p in rolled_map.items():
            o_p = orig_map.get(s, 0.0)
            better = self._gt(p, o_p)
            if s in details:
                details[s].better = better
            if better:
                any_better = True
        
        if any_better:
            return ComparisonResult(
                should_replace=True,
                reason='some rolled skills have higher prob than original',
                details=details
            )
        
        # 若都沒有比較出差異，則不換
        return ComparisonResult(
            should_replace=False,
            reason='no advantage found',
            details=details
        )

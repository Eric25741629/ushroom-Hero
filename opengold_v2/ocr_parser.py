"""
OCR 解析器 - 處理 OCR 結果解析
"""

import re
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
from .models import OCRItem, ParsedOCRResult, Equipment, SkillEntry
from .config import OpenGoldConfig


class OCRParser:
    """OCR 結果解析器"""
    
    def __init__(self, config: Optional[OpenGoldConfig] = None):
        self.config = config or OpenGoldConfig()
        self.alias_to_code = self.config.get_alias_to_code()
    
    def normalize_text(self, text: str) -> str:
        """統一處理 OCR 誤辨（使用 REPLACEMENTS 表做片語替換）"""
        if text is None:
            return ""
        t = str(text)
        for a, b in self.config.replacements:
            t = t.replace(a, b)
        return t
    
    def text_to_skill_code(self, text: str) -> Optional[str]:
        """將文字轉換為技能代碼"""
        t = self.normalize_text(text or "")
        for alias, code in self.alias_to_code:
            if alias and alias in t:
                return code
        return None
    
    def parse_skill_prob(self, text: str) -> Tuple[Optional[str], Optional[float]]:
        """從 OCR 文字中解析技能縮寫與機率
        
        支援樣式：'反擊機率+1.53%'、'反擊 +1.53%'、'反擊1.53%' 等，亦會處理全形％
        如果找不到數字，prob 回傳 None
        """
        if not text:
            return (None, None)
        
        # 統一處理空白與換行
        s = str(text).replace('\u3000', ' ').replace('\r', ' ').replace('\n', ' ').strip()
        s = re.sub(r'\s+', ' ', s)
        
        # 解析技能代號
        skill = self.text_to_skill_code(s)
        
        # 優先找帶有 % 或 全形％ 的數字（取最後一個出現的）
        percents = re.findall(r'([+-]?\d+(?:\.\d+)?)\s*[%％]', s)
        if percents:
            try:
                prob = float(percents[-1])
                return (skill, prob)
            except Exception:
                return (skill, None)
        
        # 若沒有 %，找所有數字並取最後一個
        nums = re.findall(r'([+-]?\d+(?:\.\d+)?)', s)
        if nums:
            try:
                prob = float(nums[-1])
                return (skill, prob)
            except Exception:
                return (skill, None)
        
        return (skill, None)
    
    def normalize_stage_name(self, text: str) -> str:
        """正規化階段名稱（處理常見暈眩相關誤辨）"""
        if not text:
            return ""
        t = str(text)
        for a, b in self.config.stage_replacements:
            t = t.replace(a, b)
        return t
    
    def normalize_server_ocr_results(
        self, 
        raw_ocr_results: List[Any], 
        img_shape: Optional[Tuple] = None
    ) -> List[List[Any]]:
        """將 server 回傳的 ocr_results 統一成舊版格式 [poly, text, score]"""
        normalized = []
        for r in (raw_ocr_results or []):
            try:
                if isinstance(r, dict):
                    text = r.get('text', '')
                    score = float(r.get('score', 0.0) or 0.0)
                    bbox = r.get('bbox', [])
                    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        x1, y1, x2, y2 = bbox
                        poly = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                    else:
                        # fallback: full-image polygon
                        if img_shape is not None and len(img_shape) >= 2:
                            h, w = img_shape[:2]
                            poly = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
                        else:
                            poly = np.array([[0,0],[0,0],[0,0],[0,0]])
                    normalized.append([poly, text, score])
                elif isinstance(r, (list, tuple)) and len(r) >= 2:
                    normalized.append(r)
                else:
                    normalized.append([np.array([[0,0],[0,0],[0,0],[0,0]]), str(r), 0.0])
            except Exception:
                normalized.append([np.array([[0,0],[0,0],[0,0],[0,0]]), '', 0.0])
        return normalized
    
    def extract_panel_and_entries(
        self, 
        ocr_list: List[Dict]
    ) -> Tuple[Dict[str, int], List[Dict], List[OCRItem]]:
        """從 /ocr 回傳的 ocr_results 萃取面板與詞條"""
        
        # 轉換為 OCRItem
        items = [
            OCRItem(
                text=self.normalize_text(x.get("text", "")),
                bbox=x.get("bbox"),
                score=x.get("score", 1.0),
            )
            for x in ocr_list
            if x.get("text") is not None and x.get("bbox") is not None
        ]
        
        def center_y(bbox: Tuple) -> float:
            return (bbox[1] + bbox[3]) / 2
        
        # ---------- A) 面板：生命/攻擊/防禦 ----------
        panel: Dict[str, int] = {}
        
        # (1) 同框：生命056780
        for it in items:
            m = re.search(r"(生命|攻擊|防禦)(\d+)", it.text)
            if m:
                panel[m.group(1)] = int(m.group(2))
        
        # (2) 分框：label + 數字（同列右側最近）
        num_items = [it for it in items if re.fullmatch(r"\d+", it.text)]
        label_items = [it for it in items if it.text in ["生命", "攻擊", "防禦"]]
        
        for lab in label_items:
            if lab.text in panel:
                continue
            candidates = []
            for num in num_items:
                if (
                    abs(center_y(num.bbox) - center_y(lab.bbox)) <= 12
                    and num.bbox[0] >= lab.bbox[2] - 5
                ):
                    dx = num.bbox[0] - lab.bbox[2]
                    candidates.append((dx, num))
            if candidates:
                candidates.sort(key=lambda t: t[0])
                panel[lab.text] = int(candidates[0][1].text)
        
        # ---------- B) 詞條 + % ----------
        entries: List[Dict[str, Optional[float]]] = []
        used_idx = set()
        
        # (1) 同框：技能暴擊3.32% (or 暴擊3%)
        # NB: regex must be `(\d+(?:\.\d+)?)` — the `?` belongs INSIDE the
        # optional decimal-part, not on the outer group. Previously the outer
        # group was optional, which let m.group(2) be None for integer
        # percentages and crashed `float(None)`.
        for i, it in enumerate(items):
            m = re.search(r"(.+?)(\d+(?:\.\d+)?)%", it.text)
            if m and m.group(1) and not re.fullmatch(r"[\d\.]+", m.group(1)):
                entries.append({"詞條": m.group(1), "%": float(m.group(2))})
                used_idx.add(i)
        
        # (2) 分框：詞條一格 + % 一格（同列左側最近）
        def is_term_text(t: str) -> bool:
            if t in ["生命", "攻擊", "防禦"]:
                return False
            if re.fullmatch(r"\d+", t):
                return False
            if re.fullmatch(r"\d+(?:\.\d+)?%", t):
                return False
            if re.search(r"\d", t) and "%" in t:
                return False
            return True
        
        percent_items = [
            (i, it) for i, it in enumerate(items)
            if i not in used_idx and re.fullmatch(r"\d+(?:\.\d+)?%", it.text)
        ]
        
        term_items = [(i, it) for i, it in enumerate(items) if is_term_text(it.text)]
        
        for pi, p in percent_items:
            candidates = []
            for ti, t in term_items:
                if (
                    abs(center_y(t.bbox) - center_y(p.bbox)) <= 12
                    and t.bbox[2] <= p.bbox[0] + 5
                ):
                    dx = p.bbox[0] - t.bbox[2]
                    candidates.append((dx, ti, t))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                _, ti, t = candidates[0]
                entries.append({"詞條": t.text, "%": float(p.text.rstrip("%"))})
                used_idx.add(pi)
                used_idx.add(ti)
            else:
                entries.append({"詞條": None, "%": float(p.text.rstrip("%"))})
                used_idx.add(pi)
        
        # 去重
        seen = set()
        unique_entries: List[Dict[str, Optional[float]]] = []
        for e in entries:
            key = (e.get("詞條"), e.get("%"))
            if key in seen:
                continue
            seen.add(key)
            unique_entries.append(e)
        
        return panel, unique_entries, items
    
    def build_combo_from_entries(self, entries: List[Dict]) -> str:
        """以 entries 的詞條優先產 combo（比掃所有 OCR 更準）"""
        skills: List[str] = []
        for e in entries:
            t = e.get("詞條") or ""
            code = self.text_to_skill_code(t)
            if code and code not in skills:
                skills.append(code)
            if len(skills) == 2:
                break
        return "".join(skills)
    
    def normalize_combo(self, combo: str) -> str:
        """使用 CANONICAL_PAIR + PAIR_REWRITE 做 combo 正規化"""
        if len(combo) != 2:
            return combo
        canon = self.config.canonical_pair.get(frozenset(combo), combo)
        return self.config.pair_rewrite.get(canon, canon)
    
    def is_unwanted_combo(self, combo: str) -> bool:
        """判斷是否為不要的组合（順序無關）"""
        return len(combo) == 2 and frozenset(combo) in self.config.unwanted_combos
    
    def parse_ocr(self, ocr_results: List[Dict]) -> ParsedOCRResult:
        """一次輸出面板 / 詞條 / combo / 是否不要"""
        panel, entries, items = self.extract_panel_and_entries(ocr_results)
        
        combo_raw = self.build_combo_from_entries(entries)
        
        # 若 entries 太少，退回掃全 OCR
        if len(combo_raw) < 2:
            skills: List[str] = []
            for it in items:
                code = self.text_to_skill_code(it.text)
                if code and code not in skills:
                    skills.append(code)
                if len(skills) == 2:
                    break
            combo_raw = "".join(skills)
        
        combo_norm = self.normalize_combo(combo_raw)
        unwanted = self.is_unwanted_combo(combo_norm if len(combo_norm) == 2 else combo_raw)
        
        return ParsedOCRResult(
            panel=panel,
            entries=entries,
            combo_raw=combo_raw,
            combo_norm=combo_norm,
            unwanted=unwanted,
            debug_items=items,
        )
    
    def get_first_text_from_skill_result(self, skill_result: Optional[Dict]) -> str:
        """從 server 回傳中取出第一個 OCR 文字（容錯處理）"""
        try:
            if not skill_result:
                return ''
            
            ocr = skill_result.get('ocr_results') or skill_result.get('ocr_results_raw') or []
            # Defensive: a malformed payload putting a string here would let the
            # normalisation loop iterate per-character and emit bogus 1-char items.
            if not isinstance(ocr, (list, tuple)):
                return ''
            if not ocr:
                return ''

            def has_number(s: str) -> bool:
                return bool(re.search(r'[0-9％%]', s))
            
            def is_number_only(s: str) -> bool:
                return bool(re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?%?', s))
            
            items = []
            for entry in ocr:
                if isinstance(entry, dict):
                    t = str(entry.get('text', '')).strip()
                    bbox = entry.get('bbox', None)
                elif isinstance(entry, (list, tuple)) and len(entry) > 1:
                    t = str(entry[1]).strip()
                    bbox = None
                else:
                    t = str(entry).strip()
                    bbox = None
                if t:
                    items.append({'text': t, 'bbox': bbox})
            
            if not items:
                return ''
            
            # 依 bbox 排序（先上後下，再左到右）
            def sort_key(it):
                b = it.get('bbox')
                if isinstance(b, (list, tuple)) and len(b) == 4:
                    return (b[1] // 10, b[0])
                return (0, 0)
            
            items.sort(key=sort_key)
            
            # 優先：找包含技能的文字 + 同列最近數字
            def center_y(b):
                return (b[1] + b[3]) / 2
            
            skill_items = []
            num_items = []
            for it in items:
                txt = it['text']
                if self.text_to_skill_code(txt):
                    skill_items.append(it)
                if has_number(txt):
                    num_items.append(it)
            
            for s_it in skill_items:
                s_txt = s_it['text']
                s_bbox = s_it.get('bbox')
                if not num_items:
                    continue
                if isinstance(s_bbox, (list, tuple)) and len(s_bbox) == 4:
                    s_cy = center_y(s_bbox)
                    candidates = []
                    for n_it in num_items:
                        n_bbox = n_it.get('bbox')
                        if isinstance(n_bbox, (list, tuple)) and len(n_bbox) == 4:
                            if abs(center_y(n_bbox) - s_cy) <= 12:
                                dx = n_bbox[0] - s_bbox[2]
                                candidates.append((abs(dx), n_it))
                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        return f"{s_txt} {candidates[0][1]['text']}"
                # bbox 不足時，退回簡單拼接
                for n_it in num_items:
                    if is_number_only(n_it['text']):
                        return f"{s_txt} {n_it['text']}"
            
            # 退回原本的簡單拼接策略
            texts = [it['text'] for it in items[:3]]
            first = texts[0]
            if has_number(first) and not self.text_to_skill_code(first):
                for t in texts[1:]:
                    if self.text_to_skill_code(t):
                        return f"{t} {first}"
            if has_number(first):
                return first
            for t in texts[1:]:
                if has_number(t):
                    return f"{first} {t}"
            return first
        
        except Exception:
            return ''

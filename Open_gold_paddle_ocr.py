from sympy import N, det
import device as D
import time
import numpy as np
import uiautomator2 as u2
import winsound
import cv2
import requests
import base64
import json
import argparse
import re
from typing import Optional, List, Dict, Tuple
from img_tools import (
    click_str_by_server,
    analyze_skill_via_http as shared_analyze_skill_via_http,
    analyze_stage_via_server as shared_analyze_stage_via_server,
    get_all_text
)
from config.paths import OCR_FAILS_DIR_STR
from opengold_v2.lamp_loop_state import LampLoopAction, LampLoopState
current_index = 0
OCR_SERVER_URL = "http://100.64.0.7:5001"  # OCR 服務器地址
# 全域預設：是否比對機率
IS_COMPARE_DEFAULT = True
# 是否在 OCR 不完整時保存整張截圖（預設開啟，因為此模組多由外部呼叫）
SAVE_INCOMPLETE = True
# 遇到 OCR 不完整時，最多跳過次數 (達上限則停止整個開裝流程)
SKIP_INCOMPLETE_LIMIT = 3

LAMP_SELL_PAGE_PIXEL_PROFILES = (
    (
        ((211, 585), (58, 65, 198)),
        ((332, 585), (58, 65, 198)),
    ),
    (
        ((211, 585), (58, 65, 198)),
        ((332, 573), (58, 65, 197)),
    ),
)

LAMP_READY_PIXEL_PROFILES = (
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

LAMP_PIXEL_SUM_TOLERANCE = 12

# 神燈剩餘數量 ROI（魔法熔爐下方數字，每開一個 -1）
LAMP_COUNT_ROI = (802, 821, 210, 335)


def _safe_int(text):
    """從 OCR 文字取出純數字並轉成 int；無數字則回 None。"""
    if text is None:
        return None
    digits = ''.join(c for c in str(text) if c.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _read_int_from_roi(d, y1, y2, x1, x2):
    """擷取畫面指定 ROI 並用 OCR 解析成 int；任一步失敗回 None。"""
    try:
        img = d.screenshot(format='opencv')
        if img is None:
            return None
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            return None
        texts = get_all_text(roi) or []
        for t in texts:
            n = _safe_int(t)
            if n is not None:
                return n
    except Exception as exc:
        print(f"_read_int_from_roi({y1}:{y2},{x1}:{x2}) 失敗: {exc}")
    return None


def get_remaining_lamp_count(d):
    """讀取畫面下方神燈剩餘數量；失敗回 None。"""
    return _read_int_from_roi(d, *LAMP_COUNT_ROI)


def _navigate_to_lamp_page(d):
    """從主頁面進入神燈頁面（與 open_the_gold 入口一致）。"""
    click_and_wait(d, 447, 801, 2)
    click_and_wait(d, 281, 636, 1)


def _pixel_sum_close(img, x, y, expected_bgr, tolerance=LAMP_PIXEL_SUM_TOLERANCE):
    actual = img[y, x]
    return abs(int(np.sum(actual)) - int(np.sum(expected_bgr))) <= tolerance


def _match_pixel_profile(img, pixel_profile, tolerance=LAMP_PIXEL_SUM_TOLERANCE):
    return all(
        _pixel_sum_close(img, x, y, expected_bgr, tolerance=tolerance)
        for (x, y), expected_bgr in pixel_profile
    )


def is_lamp_sell_page(img, tolerance=LAMP_PIXEL_SUM_TOLERANCE):
    return any(
        _match_pixel_profile(img, profile, tolerance=tolerance)
        for profile in LAMP_SELL_PAGE_PIXEL_PROFILES
    )


def is_lamp_ready_page(img, tolerance=LAMP_PIXEL_SUM_TOLERANCE):
    return any(
        _match_pixel_profile(img, profile, tolerance=tolerance)
        for profile in LAMP_READY_PIXEL_PROFILES
    )

# 不要的技能組合（使用 frozenset 做「無順序」判斷）
UNWANTED_COMBOS = {
    frozenset(('技', '反')), frozenset(('技', '連')), frozenset(('技', '爆')), frozenset(('技', '閃')),
    frozenset(('連', '暈')), frozenset(('連', '反')), frozenset(('連', '回')),
    frozenset(('暈', '閃')), frozenset(('暈', '爆')), frozenset(('暈', '反')),
    frozenset(('反', '閃')), frozenset(('爆', '回')), frozenset(('爆', '暈'))
}

# ===== 新解析用資料表 =====
# A. OCR 常見誤判修正（只做「片語」替換，避免誤傷）
REPLACEMENTS: List[Tuple[str, str]] = [
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

    # 原本 normalize_text 裡的修正也一起搬進來
    ("撃", "擊"),
    ("學", "擊"),
    ("舉", "擊"),
    ("量", "暈"),
]

# B. 詞條字典（canonical -> code + aliases）
AFFIX_DICT: Dict[str, Dict[str, List[str] or str]] = {
    "技能暴擊": {"code": "技", "aliases": ["技能暴擊", "技能爆擊", "技暴", "技能暴"]},
    "反擊":     {"code": "反", "aliases": ["反擊", "反"]},
    "暴擊":     {"code": "爆", "aliases": ["暴擊", "爆擊", "暴", "爆"]},
    "連擊":     {"code": "連", "aliases": ["連擊", "連"]},
    "擊暈":     {"code": "暈", "aliases": ["擊暈", "暈眩", "眩暈", "暈", "眩"]},
    "閃避":     {"code": "閃", "aliases": ["閃避", "閃"]},
    "回復":     {"code": "回", "aliases": ["回復", "回血", "回"]},
}

# C. combo 正規化（用 set 當 key）
CANONICAL_PAIR = {
    frozenset({"閃", "爆"}): "連閃",
    frozenset({"連", "暈"}): "連暈",
    frozenset({"技", "回"}): "技回",
    frozenset({"反", "爆"}): "反爆",
}

# D. 額外字串替換（保留你原本那套規則）
PAIR_REWRITE = {
    '爆閃': '連閃', '閃閃': '連閃', '閃爆': '連閃', '閃連': '連閃',
    '爆連': '連爆', '回技': '技回', '回閃': '閃回', '爆反': '反爆',
    '回暈': '暈回', '回反': '反回', '暈技': '技暈'
}



def encode_image(img):
    """將圖片編碼為 base64"""
    _, buffer = cv2.imencode('.jpg', img)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    return img_base64

def analyze_skill_via_http(img_roi):
    """Use shared OCR routing with fallback across configured servers."""
    try:
        return shared_analyze_skill_via_http(img_roi, OCR_SERVER_URL=None)
    except Exception as e:
        print(f"HTTP 請求失敗: {e}")
        return None


def normalize_server_ocr_results(raw_ocr_results, img_shape=None):
    """
    將 server 回傳的 ocr_results 統一成舊版 client 使用的格式：
    每項為 [poly, text, score]
    - 若元素為 dict（新格式），會用 bbox 生成 poly（若有），並取 text/score
    - 若元素為 list/tuple（舊格式），保持不變
    - 其他情況會退回占位值
    """
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
                    # fallback: full-image polygon if img_shape provided
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

def analyze_stage_via_http(img):
    """Use shared OCR stage routing with fallback across configured servers."""
    try:
        return shared_analyze_stage_via_server(img, OCR_SERVER_URL=None)
    except Exception as e:
        print(f"HTTP 請求失敗: {e}")
        return None


def check_server_health():
    """Check whether any configured OCR server is reachable."""
    try:
        import config_manager

        servers = config_manager.get_ocr_config().get("servers", [])
        for srv in servers:
            try:
                response = requests.get(f"{str(srv).rstrip('/')}/health", timeout=5)
                if response.status_code == 200:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def normalize_text(text: str) -> str:
    """統一處理 OCR 誤辨（使用 REPLACEMENTS 表做片語替換）。"""
    if text is None:
        return ""
    t = str(text)
    for a, b in REPLACEMENTS:
        t = t.replace(a, b)
    return t

# ===== 新解析：從 /ocr 結果直接算出面板 / 詞條 / combo =====

# alias -> code（長字串優先）
ALIAS_TO_CODE: List[Tuple[str, str]] = []
for canon, info in AFFIX_DICT.items():
    for alias in info["aliases"]:
        ALIAS_TO_CODE.append((alias, info["code"]))
ALIAS_TO_CODE.sort(key=lambda x: len(x[0]), reverse=True)


def text_to_skill_code(text: str) -> Optional[str]:
    t = normalize_text(text or "")
    for alias, code in ALIAS_TO_CODE:
        if alias and alias in t:
            return code
    return None

def get_first_text_from_skill_result(skill_result):
    """從 server 回傳中取出第一個 OCR 文字（容錯處理）。"""
    try:
        if not skill_result:
            return ''

        # 優先直接取 server 回傳格式中常見的欄位：
        # skill_result['ocr_results'][0]['text']
        ocr = skill_result.get('ocr_results') or skill_result.get('ocr_results_raw') or []
        if ocr:
            import re

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

            # 依 bbox 排序（仿 server：先上後下，再左到右）
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
                if text_to_skill_code(txt):
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

            # 退回原本的簡單拼接策略（避免數字先出現導致技能缺失）
            texts = [it['text'] for it in items[:3]]
            first = texts[0]
            if has_number(first) and not text_to_skill_code(first):
                for t in texts[1:]:
                    if text_to_skill_code(t):
                        return f"{t} {first}"
            if has_number(first):
                return first
            for t in texts[1:]:
                if has_number(t):
                    return f"{first} {t}"
            return first

        return ''
    except Exception:
        return ''

def parse_skill_prob(text):
    """從 OCR 文字中解析技能縮寫與機率（回傳 (skill_code, prob_float_or_None)）。

    支援樣式類似： '反擊機率+1.53%'、'反擊 +1.53%'、'反擊1.53%' 等，亦會處理全形％。
    如果找不到數字，prob 回傳 None。
    """
    import re
    if not text:
        return (None, None)

    # 把可能的非標準空白或換行統一處理，並壓縮多重空白
    s = str(text).replace('\u3000', ' ').replace('\r', ' ').replace('\n', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    # 先嘗試解析技能代號（改用 alias-based 的 text_to_skill_code）
    skill = text_to_skill_code(s)

    # 優先找帶有 % 或 全形％ 的數字（取最後一個出現的）
    percents = re.findall(r'([+-]?\d+(?:\.\d+)?)\s*[%％]', s)
    if percents:
        try:
            prob = float(percents[-1])
            return (skill, prob)
        except Exception:
            return (skill, None)

    # 若沒有 %，找所有數字並取最後一個（通常機率會在後面）
    nums = re.findall(r'([+-]?\d+(?:\.\d+)?)', s)
    if nums:
        try:
            prob = float(nums[-1])
            return (skill, prob)
        except Exception:
            return (skill, None)

    return (skill, None)

def compare_skill_pairs(rolled, original, is_compare=True, has_lian_shan_equip=True):
    """比較兩組詞條清單（每項為 (skill, prob)），依使用者規則決策是否要換裝。

    規則摘要：
    1) 若有 `回`（回復）則優先比較回復：若開出回復 >= 0.25 且原本 < 0.25，必換；若兩方皆為 0.25 則比較另一個詞條。
    2) 對於 `閃, 連, 爆, 反` 這類上限為 7% 的詞條，對一組詞條會把屬於該集合的數值相加再比較總和。
       - 特殊：若擁有連閃裝備（has_lian_shan_equip=True），則將 `連` 與 `爆` 的值相加比較。
    3) 若涉及 `技`（技能暴擊）與 `暈`（擊暈），優先看 `技`。

    參數：
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合）

    回傳 dict，包含：
    - 'details': 每個技能的 rolled/orig/差異
    - 'replace': 最終是否建議更換（bool）
    - 'reason': 字串說明
    """
    # 參數與常數
    EPS = 1e-6
    RECOVERY_THRESHOLD = 0.25
    STUN_THRESHOLD = 3.0
    SKILL_CRIT_THRESHOLD = 3.6
    CAP7_SET = {'閃', '連', '爆', '反'}

    # 建立映射，未提供機率視為 0.0
    def to_map(pairs):
        m = {}
        for s, p in pairs:
            if s is None:
                continue
            m[s] = max(m.get(s, 0.0), 0.0 if p is None else float(p))
        return m

    orig_map = to_map(original)
    rolled_map = to_map(rolled)

    details = {}
    # collect all skills seen
    all_skills = set(list(orig_map.keys()) + list(rolled_map.keys()))
    for s in all_skills:
        details[s] = {
            'rolled_prob': rolled_map.get(s, None),
            'orig_prob': orig_map.get(s, None),
            'better': None,
            # 新增標記：是否為 unknown（None）
            'rolled_unknown': False,
            'orig_unknown': False,
            # 比較時可能的說明
            'note': ''
        }

    # 正規化 details 中的值，並標記 unknown
    for k in list(details.keys()):
        r = details[k].get('rolled_prob')
        o = details[k].get('orig_prob')
        # 標記 unknown
        details[k]['rolled_unknown'] = (r is None)
        details[k]['orig_unknown'] = (o is None)
        # 嘗試轉為 float，無法轉則保留 None
        try:
            details[k]['rolled_prob'] = None if r is None else float(r)
        except Exception:
            details[k]['rolled_prob'] = None
            details[k]['rolled_unknown'] = True
        try:
            details[k]['orig_prob'] = None if o is None else float(o)
        except Exception:
            details[k]['orig_prob'] = None
            details[k]['orig_unknown'] = True

    # 安全比較 helper：處理 None（避免 TypeError）
    def safe_gt(a, b):
        # 若兩方皆為 None，回傳 False（無法判定更好）
        if a is None and b is None:
            return False
        # 若 a 有數值而 b 為 None，視為 a 較好
        if a is not None and b is None:
            return True
        # 若 a 為 None 而 b 有數值，視為不較好
        if a is None and b is not None:
            return False
        # 否則正常比較
        try:
            return (a - b) > EPS
        except Exception:
            return False

    if not is_compare:
        # 當使用者傳入 is_compare == False 時，視為要求跳過機率比對並強制更換
        # 所有詞條都標記為 better，並返回 replace=True
        for k in details:
            details[k]['better'] = True
        reason = '跳過機率比對 -> 強制更換'
        return {'details': details, 'replace': True, 'reason': reason}

    # Helper: compare numeric with EPS
    def gt(a, b):
        return (a is not None and b is not None and (a - b) > EPS) or (a is not None and b is None)

    # 1) 回復優先（不看 0.25 門檻，只要有回就先比）
    rolled_rec = rolled_map.get('回', 0.0)
    orig_rec = orig_map.get('回', 0.0)
    print(f"回復機率比較: rolled 回={rolled_rec}, original 回={orig_rec}")

    if ('回' in rolled_map) or ('回' in orig_map):
        # 先單純比較回復大小
        if rolled_rec > orig_rec + EPS:
            for k in details:
                r = details[k].get('rolled_prob')
                o = details[k].get('orig_prob')
                try:
                    r_val = 0.0 if r is None else float(r)
                except Exception:
                    r_val = 0.0
                try:
                    o_val = 0.0 if o is None else float(o)
                except Exception:
                    o_val = 0.0
                details[k]['better'] = r_val > o_val
            return {'details': details, 'replace': True, 'reason': 'rolled recovery higher than original'}

        if orig_rec > rolled_rec + EPS:
            return {'details': details, 'replace': False, 'reason': 'original recovery higher than rolled'}

        # 回復幾乎一樣時，才看另一個詞條
        if abs(rolled_rec - orig_rec) <= EPS:
            def other_skill_sum(m):
                for k in m:
                    if k != '回':
                        return k, m[k]
                return None, 0.0

            r_skill, r_val = other_skill_sum(rolled_map)
            o_skill, o_val = other_skill_sum(orig_map)

            if r_skill and not o_skill:
                return {'details': details, 'replace': True, 'reason': 'rolled has non-recovery skill while original lacks it'}
            if not r_skill and o_skill:
                return {'details': details, 'replace': False, 'reason': 'original has non-recovery skill while rolled lacks it'}
            # 若兩邊都有第二詞條且回復一樣，就交給後續規則

    # 2) 技能爆擊優先於擊暈
    if '技' in all_skills or '暈' in all_skills:
        tech_r = rolled_map.get('技', 0.0)
        tech_o = orig_map.get('技', 0.0)
        if gt(tech_r, tech_o):
            return {'details': details, 'replace': True, 'reason': 'rolled has higher skill-crit (技)'}
        elif tech_r == tech_o and tech_r > 0:
            # equal 技, fallback: compare 暈
            stun_r = rolled_map.get('暈', 0.0)
            stun_o = orig_map.get('暈', 0.0)
            if gt(stun_r, stun_o):
                return {'details': details, 'replace': True, 'reason': '技 equal, but rolled has higher 暈'}

    # 3) 對於 CAP7_SET，把該集合內的數值相加比較
    # 特殊處理：若擁有連閃裝備，則 `連` 與 `爆` 視為一個整體（相加）
    def cap7_sum(m):
        s = 0.0
        if has_lian_shan_equip:
            # 連閃裝備：將 `連` 與 `爆` 視為一個整體（相加）
            lian_shan_sum = float(m.get('連', 0.0)) + float(m.get('爆', 0.0))
            s += lian_shan_sum
            # 加上其他上限7%的詞條
            for k in CAP7_SET:
                if k not in ('連', '爆'):
                    s += float(m.get(k, 0.0))
        else:
            # 普通模式：直接相加所有上限7%的詞條
            for k in CAP7_SET:
                s += float(m.get(k, 0.0))
        return s

    rolled_cap7 = cap7_sum(rolled_map)
    orig_cap7 = cap7_sum(orig_map)
    if rolled_cap7 > orig_cap7 + EPS:
        return {'details': details, 'replace': True, 'reason': f'rolled cap7 sum {rolled_cap7} > orig {orig_cap7}'}
    elif orig_cap7 > rolled_cap7 + EPS:
        return {'details': details, 'replace': False, 'reason': f'orig cap7 sum {orig_cap7} > rolled {rolled_cap7}'}

    # 4) 最後退回逐一比較：若任一開出詞條機率 > 原本對應詞條，視為較好
    any_better = False
    for s, p in rolled_map.items():
        o_p = orig_map.get(s, 0.0)
        better = gt(p, o_p)
        details[s]['better'] = better
        if better:
            any_better = True

    if any_better:
        return {'details': details, 'replace': True, 'reason': 'some rolled skills have higher prob than original'}

    # 若都沒有比較出差異，則不換
    return {'details': details, 'replace': False, 'reason': 'no advantage found'}

def get_skill_combo(ocr_results):
    """從 OCR 結果提取技能組合（使用 text_to_skill_code）。"""
    skills = []
    for result in ocr_results:
        text = result[1]
        skill = text_to_skill_code(text)
        if skill and skill not in skills:
            skills.append(skill)
            if len(skills) == 2:
                break

    return ''.join(skills)


def normalize_combo(combo: str) -> str:
    """使用 CANONICAL_PAIR + PAIR_REWRITE 做 combo 正規化。"""
    if len(combo) != 2:
        return combo
    canon = CANONICAL_PAIR.get(frozenset(combo), combo)
    return PAIR_REWRITE.get(canon, canon)

def is_unwanted_combo(combo: str) -> bool:
    """判斷是否為不要的組合（順序無關）。"""
    return len(combo) == 2 and frozenset(combo) in UNWANTED_COMBOS


def extract_panel_and_entries(ocr_list):
    """從 /ocr 回傳的 ocr_results（list[dict]）萃取面板與詞條。"""
    items = [
        {
            "text": normalize_text(x.get("text", "")),
            "bbox": x.get("bbox"),
            "score": x.get("score", 1.0),
        }
        for x in ocr_list
        if x.get("text") is not None and x.get("bbox") is not None
    ]

    def center_y(b):
        return (b[1] + b[3]) / 2

    # ---------- A) 面板：生命/攻擊/防禦 ----------
    panel: Dict[str, int] = {}

    # (1) 同框：生命056780
    for it in items:
        m = re.search(r"(生命|攻擊|防禦)(\d+)", it["text"])
        if m:
            panel[m.group(1)] = int(m.group(2))

    # (2) 分框：label + 數字（同列右側最近）
    num_items = [it for it in items if re.fullmatch(r"\d+", it["text"])]
    label_items = [it for it in items if it["text"] in ["生命", "攻擊", "防禦"]]

    for lab in label_items:
        if lab["text"] in panel:
            continue
        candidates = []
        for num in num_items:
            if (
                abs(center_y(num["bbox"]) - center_y(lab["bbox"])) <= 12
                and num["bbox"][0] >= lab["bbox"][2] - 5
            ):
                dx = num["bbox"][0] - lab["bbox"][2]
                candidates.append((dx, num))
        if candidates:
            candidates.sort(key=lambda t: t[0])
            panel[lab["text"]] = int(candidates[0][1]["text"])

    # ---------- B) 詞條 + % ----------
    entries: List[Dict[str, Optional[float]]] = []
    used_idx = set()

    # (1) 同框：技能暴擊3.32%
    for i, it in enumerate(items):
        m = re.search(r"(.+?)(\d+(?:\.\d+)?)%", it["text"])
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
        (i, it)
        for i, it in enumerate(items)
        if i not in used_idx and re.fullmatch(r"\d+(?:\.\d+)?%", it["text"])
    ]

    term_items = [(i, it) for i, it in enumerate(items) if is_term_text(it["text"])]

    for pi, p in percent_items:
        candidates = []
        for ti, t in term_items:
            if (
                abs(center_y(t["bbox"]) - center_y(p["bbox"])) <= 12
                and t["bbox"][2] <= p["bbox"][0] + 5
            ):
                dx = p["bbox"][0] - t["bbox"][2]
                candidates.append((dx, ti, t))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            _, ti, t = candidates[0]
            entries.append({"詞條": t["text"], "%": float(p["text"].rstrip("%"))})
            used_idx.add(pi)
            used_idx.add(ti)
        else:
            entries.append({"詞條": None, "%": float(p["text"].rstrip("%"))})
            used_idx.add(pi)

    # 去重（不用 pandas，避免多一個依賴）
    seen = set()
    unique_entries: List[Dict[str, Optional[float]]] = []
    for e in entries:
        key = (e.get("詞條"), e.get("%"))
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(e)

    return panel, unique_entries, items


def build_combo_from_entries(entries) -> str:
    """以 entries 的詞條優先產 combo（比掃所有 OCR 更準）。"""
    skills: List[str] = []
    for e in entries:
        t = e.get("詞條") or ""
        code = text_to_skill_code(t)
        if code and code not in skills:
            skills.append(code)
        if len(skills) == 2:
            break
    return "".join(skills)


def parse_ocr(ocr_results):
    """一次輸出面板 / 詞條 / combo / 是否不要。"""
    panel, entries, items = extract_panel_and_entries(ocr_results)

    combo_raw = build_combo_from_entries(entries)
    # 若 entries 太少（例如 % 沒抓到），退回掃全 OCR
    if len(combo_raw) < 2:
        skills: List[str] = []
        for it in items:
            code = text_to_skill_code(it["text"])
            if code and code not in skills:
                skills.append(code)
            if len(skills) == 2:
                break
        combo_raw = "".join(skills)

    combo_norm = normalize_combo(combo_raw)
    unwanted = is_unwanted_combo(combo_norm if len(combo_norm) == 2 else combo_raw)

    return {
        "panel": panel,
        "entries": entries,
        "combo_raw": combo_raw,
        "combo_norm": combo_norm,
        "unwanted": unwanted,
        "debug_items": items,
    }


def normalize_stage_name(text: str) -> str:
    """正規化階段名稱（處理常見暈眩相關誤辨）。"""
    if not text:
        return ""
    t = str(text)
    # 常見誤辨：罩眩回 / 暈眩回 / 技暈眩
    t = t.replace("罩眩回", "暈回")
    t = t.replace("暈眩回", "暈回")
    t = t.replace("技暈眩", "技暈")
    return t

def extract_ocr_results(img_roi, ocr):
    """提取 OCR 結果"""
    try:
        res_list = ocr.predict(input=img_roi)
        if not res_list:
            return []
        
        res_obj = res_list[0]
        texts = res_obj.get("rec_texts", []) or []
        scores = res_obj.get("rec_scores", []) or []
        polys = res_obj.get("rec_polys", None)
        boxes = res_obj.get("rec_boxes", None)
        
        results = []
        for i, text in enumerate(texts):
            score = float(scores[i]) if i < len(scores) else 0.0
            
            # 生成多邊形
            if polys is not None and len(polys) > i:
                poly = np.asarray(polys[i]).reshape(-1, 2)
            elif boxes is not None and len(boxes) > i:
                b = np.asarray(boxes[i]).flatten().astype(int)
                if b.size == 4:
                    x1, y1, x2_or_w, y2_or_h = b.tolist()
                    if x2_or_w > x1 and y2_or_h > y1:
                        x2, y2 = x2_or_w, y2_or_h
                    else:
                        x2, y2 = x1 + x2_or_w, y1 + y2_or_h
                    poly = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                else:
                    continue
            else:
                h, w = img_roi.shape[:2]
                poly = np.array([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]])
            
            results.append([poly, text, score])
            
            # 保存低信心截圖
            if score < 0.8:
                save_low_confidence_screenshot(img_roi, poly, text, score, "general")
        
        return results
    except Exception as e:
        print(f"OCR 處理失敗: {e}")
        return []

def save_low_confidence_screenshot(img, poly, text, score, stage_type):
    """保存低信心度截圖"""
    try:
        import os
        if not os.path.exists(OCR_FAILS_DIR_STR):
            os.makedirs(OCR_FAILS_DIR_STR)
        
        timestamp = int(time.time() * 1000)
        pts = np.asarray(poly, dtype=int)
        x1, y1 = pts.min(axis=0)
        x2, y2 = pts.max(axis=0)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = max(x1 + 1, x2), max(y1 + 1, y2)
        
        if y2 <= y1 or x2 <= x1:
            return
            
        crop = img[y1:y2, x1:x2]
        filename = os.path.join(OCR_FAILS_DIR_STR, f"{stage_type}_low_confidence_{score:.3f}_{timestamp}.jpg")
        cv2.imwrite(filename, crop)
        print(f"低信心截圖: {filename} ('{text}', 信心: {score:.3f})")
    except Exception as e:
        print(f"截圖保存失敗: {e}")


def save_incomplete_screenshot(device_obj, folder='ocr_incomplete', max_files=1000):
    """使用 device 的 screenshot 儲存整張畫面，並限制資料夾檔案數量到 max_files

    會嘗試使用傳入的物件的 `screenshot(format='opencv')`，若不存在則退回 `capture_screenshot()`。
    """
    try:
        import os
        import time

        # 取得 numpy 圖片
        if hasattr(device_obj, 'screenshot'):
            img = device_obj.screenshot(format='opencv')
        else:
            # 一些 wrapper 使用 capture_screenshot()
            img = device_obj.capture_screenshot()

        if img is None:
            print("警告：無法取得截圖 (None)")
            return

        if not os.path.exists(folder):
            os.makedirs(folder)

        # 刪除最舊的檔案直到數量 < max_files
        files = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        if len(files) >= max_files:
            files.sort(key=lambda x: os.path.getmtime(x))
            # 刪除多出來的最舊檔案
            to_del = len(files) - max_files + 1
            for i in range(to_del):
                try:
                    os.remove(files[i])
                except Exception:
                    pass

        timestamp = int(time.time() * 1000)
        filename = os.path.join(folder, f"incomplete_{timestamp}.jpg")
        cv2.imwrite(filename, img)
        print(f"已保存不完整 OCR 截圖: {filename}")
    except Exception as e:
        print(f"保存不完整截圖失敗: {e}")

def click_and_wait(d, x, y, wait_time=1):
    """點擊並等待"""
    d.click(x, y)
    time.sleep(wait_time)


def confirm_if_needed(d, device):
    """若畫面上有確認視窗，就點擊確認按鈕。"""
    img = device.capture_screenshot()
    ok = (
        abs(np.sum(img[554, 221]) - np.sum([58, 65, 198])) <= 10 and
        abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10
    )
    if ok:
        click_and_wait(d, 204, 552, 1)
    return ok


def read_pairs_from_rois(rois):
    """從多個 ROI 讀取 (skill_code, prob) 列表。"""
    pairs = []
    for roi in rois:
        res = analyze_skill_via_http(roi)
        print(f"OCR 原始結果: {res}")
        txt = get_first_text_from_skill_result(res)
        skill_code, prob = parse_skill_prob(txt)
        pairs.append((skill_code, prob))
    return pairs

def open_the_gold(d, times=1000, is_compare=IS_COMPARE_DEFAULT, has_lian_shan_equip=True, device_ip: str = None):
    """自動開裝備主流程。

    參數：
    - times: 執行時間（秒），-1 表示無限
    - is_compare: 是否進行機率比對
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合），
                          若為 True，則在比較詞條時將 `連` 與 `爆` 視為一個整體
    """
    start_time = time.time()
    if times == -1:
        times = float('inf')

    # 檢查服務器連接
    if not check_server_health():
        print("錯誤: 無法連接到 OCR 服務器")
        return

    # 初始點擊：進入神燈頁面
    _navigate_to_lamp_page(d)

    # 純邏輯狀態機（測試覆蓋於 tests/test_lamp_loop_state.py）
    loop_state = LampLoopState(
        stable_threshold=2,
        reengage_after_stable=5,
        unreadable_renav_threshold=8,
    )

    # OCR-不完整跳過計數器（function-local，避免裝置 thread 互相污染）
    ocr_skip_count = 0

    while time.time() - start_time < times:
        device = D.device(d)
        img = device.capture_screenshot()
        if is_lamp_sell_page(img):
            print("當前在全部出售頁面")
            if click_str_by_server(d, "全部出售"):
                return

        lamp_count = get_remaining_lamp_count(d)

        # 偵測技能彈窗（必須真的解析到技能代號才算）
        skill_roi = img[634:744, 291:367]
        skill_result = analyze_skill_via_http(skill_roi)
        ocr_results_raw = []
        has_popup = False
        if skill_result and skill_result.get('success'):
            ocr_results_raw = skill_result.get('ocr_results', []) or []
            if ocr_results_raw and any(
                text_to_skill_code(it.get('text', '') if isinstance(it, dict) else '')
                for it in ocr_results_raw
            ):
                has_popup = True

        action = loop_state.tick(lamp_count=lamp_count, has_popup=has_popup)

        if action == LampLoopAction.RENAVIGATE:
            print(
                f"神燈剩餘讀不到已連續 {loop_state.unreadable_renav_threshold} 次，重新導航"
            )
            try:
                _navigate_to_lamp_page(d)
            except Exception as nav_exc:
                print(f"重新導航失敗: {nav_exc}")
            time.sleep(1.5)
            continue

        if action == LampLoopAction.WAIT:
            # 細分等待時長：讀不到 / 動畫中 / 等穩定門檻
            if lamp_count is None:
                time.sleep(1.5)
            elif loop_state.stable_streak == 0:
                time.sleep(2)
            else:
                time.sleep(1)
            continue

        if action == LampLoopAction.REENGAGE_AUTO:
            print(f"穩定且無彈窗，重新啟用自動模式（lamp_count={lamp_count}）")
            click_and_wait(d, 370, 826, 1)   # 自動按鈕
            click_and_wait(d, 271, 576, 5)   # 開始確認
            continue

        # action == HANDLE_POPUP：穩定且偵測到裝備彈窗
        print(f"神燈剩餘穩定於 {lamp_count}，偵測到裝備彈窗，點擊處理")
        d.click(271, 576)
        time.sleep(5)
        img = device.capture_screenshot()
        skill_roi = img[634:744, 291:367]
        skill_result = analyze_skill_via_http(skill_roi)

        if not skill_result or not skill_result.get('success'):
            continue

        ocr_results_raw = skill_result.get('ocr_results', [])
        parsed = parse_ocr(ocr_results_raw)
        combo = parsed.get('combo_raw', '')
        normalized_combo = parsed.get('combo_norm', combo)
        is_unwanted = parsed.get('unwanted', False)

        # 列印明確的文字結果（提高可讀性）
        texts = [r.get('text', '') for r in ocr_results_raw if isinstance(r, dict)]
        print(f"識別結果 (texts): {texts}")
        print(f"技能組合: {combo} => {normalized_combo} ; 不要? {is_unwanted}")
        
        # 判斷是否需要（改用本地解析結果）
        if is_unwanted:
            print("不需要的組合")
            click_and_wait(d, 227, 798, 1)
            confirm_if_needed(d, device)
            
        else:
            print("需要的組合")
            # 處理需要的組合的邏輯（is_compare 參數會在內部處理機率比較）
            skipped = process_wanted_combo(d, normalized_combo, is_compare=is_compare, has_lian_shan_equip=has_lian_shan_equip, device_ip=device_ip)
            # 若 process_wanted_combo 回傳 True 表示因 OCR 不完整而跳過
            if skipped:
                ocr_skip_count += 1
                print(f"OCR 不完整已跳過次數: {ocr_skip_count}/{SKIP_INCOMPLETE_LIMIT}")
                if ocr_skip_count >= SKIP_INCOMPLETE_LIMIT:
                    # 在中止前先確認神燈是否真的開完了：若剩餘數量 > 0，
                    # 視為 OCR/UI 暫時失常而非任務完成，重新導航到神燈頁面後再嘗試。
                    remaining = get_remaining_lamp_count(d)
                    if remaining is not None and remaining > 0:
                        print(
                            f"警告：連續跳過 {SKIP_INCOMPLETE_LIMIT} 次 OCR 不完整，"
                            f"但神燈剩餘 {remaining} 顆，重置計數並重新導航後繼續。"
                        )
                        ocr_skip_count = 0
                        try:
                            _navigate_to_lamp_page(d)
                        except Exception as nav_exc:
                            print(f"重新導航失敗: {nav_exc}")
                        time.sleep(2)
                        continue
                    print(
                        f"警告：連續跳過 {SKIP_INCOMPLETE_LIMIT} 次 OCR 不完整，"
                        f"剩餘={remaining}，停止開神燈。"
                    )
                    click_and_wait(d, 447, 801, 2)
                    click_and_wait(d, 273, 560, 2)
                    return
                # 否則繼續下一回合（不重置計數）
            else:
                # 若成功處理一次，重置連續跳過計數
                ocr_skip_count = 0
    # 結束清理
    click_and_wait(d, 447, 801, 2)
    click_and_wait(d, 273, 560, 2)

def process_wanted_combo(d, combo, is_compare=IS_COMPARE_DEFAULT, has_lian_shan_equip=True, device_ip: str = None):
    """處理需要的技能組合
    
    參數：
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合）
    """
    click_and_wait(d, 518, 16, 1)
    click_and_wait(d, 419, 720, 3)
    click_and_wait(d, 272, 796, 1)
    click_and_wait(d, 281, 350, 2)
    
    device = D.device(d)
    img = device.capture_screenshot()
    
    # 分析當前階段
    all_stage = img[328:938, 147:371]
    stage_result = analyze_stage_via_http(all_stage)

    if not stage_result :
        print("無法識別階段資訊")
        return
    
    stage_texts = stage_result.get('stage_texts', [])
    stage_texts = [normalize_stage_name(i) for i in stage_texts]
    print(f"正規化後階段: {stage_texts}")
    #去除少於2字的階段
    stage_texts = [text for text in stage_texts if len(text) >= 2]
    # 尋找匹配的階段
    if combo in stage_texts:
        index = stage_texts.index(combo)
        print(f"找到階段 '{combo}' 在索引: {index}")
        
        if index == 0:
            click_and_wait(d, 281, 350, 1)
        else:
            click_and_wait(d, 266, 412 + (index-1) * 49, 1)
        
        # 執行升級流程 (傳遞 is_compare 與 has_lian_shan_equip)
        # execute_upgrade_sequence 會回傳 True 表示因 OCR 不完整而跳過、False 表示正常處理
        skipped = execute_upgrade_sequence(d, index, stage_texts, is_compare=is_compare, has_lian_shan_equip=has_lian_shan_equip, device_ip=device_ip)
        return skipped
    else:
        print(f"未找到階段 '{combo}'")
        print(f"可用階段: {stage_texts}")

def execute_upgrade_sequence(d, index, stage_texts, is_compare=IS_COMPARE_DEFAULT, has_lian_shan_equip=True, device_ip: str = None):
    """執行升級序列
    
    參數：
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合），
                          若為 True，則在比較詞條時將 `連` 與 `爆` 視為一個整體
    """
    click_and_wait(d, 378, 721, 1) #切換按鈕
    click_and_wait(d, 268, 869, 1) #關閉方案選單
    click_and_wait(d, 282, 584, 1) #點開開到裝備
    
    # 在此位置（第 592 行）先比對「開出」(rolled) 與「原有」(original) 的詞條，再決定是否執行換裝
    device = D.device(d)
    img = device.capture_screenshot()
    
    # 讀取開出與原有 ROI（與主程式中使用的座標一致）
    rolled_rois = [
        img[645:675, 295:439],
        img[696:724, 295:439]
    ]
    # 定義兩組原有詞條 ROI（手機 / 電腦）
    orig_rois_for_smart_phone = [
        img[400:430, 292:439],
        img[450:480, 292:439]
    ]
    orig_rois_for_computer = [
        img[420:450, 292:439],
        img[460:490, 292:439]
    ]

    # 根據連線的 device_ip 選擇預設要使用的 ROI
    if device_ip and 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp' in device_ip:
        primary_orig_rois = orig_rois_for_smart_phone
        secondary_orig_rois = orig_rois_for_computer
    else:
        primary_orig_rois = orig_rois_for_computer
        secondary_orig_rois = orig_rois_for_smart_phone

    # 解析 ROI 為 (skill, prob) 列表
    rolled = read_pairs_from_rois(rolled_rois)
    original = read_pairs_from_rois(primary_orig_rois)

    # 若原始解析不完整，嘗試使用另一組 ROI 重試一次
    def _pairs_incomplete(pairs_list):
        if not isinstance(pairs_list, (list, tuple)):
            return True
        if len(pairs_list) < 2:
            return True
        for p in pairs_list:
            if not p or len(p) < 1:
                return True
            skill = p[0]
            if skill is None:
                return True
            if isinstance(skill, str) and skill.strip() == "":
                return True
        return False

    if _pairs_incomplete(original):
        print("原有詞條解析不完整，嘗試使用備用 ROI 重試...")
        alt_original = read_pairs_from_rois(secondary_orig_rois)
        print(f"備用 ROI 解析結果: {alt_original}")
        # 若備用解析較完整則採用備用
        if not _pairs_incomplete(alt_original):
            original = alt_original
            print("採用備用 ROI 的解析結果")
        else:
            print("備用 ROI 也無法完整解析，維持原始結果並交由後續處理")

    # 若原有或開出詞條中的機率明顯不合理 (>10%)，也視為解析錯誤，嘗試備用 ROI
    def _prob_too_large(pairs_list, threshold=10.0):
        try:
            for p in (pairs_list or []):
                if not p or len(p) < 2:
                    continue
                prob = p[1]
                if prob is None:
                    continue
                try:
                    if float(prob) > float(threshold):
                        return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    if _prob_too_large(original):
        print("原有詞條出現超過 10% 的機率，視為 OCR 誤判 -> 嘗試使用備用 ROI 重試...")
        alt_original = read_pairs_from_rois(secondary_orig_rois)
        print(f"備用 ROI 解析結果: {alt_original}")
        if not _pairs_incomplete(alt_original) and not _prob_too_large(alt_original):
            original = alt_original
            print("採用備用 ROI 的解析結果（通過機率合理性檢查）")
        else:
            print("備用 ROI 未通過或仍不完整，維持原始結果並交由後續處理")

    # debug: 印出解析後的詞條 pairs
    print(f"rolled pairs: {rolled}")
    print(f"original pairs: {original}")
    # 若 OCR 未能完整解析（任一詞條為 None 或 空字串，或數量不足 2），
    # 則跳過自動比較，讓使用者自行判斷
    def ocr_incomplete(pairs_list):
        if not isinstance(pairs_list, (list, tuple)):
            return True
        if len(pairs_list) < 2:
            return True
        for p in pairs_list:
            # p 預期為 (skill_code, prob)
            if not p or len(p) < 1:
                return True
            skill = p[0]
            if skill is None:
                return True
            if isinstance(skill, str) and skill.strip() == "":
                return True
        return False

    if ocr_incomplete(rolled) or ocr_incomplete(original):
        print("警告：OCR 無法完整辨識詞條，交由使用者判斷，跳過自動比較")
        # 可以在此加入額外通知或紀錄（例如保存截圖）
        # 若啟用保存不完整結果，則把整張畫面截下來放到指定資料夾（最多保留 1000 張）
        try:
            if SAVE_INCOMPLETE:
                save_incomplete_screenshot(d, folder='ocr_incomplete', max_files=1000)
        except Exception as e:
            print(f"嘗試保存不完整截圖時發生錯誤: {e}")
        # 回傳 True 表示因 OCR 不完整而跳過
        return True

    # 執行既有的比較邏輯（compare_skill_pairs）
    result = compare_skill_pairs(rolled, original, is_compare=is_compare, has_lian_shan_equip=has_lian_shan_equip)
    replace = result.get('replace', False)
    reason = result.get('reason', '')
    print(f"升級前比對結果: replace={replace} ; reason: {reason}")
    
    if replace:
        # 較好 -> 執行換裝（保留原本動作）
        click_and_wait(d, 376, 798, 0.3)
        click_and_wait(d, 227, 798, 1)

        # 檢查並點擊確認
        confirm_if_needed(d, device)
    else:
        # 較差 -> 不換，執行「不需要的組合」分支
        print("不需要的組合")
        click_and_wait(d, 227, 798, 1)

        # 檢查並點擊確認
        confirm_if_needed(d, device)
    
    # 最終完成
    click_and_wait(d, 419, 720, 3)
    click_and_wait(d, 272, 796, 1)
    click_and_wait(d, 281, 350, 1)
    
    # 重新識別階段以確保準確性
    device = D.device(d)
    img = device.capture_screenshot()
    all_stage = img[328:832, 147:371]
    stage_result = analyze_stage_via_http(all_stage)
    # stage_result = [i.get('stage_texts', []) for i in stage_result]
    print(stage_result)
    if stage_result and stage_result.get('success'):
        current_stage_texts = stage_result.get("stage_texts")
        #去除少於2字的階段
        current_stage_texts = [text for text in current_stage_texts if len(text) >= 2]
        # 尋找原始裝備並點擊
        original_combo = stage_texts[0]
        if original_combo in current_stage_texts:
            original_index = current_stage_texts.index(original_combo)
            print(f"切換回原始裝備 '{original_combo}' 在索引: {original_index}")
            print(f"當前階段: {current_stage_texts}")
            if original_index == 0:
                click_and_wait(d, 281, 350, 1)
            else:
                click_y = 412 + (original_index - 1) * 49
                click_and_wait(d, 266, click_y, 1)
        else:
            print(f"警告：未在列表中找到原始裝備 '{original_combo}'，點擊第一個")
            click_and_wait(d, 281, 350, 1)
    else:
        print("警告：無法重新識別階段，點擊第一個")
        click_and_wait(d, 281, 350, 1)
        
    click_and_wait(d, 347, 721, 1)
    click_and_wait(d, 268, 869, 1)
    click_and_wait(d, 441, 805, 1)
    click_and_wait(d, 271, 634, 1)
    time.sleep(3)
    # 回傳 False 表示沒有因 OCR 不完整而跳過
    return False

if __name__ == "__main__":
    # current_device_ip = 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp'
    current_device_ip = '7fe98fc6'
    # current_device_ip = 'emulator-5554'
    # 檢查服務器連接
    if not check_server_health():
        print("錯誤: 請先啟動 OCR 服務器 (python ocr_server.py)")
        exit(1)
    
    # CLI 選項：是否擁有連閃裝備
    parser = argparse.ArgumentParser(description="自動開裝備主程式")
    parser.add_argument('--no-lian-shan', action='store_false', dest='lian_shan', default=True, 
                        help='禁用連閃裝備模式（默認啟用）')
    parser.add_argument('--compare', action='store_true', default=True,
                        help='是否進行機率比對')
    parser.add_argument('--save-incomplete', action='store_true', default=False,
                        help='當 OCR 不完整時保存整個畫面截圖以便後續測試')
    args = parser.parse_args()
    
    has_lian_shan_equip = args.lian_shan
    is_compare = args.compare
    # 是否在 OCR 無法完整解析時保存截圖
    SAVE_INCOMPLETE = args.save_incomplete

    print(f"正在連接到設備: {current_device_ip}")
    print(f"設定: is_compare={is_compare}, has_lian_shan_equip={has_lian_shan_equip}")
    d = u2.connect(current_device_ip)
    img = d.screenshot(format='opencv') 
    if is_lamp_sell_page(img):
        print("當前在全部出售頁面")
        click_str_by_server(d,"全部出售",y_range=(535,600))
         
    
    # print(f"成功連接到設備: {current_device_ip}")
    # img = d.screenshot(format='opencv')
    # print(is_lamp_ready_page(img))
    open_the_gold(d, times=-1, is_compare=is_compare, has_lian_shan_equip=has_lian_shan_equip)

    # orig_rois2_for_computer = [
    #     img[420:450, 292:439],
    #     img[460:490, 292:439]
    # ]
    # cv2.imshow("orig_roi_1", orig_rois_for_smart_phone[0])
    # cv2.imshow("orig_roi_2", orig_rois_for_smart_phone[1])
    # cv2.waitKey(0)
    # # 解析 ROI 為 (skill, prob) 列表
    # rolled = read_pairs_from_rois(  orig_rois_for_smart_phone)
    # rolled_rois = [
    #     img[645:675, 295:439],
    #     img[696:724, 295:439]
    # ]
    # # cv2.waitKey(0)
    # # # 解析 ROI 為 (skill, prob) 列表
    # rolled = read_pairs_from_rois(rolled_rois)
    # original = read_pairs_from_rois(orig_rois_for_smart_phone)
    # print(f"rolled pairs: {rolled}")
    # print(f"original pairs: {original}")
    # gold_num_img =img[802:825,240:317]
    # 提取技能資訊並透過 HTTP 分析
    # gold_num = analyze_skill_via_http(gold_num_img)
    # print(gold_num.get('ocr_results')[0].get('text'))

    # try:
    #     print(f"正在連接到設備: {current_device_ip}")
    #     d = u2.connect(current_device_ip)
    #     print(f"成功連接到設備: {current_device_ip}")
    #     open_the_gold(d, times=-1)
    #     img = d.screenshot(format='opencv')

    #     # 讀取開出來的兩個詞條 ROI
    #     rolled_rois = [
    #         img[645:673, 292:439],
    #         img[696:724, 292:439]
    #     ]

    #     # 讀取原有的兩個詞條 ROI
    #     orig_rois = [
    #         img[415:443, 292:439],
    #         img[466:492, 292:439]
    #     ]

    #     rolled = []
    #     for i, roi in enumerate(rolled_rois, start=1):
    #         res = analyze_skill_via_http(roi)
    #         txt = get_first_text_from_skill_result(res)
    #         skill_code, prob = parse_skill_prob(txt)
    #         print(f"開出第{i}詞條 OCR 原文: '{txt}' -> 技能: {skill_code}, 機率: {prob}")
    #         rolled.append((skill_code, prob))

    #     original = []
    #     for i, roi in enumerate(orig_rois, start=1):
    #         res = analyze_skill_via_http(roi)
    #         txt = get_first_text_from_skill_result(res)
    #         print(f"OCR 原文: '{txt}'")
    #         skill_code, prob = parse_skill_prob(txt)
    #         print(f"原有第{i}詞條 OCR 原文: '{txt}' -> 技能: {skill_code}, 機率: {prob}")
    #         original.append((skill_code, prob))

    #     # 比較（忽略順序，依使用者規則）
    #     result = compare_skill_pairs(rolled, original)
    #     details = result.get('details', {})
    #     print("\n比對結果:")
    #     if details:
    #         for k, v in details.items():
    #             print(f"- 技能: {k} | 開出: {v.get('rolled_prob')} | 原有: {v.get('orig_prob')} | 是否較好: {v.get('better')}")
    #     else:
    #         print("(無詳細比對資料)")

    #     # 最終建議
    #     replace = result.get('replace', False)
    #     reason = result.get('reason', '')
    #     print(f"\n決策: {'換裝' if replace else '不換'} ；原因: {reason}")

    # except Exception as e:
    #     print(f"執行錯誤: {e}")
    # # d = u2.connect(current_device_ip)
    # # img = d.screenshot(format='opencv')
    # # all_stage = img[328:832, 147:371]
    # # stage_result = analyze_stage_via_http(all_stage)
    # # print(stage_result)
    # # # stage_result = [i.get('stage_texts', []) for i in stage_result]
    # # print(stage_result)

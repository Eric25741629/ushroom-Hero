# =====================================================================
# DEPRECATED — V1 神燈實作（monolithic Open_gold_paddle_ocr）
#
# 標準實作已遷至 `opengold_v2/lamp_service.py`（V2，模組化 + OpenGoldConfig）。
# runtime 開神燈一律走 V2（`game_actions/lamp_scheduler.py` 無條件用 LampService），
# 舊的 use_opengold_v2 切換旗標已從 config / 儀表板移除。
# 本檔僅保留作獨立除錯 CLI：`python Open_gold_paddle_ocr.py`。
# 請勿在此加新功能、修 bug 也優先導向 V2；`open_the_gold` 等 V1 流程屬待刪死碼。
# =====================================================================
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
    get_all_text,
    OCRError,
)
import logging as _logging
_lamp_logger = _logging.getLogger(__name__)
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
        try:
            texts = get_all_text(roi) or []
        except OCRError as ocr_exc:
            # OCR 本身壞了 — 不是「畫面沒文字」。記 warning 讓 log 區分。
            _lamp_logger.warning(
                f"[OCR] _read_int_from_roi pipeline error ({y1}:{y2},{x1}:{x2}): {ocr_exc}"
            )
            return None
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
    """Use shared OCR routing with fallback across configured servers.

    Backward-compat wrapper: callers in this file historically treated
    ``None`` as "OCR failed". The shared layer now raises ``OCRError`` for
    that case while ``[]`` means "OCR ok, no text". We catch ``OCRError``
    here so existing callers keep working, but log a warning so it's
    distinguishable from a real empty result in logs.
    """
    try:
        return shared_analyze_skill_via_http(img_roi, OCR_SERVER_URL=None)
    except OCRError as e:
        # OCR pipeline broken — different from "no text on screen".
        _lamp_logger.warning(f"[OCR] analyze_skill_via_http failed (pipeline error): {e}")
        return None
    except Exception as e:
        _lamp_logger.warning(f"[OCR] analyze_skill_via_http unexpected error: {e}")
        return None


def analyze_stage_via_http(img):
    """Use shared OCR stage routing with fallback across configured servers.

    Mirrors ``analyze_skill_via_http`` — ``OCRError`` (pipeline broken)
    is distinguished from empty results via warning log, then converted
    to ``None`` for backward compatibility.
    """
    try:
        return shared_analyze_stage_via_server(img, OCR_SERVER_URL=None)
    except OCRError as e:
        _lamp_logger.warning(f"[OCR] analyze_stage_via_http failed (pipeline error): {e}")
        return None
    except Exception as e:
        _lamp_logger.warning(f"[OCR] analyze_stage_via_http unexpected error: {e}")
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

_SAME_ROW_TOLERANCE = 12  # bbox center_y delta to treat as same OCR row
_BBOX_ROW_BUCKET = 10  # quantise bbox y by this many px when sorting top-to-bottom


def _has_number(s):
    return bool(re.search(r'[0-9％%]', s))


def _is_number_only(s):
    return bool(re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?%?', s))


def _bbox_4_tuple(b):
    """Return b iff it looks like (x0, y0, x1, y1), else None."""
    return b if isinstance(b, (list, tuple)) and len(b) == 4 else None


def _normalize_ocr_entry(entry):
    """Coerce one raw OCR entry into {text, bbox}. dict / [bbox,text,score] /
    bare string are all accepted. Empty text → None to signal 'drop me'."""
    if isinstance(entry, dict):
        t, bbox = str(entry.get('text', '')).strip(), entry.get('bbox')
    elif isinstance(entry, (list, tuple)) and len(entry) > 1:
        t, bbox = str(entry[1]).strip(), None
    else:
        t, bbox = str(entry).strip(), None
    return {'text': t, 'bbox': bbox} if t else None


def _items_sort_key(it):
    """Top-to-bottom, then left-to-right; missing bbox → grouped at the start."""
    b = _bbox_4_tuple(it.get('bbox'))
    return (b[1] // _BBOX_ROW_BUCKET, b[0]) if b else (0, 0)


def _find_skill_with_nearby_number(skill_items, num_items):
    """For each skill item, look for a number on the same OCR row (within
    _SAME_ROW_TOLERANCE px). Returns 'skill number' on first hit, else None."""
    if not num_items:
        return None
    for s_it in skill_items:
        s_txt = s_it['text']
        s_bbox = _bbox_4_tuple(s_it.get('bbox'))
        if s_bbox:
            s_cy = (s_bbox[1] + s_bbox[3]) / 2
            candidates = []
            for n_it in num_items:
                n_bbox = _bbox_4_tuple(n_it.get('bbox'))
                if n_bbox and abs((n_bbox[1] + n_bbox[3]) / 2 - s_cy) <= _SAME_ROW_TOLERANCE:
                    candidates.append((abs(n_bbox[0] - s_bbox[2]), n_it))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return f"{s_txt} {candidates[0][1]['text']}"
        # bbox missing: fall back to any pure-number item
        for n_it in num_items:
            if _is_number_only(n_it['text']):
                return f"{s_txt} {n_it['text']}"
    return None


def _simple_concat_fallback(items):
    """Last-resort string assembly when no skill/number pairing was found.

    Looks at the first 3 items only — the OCR row order put the most likely
    'skill + number' pair near the top. Tries to surface a 'skill number'
    pairing first, falls back to the leading number, then stitches a number
    from later items, finally returns the first text alone.
    """
    texts = [it['text'] for it in items[:3]]
    first = texts[0]
    if _has_number(first) and not text_to_skill_code(first):
        for t in texts[1:]:
            if text_to_skill_code(t):
                return f"{t} {first}"
    if _has_number(first):
        return first
    for t in texts[1:]:
        if _has_number(t):
            return f"{first} {t}"
    return first


def get_first_text_from_skill_result(skill_result):
    """從 server 回傳中取出第一個 OCR 文字（容錯處理）。

    Strategy (in order):
      1) Find a skill item that has a number on the same OCR row → "skill number"
      2) Failing that, simple concat of first 3 items in row order
      3) On any unexpected error → empty string (caller treats as 'no detection')
    """
    try:
        if not skill_result:
            return ''
        ocr = skill_result.get('ocr_results') or skill_result.get('ocr_results_raw') or []
        # Defensive: if a malformed payload puts a string here, iterating it
        # would yield single characters which become bogus 1-char "items".
        if not isinstance(ocr, (list, tuple)):
            return ''
        if not ocr:
            return ''

        items = [it for it in (_normalize_ocr_entry(e) for e in ocr) if it is not None]
        if not items:
            return ''

        items.sort(key=_items_sort_key)

        skill_items = [it for it in items if text_to_skill_code(it['text'])]
        num_items = [it for it in items if _has_number(it['text'])]

        primary = _find_skill_with_nearby_number(skill_items, num_items)
        if primary is not None:
            return primary

        return _simple_concat_fallback(items)
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

_COMPARE_EPS = 1e-6
_CAP7_SET = frozenset({'閃', '連', '爆', '反'})


def _gt_with_eps(a, b):
    """a > b within EPS. None is treated as 'no value', smaller than any number."""
    return (a is not None and b is not None and (a - b) > _COMPARE_EPS) or (
        a is not None and b is None
    )


def _pairs_to_skill_map(pairs):
    """[(skill, prob), ...] → {skill: max_prob}. None probs become 0.0; None skills dropped."""
    m = {}
    for s, p in pairs:
        if s is None:
            continue
        m[s] = max(m.get(s, 0.0), 0.0 if p is None else float(p))
    return m


def _build_compare_details(rolled_map, orig_map):
    """Return per-skill {rolled_prob, orig_prob, better, *_unknown, note} dict."""
    details = {}
    for s in set(orig_map) | set(rolled_map):
        r = rolled_map.get(s)
        o = orig_map.get(s)
        details[s] = {
            'rolled_prob': r,
            'orig_prob': o,
            'better': None,
            'rolled_unknown': r is None,
            'orig_unknown': o is None,
            'note': '',
        }
    return details


def _cap7_sum(skill_map, has_lian_shan_equip):
    """Sum the cap-7% skills. Lian-shan equipment merges 連/爆 into one combined value."""
    s = 0.0
    if has_lian_shan_equip:
        s += float(skill_map.get('連', 0.0)) + float(skill_map.get('爆', 0.0))
        s += sum(float(skill_map.get(k, 0.0)) for k in _CAP7_SET if k not in ('連', '爆'))
    else:
        s += sum(float(skill_map.get(k, 0.0)) for k in _CAP7_SET)
    return s


def _mark_all_better_when_recovery_higher(details):
    for k in details:
        r = details[k].get('rolled_prob') or 0.0
        o = details[k].get('orig_prob') or 0.0
        details[k]['better'] = float(r) > float(o)


def _rule_recovery_priority(rolled_map, orig_map, details):
    """Rule 1: 回 (recovery) is dominant. Returns final dict, or None to fall through."""
    if '回' not in rolled_map and '回' not in orig_map:
        return None

    rolled_rec = rolled_map.get('回', 0.0)
    orig_rec = orig_map.get('回', 0.0)
    print(f"回復機率比較: rolled 回={rolled_rec}, original 回={orig_rec}")

    if rolled_rec > orig_rec + _COMPARE_EPS:
        _mark_all_better_when_recovery_higher(details)
        return {'details': details, 'replace': True, 'reason': 'rolled recovery higher than original'}
    if orig_rec > rolled_rec + _COMPARE_EPS:
        return {'details': details, 'replace': False, 'reason': 'original recovery higher than rolled'}

    # Recovery is equal — compare presence of any non-recovery skill.
    if abs(rolled_rec - orig_rec) > _COMPARE_EPS:
        return None

    def _first_non_recovery_skill(m):
        for k in m:
            if k != '回':
                return k
        return None

    r_skill = _first_non_recovery_skill(rolled_map)
    o_skill = _first_non_recovery_skill(orig_map)
    if r_skill and not o_skill:
        return {'details': details, 'replace': True, 'reason': 'rolled has non-recovery skill while original lacks it'}
    if not r_skill and o_skill:
        return {'details': details, 'replace': False, 'reason': 'original has non-recovery skill while rolled lacks it'}
    return None  # Both have non-recovery skills — let later rules decide.


def _rule_skill_crit_over_stun(rolled_map, orig_map, details):
    """Rule 2: 技 (skill-crit) outranks 暈 (stun); on tie use 暈 as tiebreaker."""
    if '技' not in rolled_map and '技' not in orig_map and '暈' not in rolled_map and '暈' not in orig_map:
        return None
    tech_r = rolled_map.get('技', 0.0)
    tech_o = orig_map.get('技', 0.0)
    if _gt_with_eps(tech_r, tech_o):
        return {'details': details, 'replace': True, 'reason': 'rolled has higher skill-crit (技)'}
    if tech_r == tech_o and tech_r > 0:
        if _gt_with_eps(rolled_map.get('暈', 0.0), orig_map.get('暈', 0.0)):
            return {'details': details, 'replace': True, 'reason': '技 equal, but rolled has higher 暈'}
    return None


def _rule_cap7_sum(rolled_map, orig_map, details, has_lian_shan_equip):
    """Rule 3: compare summed cap-7% skills."""
    rolled_cap7 = _cap7_sum(rolled_map, has_lian_shan_equip)
    orig_cap7 = _cap7_sum(orig_map, has_lian_shan_equip)
    if rolled_cap7 > orig_cap7 + _COMPARE_EPS:
        return {'details': details, 'replace': True, 'reason': f'rolled cap7 sum {rolled_cap7} > orig {orig_cap7}'}
    if orig_cap7 > rolled_cap7 + _COMPARE_EPS:
        return {'details': details, 'replace': False, 'reason': f'orig cap7 sum {orig_cap7} > rolled {rolled_cap7}'}
    return None


def _rule_any_skill_better(rolled_map, orig_map, details):
    """Rule 4 (fallback): replace if any single skill is better."""
    any_better = False
    for s, p in rolled_map.items():
        better = _gt_with_eps(p, orig_map.get(s, 0.0))
        details[s]['better'] = better
        if better:
            any_better = True
    if any_better:
        return {'details': details, 'replace': True, 'reason': 'some rolled skills have higher prob than original'}
    return {'details': details, 'replace': False, 'reason': 'no advantage found'}


def compare_skill_pairs(rolled, original, is_compare=True, has_lian_shan_equip=True):
    """比較兩組詞條清單（每項為 (skill, prob)），依使用者規則決策是否要換裝。

    規則順序（找到第一個分勝負的規則就回傳）：
      1) 回 (recovery) 優先：rolled 回 > orig 回 → 換；反之不換。
         若兩方 回 相等：rolled 有非回詞條而 orig 沒 → 換；反之不換。
         其餘交給後續規則。
      2) 技 (skill-crit) 高於 暈 (stun)：技 高就換；技 平則比 暈。
      3) cap-7% 詞條集合 ({閃, 連, 爆, 反}) 總和比較。
         連閃裝備時 `連` 與 `爆` 算同一池。
      4) 逐項比較：任何技能 rolled > orig 就視為較好。

    參數：
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合）
    - is_compare: False 時跳過所有規則，強制 replace=True

    回傳 {'details', 'replace', 'reason'}。
    """
    rolled_map = _pairs_to_skill_map(rolled)
    orig_map = _pairs_to_skill_map(original)
    details = _build_compare_details(rolled_map, orig_map)

    if not is_compare:
        for k in details:
            details[k]['better'] = True
        return {'details': details, 'replace': True, 'reason': '跳過機率比對 -> 強制更換'}

    for rule in (
        lambda: _rule_recovery_priority(rolled_map, orig_map, details),
        lambda: _rule_skill_crit_over_stun(rolled_map, orig_map, details),
        lambda: _rule_cap7_sum(rolled_map, orig_map, details, has_lian_shan_equip),
    ):
        result = rule()
        if result is not None:
            return result

    return _rule_any_skill_better(rolled_map, orig_map, details)

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


_PANEL_LABELS = ("生命", "攻擊", "防禦")
_SAME_ROW_Y_TOLERANCE = 12  # bbox center_y delta to count as the same row
_NEAR_LEFT_X_TOLERANCE = 5  # how far term/label may overlap the value's left edge


def _bbox_center_y(b):
    return (b[1] + b[3]) / 2


def _normalize_ocr_items(ocr_list):
    """Coerce raw OCR dicts to {text, bbox, score} with normalised text."""
    return [
        {
            "text": normalize_text(x.get("text", "")),
            "bbox": x.get("bbox"),
            "score": x.get("score", 1.0),
        }
        for x in ocr_list
        if x.get("text") is not None and x.get("bbox") is not None
    ]


def _extract_panel_attributes(items) -> Dict[str, int]:
    """生命/攻擊/防禦 — 同框 (e.g. '生命56780') 與分框 (label + nearby number)."""
    panel: Dict[str, int] = {}

    # Same-frame: "生命56780"
    for it in items:
        m = re.search(r"(生命|攻擊|防禦)(\d+)", it["text"])
        if m:
            panel[m.group(1)] = int(m.group(2))

    # Split-frame: label box + nearest number box on the same row to the right
    num_items = [it for it in items if re.fullmatch(r"\d+", it["text"])]
    label_items = [it for it in items if it["text"] in _PANEL_LABELS]

    for lab in label_items:
        if lab["text"] in panel:
            continue
        candidates = []
        for num in num_items:
            same_row = abs(_bbox_center_y(num["bbox"]) - _bbox_center_y(lab["bbox"])) <= _SAME_ROW_Y_TOLERANCE
            to_the_right = num["bbox"][0] >= lab["bbox"][2] - _NEAR_LEFT_X_TOLERANCE
            if same_row and to_the_right:
                dx = num["bbox"][0] - lab["bbox"][2]
                candidates.append((dx, num))
        if candidates:
            candidates.sort(key=lambda t: t[0])
            panel[lab["text"]] = int(candidates[0][1]["text"])

    return panel


def _is_term_text(t: str) -> bool:
    """A term is anything that isn't a panel label, pure number, or percentage."""
    if t in _PANEL_LABELS:
        return False
    if re.fullmatch(r"\d+", t):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?%", t):
        return False
    if re.search(r"\d", t) and "%" in t:
        return False
    return True


def _extract_entries(items) -> List[Dict[str, Optional[float]]]:
    """詞條 + % — 同框 (e.g. '技能暴擊3.32%') 與分框 (詞條 + nearby %)."""
    entries: List[Dict[str, Optional[float]]] = []
    used_idx = set()

    # Same-frame: "技能暴擊3.32%"
    for i, it in enumerate(items):
        m = re.search(r"(.+?)(\d+(?:\.\d+)?)%", it["text"])
        if m and m.group(1) and not re.fullmatch(r"[\d\.]+", m.group(1)):
            entries.append({"詞條": m.group(1), "%": float(m.group(2))})
            used_idx.add(i)

    # Split-frame: percent box + nearest term box on the same row to the left
    percent_items = [
        (i, it)
        for i, it in enumerate(items)
        if i not in used_idx and re.fullmatch(r"\d+(?:\.\d+)?%", it["text"])
    ]
    term_items = [(i, it) for i, it in enumerate(items) if _is_term_text(it["text"])]

    for pi, p in percent_items:
        candidates = []
        for ti, t in term_items:
            same_row = abs(_bbox_center_y(t["bbox"]) - _bbox_center_y(p["bbox"])) <= _SAME_ROW_Y_TOLERANCE
            to_the_left = t["bbox"][2] <= p["bbox"][0] + _NEAR_LEFT_X_TOLERANCE
            if same_row and to_the_left:
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

    return entries


def _dedupe_entries(entries):
    """Stable dedupe by (詞條, %) — preserves first-seen order."""
    seen = set()
    unique: List[Dict[str, Optional[float]]] = []
    for e in entries:
        key = (e.get("詞條"), e.get("%"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique


def extract_panel_and_entries(ocr_list):
    """從 /ocr 回傳的 ocr_results（list[dict]）萃取面板與詞條。"""
    items = _normalize_ocr_items(ocr_list)
    panel = _extract_panel_attributes(items)
    entries = _dedupe_entries(_extract_entries(items))
    return panel, entries, items


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

_SKILL_ROI = (slice(634, 744), slice(291, 367))


def _read_skill_popup(img):
    """OCR 神燈彈窗中央的技能 ROI。回傳 (has_popup, ocr_results, raw_result)。

    has_popup 只在 OCR 真的解析到技能代號時才為 True — 避免雜訊誤觸發。
    """
    skill_roi = img[_SKILL_ROI]
    skill_result = analyze_skill_via_http(skill_roi)
    if not (skill_result and skill_result.get('success')):
        return False, [], None
    ocr_results = skill_result.get('ocr_results', []) or []
    has_popup = bool(ocr_results) and any(
        text_to_skill_code(it.get('text', '') if isinstance(it, dict) else '')
        for it in ocr_results
    )
    return has_popup, ocr_results, skill_result


def _adaptive_wait(loop_state, lamp_count) -> None:
    """讀不到 1.5s / 動畫中 2s / 等穩定門檻 1s。"""
    if lamp_count is None:
        time.sleep(1.5)
    elif loop_state.stable_streak == 0:
        time.sleep(2)
    else:
        time.sleep(1)


def _reengage_auto(d) -> None:
    click_and_wait(d, 370, 826, 1)   # 自動按鈕
    click_and_wait(d, 271, 576, 5)   # 開始確認


def _finish_lamp_session(d) -> None:
    click_and_wait(d, 447, 801, 2)
    click_and_wait(d, 273, 560, 2)


def _renav_safely(d, log_prefix: str = "") -> None:
    try:
        _navigate_to_lamp_page(d)
    except Exception as nav_exc:
        print(f"{log_prefix}重新導航失敗: {nav_exc}")


def _handle_skip_overflow(d) -> bool:
    """連續跳過達上限時的決策。回傳 True = 繼續，False = 結束 session。

    若剩餘神燈 > 0 視為暫時 OCR 失常 → 重新導航；否則收尾結束。
    """
    remaining = get_remaining_lamp_count(d)
    if remaining is not None and remaining > 0:
        print(
            f"警告：連續跳過 {SKIP_INCOMPLETE_LIMIT} 次 OCR 不完整，"
            f"但神燈剩餘 {remaining} 顆，重置計數並重新導航後繼續。"
        )
        _renav_safely(d)
        time.sleep(2)
        return True
    print(
        f"警告：連續跳過 {SKIP_INCOMPLETE_LIMIT} 次 OCR 不完整，"
        f"剩餘={remaining}，停止開神燈。"
    )
    return False


def _handle_popup(d, device, is_compare, has_lian_shan_equip, device_ip):
    """處理偵測到的裝備彈窗。回傳 'unwanted' / 'wanted_ok' / 'wanted_skipped' / 'noop'。"""
    print("神燈剩餘穩定，偵測到裝備彈窗，點擊處理")
    d.click(271, 576)
    time.sleep(5)
    img = device.capture_screenshot()
    _, ocr_results_raw, skill_result = _read_skill_popup(img)
    if not skill_result:
        return 'noop'

    parsed = parse_ocr(ocr_results_raw)
    combo = parsed.get('combo_raw', '')
    normalized_combo = parsed.get('combo_norm', combo)
    is_unwanted = parsed.get('unwanted', False)

    texts = [r.get('text', '') for r in ocr_results_raw if isinstance(r, dict)]
    print(f"識別結果 (texts): {texts}")
    print(f"技能組合: {combo} => {normalized_combo} ; 不要? {is_unwanted}")

    if is_unwanted:
        print("不需要的組合")
        click_and_wait(d, 227, 798, 1)
        confirm_if_needed(d, device)
        return 'unwanted'

    print("需要的組合")
    skipped = process_wanted_combo(
        d, normalized_combo,
        is_compare=is_compare,
        has_lian_shan_equip=has_lian_shan_equip,
        device_ip=device_ip,
    )
    return 'wanted_skipped' if skipped else 'wanted_ok'


def open_the_gold(d, times=1000, is_compare=IS_COMPARE_DEFAULT, has_lian_shan_equip=True, device_ip: str = None):
    """自動開裝備主流程。

    參數：
    - times: 執行時間（秒），-1 表示無限
    - is_compare: 是否進行機率比對
    - has_lian_shan_equip: 是否擁有連閃裝備（連閃 & 爆閃組合），
                          若為 True，則在比較詞條時將 `連` 與 `爆` 視為一個整體
    """
    _lamp_logger.warning(
        "[DEPRECATED] Open_gold_paddle_ocr.open_the_gold 已廢棄，請改用 opengold_v2.LampService。"
        " lamp_scheduler 已一律走 V2；此函式僅為相容保留。"
    )
    start_time = time.time()
    if times == -1:
        times = float('inf')

    if not check_server_health():
        print("錯誤: 無法連接到 OCR 服務器")
        return

    _navigate_to_lamp_page(d)

    loop_state = LampLoopState(
        stable_threshold=2,
        reengage_after_stable=5,
        unreadable_renav_threshold=8,
    )
    ocr_skip_count = 0

    while time.time() - start_time < times:
        device = D.device(d)
        img = device.capture_screenshot()
        if is_lamp_sell_page(img):
            print("當前在全部出售頁面")
            if click_str_by_server(d, "全部出售"):
                return

        lamp_count = get_remaining_lamp_count(d)
        has_popup, _, _ = _read_skill_popup(img)
        action = loop_state.tick(lamp_count=lamp_count, has_popup=has_popup)

        if action == LampLoopAction.RENAVIGATE:
            print(f"神燈剩餘讀不到已連續 {loop_state.unreadable_renav_threshold} 次，重新導航")
            _renav_safely(d)
            time.sleep(1.5)
            continue

        if action == LampLoopAction.WAIT:
            _adaptive_wait(loop_state, lamp_count)
            continue

        if action == LampLoopAction.REENGAGE_AUTO:
            print(f"穩定且無彈窗，重新啟用自動模式（lamp_count={lamp_count}）")
            _reengage_auto(d)
            continue

        # action == HANDLE_POPUP
        outcome = _handle_popup(d, device, is_compare, has_lian_shan_equip, device_ip)

        if outcome == 'wanted_skipped':
            ocr_skip_count += 1
            print(f"OCR 不完整已跳過次數: {ocr_skip_count}/{SKIP_INCOMPLETE_LIMIT}")
            if ocr_skip_count >= SKIP_INCOMPLETE_LIMIT:
                if _handle_skip_overflow(d):
                    ocr_skip_count = 0
                    continue
                _finish_lamp_session(d)
                return
        elif outcome == 'wanted_ok':
            ocr_skip_count = 0
        # outcome in ('unwanted', 'noop'): 不動 ocr_skip_count，繼續下一輪

    _finish_lamp_session(d)

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

_FC65396D_PHONE_TAG = 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp'
_PROB_TOO_LARGE_THRESHOLD = 10.0
_ROLLED_ROI_BOXES = [(645, 675, 295, 439), (696, 724, 295, 439)]
_ORIG_ROI_PHONE = [(400, 430, 292, 439), (450, 480, 292, 439)]
_ORIG_ROI_COMPUTER = [(420, 450, 292, 439), (460, 490, 292, 439)]


def _pairs_incomplete(pairs_list):
    """True iff `pairs_list` is missing entries or has any None/empty skill code."""
    if not isinstance(pairs_list, (list, tuple)) or len(pairs_list) < 2:
        return True
    for p in pairs_list:
        if not p or len(p) < 1:
            return True
        skill = p[0]
        if skill is None or (isinstance(skill, str) and skill.strip() == ""):
            return True
    return False


def _pairs_prob_too_large(pairs_list, threshold=_PROB_TOO_LARGE_THRESHOLD):
    """True iff any pair has a numeric prob exceeding `threshold` (i.e. OCR misread)."""
    for p in (pairs_list or []):
        if not p or len(p) < 2 or p[1] is None:
            continue
        try:
            if float(p[1]) > float(threshold):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _slice_rois(img, boxes):
    """Slice [(y0,y1,x0,x1), ...] boxes out of `img`."""
    return [img[y0:y1, x0:x1] for (y0, y1, x0, x1) in boxes]


def _select_orig_roi_pair(img, device_ip):
    """Pick (primary, secondary) original-equipment ROI sets based on device.

    The fc65396d phone places the original-equipment row higher on screen than
    the computer/emulator UI; pick the matching ROI as primary and keep the
    other as fallback for OCR retries.
    """
    if device_ip and _FC65396D_PHONE_TAG in device_ip:
        return _slice_rois(img, _ORIG_ROI_PHONE), _slice_rois(img, _ORIG_ROI_COMPUTER)
    return _slice_rois(img, _ORIG_ROI_COMPUTER), _slice_rois(img, _ORIG_ROI_PHONE)


def _read_original_pairs_with_fallback(primary_rois, secondary_rois):
    """Read original-equipment pairs from primary; fall back to secondary if the
    primary read is incomplete or has a >10% prob (OCR misread)."""
    pairs = read_pairs_from_rois(primary_rois)

    if _pairs_incomplete(pairs):
        print("原有詞條解析不完整，嘗試使用備用 ROI 重試...")
        alt = read_pairs_from_rois(secondary_rois)
        print(f"備用 ROI 解析結果: {alt}")
        if not _pairs_incomplete(alt):
            print("採用備用 ROI 的解析結果")
            pairs = alt
        else:
            print("備用 ROI 也無法完整解析，維持原始結果並交由後續處理")

    if _pairs_prob_too_large(pairs):
        print("原有詞條出現超過 10% 的機率，視為 OCR 誤判 -> 嘗試使用備用 ROI 重試...")
        alt = read_pairs_from_rois(secondary_rois)
        print(f"備用 ROI 解析結果: {alt}")
        if not _pairs_incomplete(alt) and not _pairs_prob_too_large(alt):
            print("採用備用 ROI 的解析結果（通過機率合理性檢查）")
            pairs = alt
        else:
            print("備用 ROI 未通過或仍不完整，維持原始結果並交由後續處理")
    return pairs


def _save_incomplete_if_enabled(d):
    if not SAVE_INCOMPLETE:
        return
    try:
        save_incomplete_screenshot(d, folder='ocr_incomplete', max_files=1000)
    except Exception as e:
        print(f"嘗試保存不完整截圖時發生錯誤: {e}")


def _return_to_original_equipment(d, original_stage_texts):
    """After upgrade decision, navigate back to the original equipment slot."""
    click_and_wait(d, 419, 720, 3)
    click_and_wait(d, 272, 796, 1)
    click_and_wait(d, 281, 350, 1)

    device = D.device(d)
    img = device.capture_screenshot()
    stage_result = analyze_stage_via_http(img[328:832, 147:371])
    print(stage_result)

    if stage_result and stage_result.get('success'):
        current_texts = [t for t in stage_result.get("stage_texts") or [] if len(t) >= 2]
        original_combo = original_stage_texts[0]
        if original_combo in current_texts:
            idx = current_texts.index(original_combo)
            print(f"切換回原始裝備 '{original_combo}' 在索引: {idx}")
            print(f"當前階段: {current_texts}")
            if idx == 0:
                click_and_wait(d, 281, 350, 1)
            else:
                click_and_wait(d, 266, 412 + (idx - 1) * 49, 1)
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


def execute_upgrade_sequence(d, index, stage_texts, is_compare=IS_COMPARE_DEFAULT, has_lian_shan_equip=True, device_ip: str = None):
    """執行升級序列。Returns True iff skipped due to incomplete OCR.

    參數：
    - has_lian_shan_equip: 連閃裝備時 `連` 與 `爆` 詞條算同一池
    """
    click_and_wait(d, 378, 721, 1)  # 切換按鈕
    click_and_wait(d, 268, 869, 1)  # 關閉方案選單
    click_and_wait(d, 282, 584, 1)  # 點開開到裝備

    device = D.device(d)
    img = device.capture_screenshot()

    rolled_rois = _slice_rois(img, _ROLLED_ROI_BOXES)
    primary_orig, secondary_orig = _select_orig_roi_pair(img, device_ip)

    rolled = read_pairs_from_rois(rolled_rois)
    original = _read_original_pairs_with_fallback(primary_orig, secondary_orig)

    print(f"rolled pairs: {rolled}")
    print(f"original pairs: {original}")

    if _pairs_incomplete(rolled) or _pairs_incomplete(original):
        print("警告：OCR 無法完整辨識詞條，交由使用者判斷，跳過自動比較")
        _save_incomplete_if_enabled(d)
        return True

    result = compare_skill_pairs(rolled, original, is_compare=is_compare, has_lian_shan_equip=has_lian_shan_equip)
    replace = result.get('replace', False)
    print(f"升級前比對結果: replace={replace} ; reason: {result.get('reason', '')}")

    if replace:
        click_and_wait(d, 376, 798, 0.3)
        click_and_wait(d, 227, 798, 1)
    else:
        print("不需要的組合")
        click_and_wait(d, 227, 798, 1)
    confirm_if_needed(d, device)

    _return_to_original_equipment(d, stage_texts)
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

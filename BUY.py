from collections import Counter
from opencc import OpenCC
import cv2
import json
import time
import random
import uiautomator2 as u2
from datetime import datetime
from dataclasses import dataclass
import os
from img_tools import *
from tools import click_white

cc = OpenCC('t2s')   # 繁 -> 簡


# 模糊比對（已不使用，保留避免外部引用錯誤）
def fuzzy_match(ocr_text, target_list, min_match_ratio=0.6):
    if not ocr_text:
        return False
    return False


# 僅做繁->簡的等值比對所需的標準化
def _norm(text: str) -> str:
    if text is None:
        return ""
    # 僅做：繁->簡 + 去除首尾空白
    return cc.convert(str(text)).strip()


@dataclass
class CardStatus:
    name: str
    bbox: list
    bbox_rel: list
    sold_out: bool


def buy_items(
    d: u2.Device,
    wanted_items: list,
    retry_num: int = 10,
    stop_str: str = '',
    buy_duplicates: bool = True,
    early_scroll_on_no_match: bool = False,
    crop_height: int = 620,
    debug: bool = False,
    allow_substring_match: bool = True,
    log_ocr: bool = False,
    log_dir: str = "logs",
    confirm_no_match_rounds: int = 3,
    no_scroll: bool = False
):
    wanted_items_s = [_norm(w) for w in wanted_items]
    wanted_set = set(wanted_items_s)
    wanted_counter = Counter(wanted_items_s)

    sold_markers_s = [_norm(x) for x in ['已售罄', '已售', '售罄']]
    SOLD_COMMON_MISREAD = {'已售馨': '已售罄', '售馨': '售罄'}

    def _levenshtein(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        dp = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            prev = dp[0]
            dp[0] = i
            for j, cb in enumerate(b, 1):
                cur = dp[j]
                dp[j] = prev if ca == cb else min(prev + 1, dp[j] + 1, dp[j - 1] + 1)
                prev = cur
        return dp[-1]

    def is_sold_text(txt_norm: str) -> bool:
        if txt_norm in sold_markers_s:
            return True
        mapped = SOLD_COMMON_MISREAD.get(txt_norm)
        if mapped and mapped in sold_markers_s:
            return True
        return any(
            abs(len(marker) - len(txt_norm)) <= 1 and _levenshtein(txt_norm, marker) <= 1
            for marker in sold_markers_s
        )

    def is_same_card_rel(box_rel_a, box_rel_b, x_th=0.05, y_th=0.08, debug_flag=False):
        """
        判定兩個相對 bbox 是否屬於同一張卡片。
        調整：
          - 收窄垂直容差 y_th，避免把畫面其他列的售罄標記誤判為該卡片的售罄。
          - 微調近距離寬鬆條件比例。
          - 提高交集面積門檻以減少誤配。
        """
        try:
            ax1, ay1, ax2, ay2 = box_rel_a
            bx1, by1, bx2, by2 = box_rel_b
        except Exception:
            if debug_flag:
                print(f"[DEBUG] is_same_card_rel: invalid boxes {box_rel_a}, {box_rel_b}")
            return False
    
        acx, acy = (ax1 + ax2) / 2, (ay1 + ay2) / 2
        bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
        dx, dy = abs(acx - bcx), abs(acy - bcy)
    
        # 精準同一格中心判定
        if dx < x_th and dy < y_th:
            return True
        # 次要容差（略微放寬，但不如以前寬）
        if dx < x_th * 1.2 and dy < y_th * 1.5:
            return True
    
        # 使用交集面積比例做最後判斷，門檻調高到 0.12
        inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
        inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
        inter_w, inter_h = max(0.0, inter_x2 - inter_x1), max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter_area
        ratio = (inter_area / union) if union > 0 else 0.0
        if debug_flag:
            print(f"[DEBUG] is_same_card_rel: dx={dx:.3f}, dy={dy:.3f}, iou_like={ratio:.3f}")
        return ratio >= 0.12

    def prepare_log_path():
        if not log_ocr:
            return None
        try:
            ip_id = getattr(d, 'serial', None) or getattr(d, 'udid', None) or 'device'
        except Exception:
            ip_id = 'device'
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"{ip_id}_log.txt")

    def write_ocr_log(path, header, items):
        if not path:
            return
        with open(path, 'a', encoding='utf-8') as f_log:
            f_log.write(header + "\n")
            for line in items:
                f_log.write(line + "\n")

    def capture_cards(batch_idx, log_path):
        img = d.screenshot(format='opencv')[:crop_height, :]
        result = analyze_skill_via_http(img)

        if result == 404:
            return "abort", []

        if not result.get("success"):
            return "retry", []

        ocr_list = result.get('ocr_results', [])
        log_lines = []
        matches, sold_flags = [], []

        for item in ocr_list:
            raw_txt = item.get('text')
            txt_s = _norm(raw_txt)
            matched_direct = txt_s in wanted_set
            matched_sub = False

            if allow_substring_match and not matched_direct:
                for w in wanted_items_s:
                    if w in txt_s and 0 < (len(txt_s) - len(w)) <= 3:
                        matched_sub = True
                        break

            if matched_direct or matched_sub:
                matches.append(item)
                if debug:
                    print(f"[DEBUG] 命中目標: raw='{raw_txt}' norm='{txt_s}' direct={matched_direct} sub={matched_sub}")
            elif debug:
                print(f"[DEBUG] 未命中: raw='{raw_txt}' norm='{txt_s}'")

            sold_flag_bool = is_sold_text(txt_s)
            if sold_flag_bool:
                sold_flags.append(item)
                if debug:
                    print(f"[DEBUG] 售罄標記偵測: '{txt_s}' -> True")

            if log_path:
                rel = item.get('bbox_rel') or [None] * 4
                bbox = item.get('bbox') or [None] * 4
                log_lines.append(
                    f"RAW='{raw_txt}' NORM='{txt_s}' MATCH_DIR={matched_direct} MATCH_SUB={matched_sub} "
                    f"SOLD_FLAG={'Y' if sold_flag_bool else 'N'} BBOX={bbox} REL={rel}"
                )

        if log_path:
            header = f"\n=== OCR batch {batch_idx} @ {datetime.now().isoformat()} (count={len(ocr_list)}) ==="
            write_ocr_log(log_path, header, log_lines)

        cards = []
        for match in matches:
            name = _norm(match.get("text"))
            sold = any(
                is_same_card_rel(match.get("bbox_rel"), sold_item.get("bbox_rel"))
                for sold_item in sold_flags
            )
            cards.append(
                CardStatus(
                    name=name,
                    bbox=match.get("bbox"),
                    bbox_rel=match.get("bbox_rel"),
                    sold_out=sold
                )
            )
        return "ok", cards

    def seen_before(card: CardStatus):
        required = wanted_counter.get(card.name, 0)
        if required:
            if bought_counts[card.name] >= required:
                if debug:
                    print(f"[DEBUG] 跳過已達購買數: {card.name} ({bought_counts[card.name]}/{required})")
                return True
            if skipped_counts[card.name] >= required:
                if debug:
                    print(f"[DEBUG] 跳過已判定售罄: {card.name} ({skipped_counts[card.name]}/{required})")
                return True
        return False

    def handle_card(card: CardStatus):
        if card.sold_out:
            print(f"{card.name} 已售罄 → 跳過")
            skipped_names.append(card.name)
            skipped_counts[card.name] += 1
            return False

        if not card.bbox:
            print(f"{card.name} 缺少 bbox → 跳過")
            skipped_names.append(card.name)
            skipped_counts[card.name] += 1
            return False

        x1, y1, x2, y2 = card.bbox
        buy_x = (x1 + x2) // 2
        buy_y = y2 + 163

        print(f"購買 {card.name} 於 ({buy_x}, {buy_y})")
        d.click(buy_x, buy_y)
        time.sleep(0.5)
        d.click(392 + random.randint(-2, 2), 460 + random.randint(-2, 2))
        time.sleep(0.5)
        d.click(271, 557)
        time.sleep(1)
        d.click(269, 589)

        bought_names.append(card.name)
        bought_counts[card.name] += 1
        bought_cards.append({"name": card.name, "bbox": card.bbox, "bbox_rel": card.bbox_rel})
        return True

    bought_names, skipped_names, missing_items = [], [], []
    bought_cards = []
    bought_counts, skipped_counts = Counter(), Counter()
    log_path = prepare_log_path()

    for retry in range(1, retry_num + 1):
        print(f"第 {retry} 次掃描…")
        no_match_tries = 0

        while True:
            status, cards = capture_cards(retry, log_path)
            if status == "abort":
                break
            if status == "retry":
                time.sleep(1)
                break

            if not cards:
                if early_scroll_on_no_match:
                    print("本頁無任何目標，提前滑動翻頁。")
                    break

                no_match_tries += 1
                if no_match_tries <= max(1, int(confirm_no_match_rounds)):
                    print(f"本頁暫無目標，原地重掃 {no_match_tries}/{confirm_no_match_rounds} …")
                    time.sleep(0.4)
                    continue

                print("本頁確認無目標，開始翻頁。")
                break

            did_purchase = False
            for card in cards:
                if seen_before(card):
                    continue
                if handle_card(card):
                    did_purchase = True
                    no_match_tries = 0
                    break

            if not did_purchase:
                break

        if all(bought_counts[name] + skipped_counts[name] >= count for name, count in wanted_counter.items()):
            break

        if no_scroll:
            print("設定不滑動，結束購買流程。")
            break
        try:
            d.swipe(269, crop_height, 269, 270, 0.2)
        except Exception as e:
            time.sleep(2)

        time.sleep(0.05)
        d.click(267, 515)
        time.sleep(0.3)

    for name, required in wanted_counter.items():
        acquired = bought_counts[name] + skipped_counts[name]
        missing_items.extend([name] * max(0, required - acquired))

    # 檢查是否有 missing 且不在 sold_out 中的項目
    unique_missing = set(missing_items)
    unique_sold_out = set(skipped_names)
    items_to_recheck = unique_missing - unique_sold_out
    
    if items_to_recheck:
        print(f"\n發現未購買且未售罄的項目: {list(items_to_recheck)}")
        print("滾動到最上方重新檢查...")
        
        # 滾動到最上方（多次向上滑動確保到達頂部）
        for _ in range(5):
            try:
                d.swipe(269, 250, 269, crop_height, 0.2)
                time.sleep(0.3)
            except Exception as e:
                print(f"滾動時發生錯誤: {e}")
                time.sleep(1)
        
        # 等待頁面穩定
        time.sleep(1)
        
        # 重新掃描頂部區域
        print("重新掃描頂部區域...")
        status, cards = capture_cards("recheck", log_path)
        
        if status == "ok" and cards:
            for card in cards:
                # 只處理在 items_to_recheck 中的項目
                if card.name in items_to_recheck:
                    if not seen_before(card):
                        if handle_card(card):
                            print(f"重新檢查時成功購買: {card.name}")
                            # 更新 missing_items
                            if card.name in missing_items:
                                missing_items.remove(card.name)

    return {
        "bought": bought_names,
        "sold_out": skipped_names,
        "missing": missing_items,
        "bought_cards": bought_cards,
        "params": {
            "buy_duplicates": buy_duplicates,
            "early_scroll_on_no_match": early_scroll_on_no_match,
            "crop_height": crop_height,
            "allow_substring_match": allow_substring_match,
            "log_ocr": log_ocr,
            "ocr_log_path": log_path,
            "confirm_no_match_rounds": confirm_no_match_rounds,
        }
    }


if __name__ == "__main__":
    import uiautomator2 as u2
    # device = u2.connect('adb-fc65396d-4LPqmI._adb-tls-connect._tcp')  # 連接到默認設備
    # device = u2.connect('7fe98fc6')  # 連接到默認設備
    device = u2.connect('emulator-5554')
    wanted_items = ['絕密打工指南']  # 可購買物品名稱列表

    # buy_items(device, ['神羽幣', '符石阻斷劑','神力水晶','神力水晶','覺醒捲軸','神燈金鑰','鑽石金鑰','焚焰金鑰'])
    buy_items(device, wanted_items, debug=True, log_ocr=True)
    print(buy_items(device, wanted_items, debug=True, log_ocr=True))
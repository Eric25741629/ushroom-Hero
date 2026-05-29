"""偵測「要的品質」神燈掉落 — 封包為主、OCR 為輔。

遊戲開神燈是「自動開 + 自動賣」，只保留最高品質（rarity 11 永恆）。當某批
開到永恆時遊戲會停住（自動賣掉其餘 19、留 1 顆等玩家決定）。舊版只能靠
「神燈數量停滯」這個慢且不準的 timeout 來察覺，才去點開比較頁。

本模組改用 0x0504 神燈掉落 push frame 精確、即時地判斷「這批有沒有開到
要的品質」。0x0504 同時帶 rarity 與詞條(affixes)，所以連「好不好」都能直接
從封包算，不必再靠 OCR 讀畫面。OCR / 數量停滯退居備援。

純邏輯，不碰 device / page / IO；protobuf 解析複用 ``utils.equipment_cache``。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from utils.equipment_cache import parse_equipment_lamp_drops
from utils.web_game_api import EQUIP_AFFIX

# 神燈掉落結果 push frame 的 cmd_id（rx）。
LAMP_DROP_CMD = 0x0504

# 預設「要的品質」門檻 = 11 永恆（遊戲自動賣只留這階）。
WANTED_RARITY_DEFAULT = 11


def filter_high_rarity(
    entries: Iterable[Dict[str, Any]],
    min_rarity: int = WANTED_RARITY_DEFAULT,
) -> List[Dict[str, Any]]:
    """保留 rarity ≥ ``min_rarity`` 的掉落 entry（順序維持輸入順序）。"""
    threshold = int(min_rarity)
    return [e for e in entries if int(e.get("rarity", 0)) >= threshold]


def find_high_rarity_drops(
    body: bytes,
    min_rarity: int = WANTED_RARITY_DEFAULT,
) -> List[Dict[str, Any]]:
    """從單一 0x0504 body 解析出 rarity ≥ 門檻的裝備掉落。"""
    return filter_high_rarity(parse_equipment_lamp_drops(body), min_rarity)


def find_high_rarity_drops_in_frames(
    frames: Iterable[Dict[str, Any]],
    min_rarity: int = WANTED_RARITY_DEFAULT,
) -> List[Dict[str, Any]]:
    """掃過一批擷取到的 WS frame（drain_captured_bodies 的輸出），

    只看 0x0504 frame 的 body，回傳所有 rarity ≥ 門檻的掉落。非 0x0504、
    或缺 body 的 frame 一律忽略。
    """
    out: List[Dict[str, Any]] = []
    for frame in frames:
        if int(frame.get("cmd", 0)) != LAMP_DROP_CMD:
            continue
        body = frame.get("body")
        if not body:
            continue
        out.extend(find_high_rarity_drops(body, min_rarity))
    return out


def format_drop(entry: Dict[str, Any]) -> str:
    """人類可讀的單件掉落描述，例如：``永恆 鞋 [反擊=1.32%] uid=999``。"""
    rarity = entry.get("rarity_name") or f"r{entry.get('rarity')}"
    slot = entry.get("slot_name") or f"s{entry.get('slot')}"
    affixes = entry.get("affixes") or {}
    affix_str = ", ".join(
        f"{EQUIP_AFFIX.get(int(k), f'a{k}')}={int(v) / 100:.2f}%"
        for k, v in affixes.items()
    )
    return f"{rarity} {slot} [{affix_str}] uid={entry.get('uid')}"

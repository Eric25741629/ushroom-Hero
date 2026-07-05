"""坐騎掃描：純 WS 的 encode / parse + 目標權重評分。

用途：以純 WebSocket 協議掃描公會、其他玩家、車位佔用資料，
反推可作為坐騎（車位戰鬥）目標的玩家名單，並依「最近命中 / 公會 / 金幣 / 等級」
給出權重供排序。

本模組為純函式：只做 protobuf body 的組裝與解析，不觸碰任何裝置 /
cv2 / torch，也不開任何 socket。實際送收由上層 WS runner 負責。
"""
from __future__ import annotations

import re

from ws_token import codec

# --- 指令碼（cmd = module*256 + N）------------------------------------------
CMD_GUILD_SEARCH = 7427   # 公會搜尋（分頁列表）
CMD_GUILD_MEMBERS = 7440  # 公會成員列表
CMD_ROLE_OTHERS = 780     # 批次查詢其他玩家屬性
CMD_LOT_INFO = 12801      # 車位（家園）佔用資訊

# 角色屬性 attr_id -> 我們關心的欄位
_ATTR_LEVEL = 1001
_ATTR_SERVER = 1006
_ATTR_POWER = 1020

# CJK 判斷（含擴充 A 區），用於過濾亂碼名稱
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


# --- 名稱解碼工具 -----------------------------------------------------------

def _decode_name(raw: object) -> str | None:
    """把 bytes 欄位當 UTF-8 名稱解碼；解不出或非可見字元一律回 None。

    只保留「可列印」或「含 CJK」的字串，濾掉二進位/亂碼欄位。
    """
    if not isinstance(raw, (bytes, bytearray)):
        return None
    try:
        s = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if s.isprintable() or _CJK.search(s):
        return s
    return None


# --- encoders（組 c2s body）------------------------------------------------

def enc_guild_search(type_: int, page: int, key: str = "") -> bytes:
    """公會搜尋：type(#1) + 關鍵字(#2) + 分頁(#3)。"""
    return codec.pb_uint(1, type_) + codec.pb_str(2, key) + codec.pb_uint(3, page)


def enc_guild_members(guild_id: int) -> bytes:
    """公會成員列表：guild_id(#1)。"""
    return codec.pb_uint(1, guild_id)


def enc_role_others(role_ids: list[int], source: int = 1) -> bytes:
    """批次查詢其他玩家：重複 role_id(#1) + source(#2)。"""
    return b"".join(codec.pb_uint(1, r) for r in role_ids) + codec.pb_uint(2, source)


def enc_lot_info(master_id: int) -> bytes:
    """車位查詢：type(#1)=0 家園車位 + 目標玩家(#2) + reserved(#3)=0。"""
    return codec.pb_uint(1, 0) + codec.pb_uint(2, master_id) + codec.pb_uint(3, 0)


# --- parsers（解 s2c body）-------------------------------------------------

def parse_guild_search(body: bytes) -> dict:
    """解析公會搜尋回應。

    Top: page_num(#3, int)；每個 #4(bytes) 為一個公會條目。
    公會條目子欄位：guild_id(#1) / name(#3) / leader_id(#5) /
    guild_level(#8) / member_num(#10)；缺欄位 -> None。
    """
    page_num: int | None = None
    guilds: list[dict] = []
    for fn, val in codec.walk(body):
        if fn == 3 and isinstance(val, int):
            page_num = val
        elif fn == 4 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(val)
            guilds.append({
                "guild_id": d.get(1),
                "name": _decode_name(d.get(3)),
                "leader_id": d.get(5),
                "member_num": d.get(10),
                "guild_level": d.get(8),
            })
    return {"page_num": page_num, "guilds": guilds}


def parse_guild_members(body: bytes) -> list[dict]:
    """解析公會成員：每個 top #2(bytes) 為一名成員，role_id(#1) / name(#2)。

    role_id 為 0/缺值者跳過。
    """
    members: list[dict] = []
    for fn, val in codec.walk(body):
        if fn == 2 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(val)
            role_id = d.get(1)
            if not role_id:
                continue
            members.append({"role_id": role_id, "name": _decode_name(d.get(2))})
    return members


def parse_role_others(body: bytes) -> dict:
    """解析其他玩家屬性 -> {role_id: {level, server, power}}。

    每個 top #1(bytes) = p_other_role_info：其 #1=role_id，
    #2(bytes)=info_list（p_role_change），內含重複 {1:attr_id, 2:value}，
    取 1001->level / 1006->server / 1020->power。
    """
    out: dict[int, dict] = {}
    for fn, val in codec.walk(body):
        if fn != 1 or not isinstance(val, (bytes, bytearray)):
            continue
        role_id: int | None = None
        info: dict = {}
        for ifn, ival in codec.walk(val):
            if ifn == 1 and isinstance(ival, int):
                role_id = ival
            elif ifn == 2 and isinstance(ival, (bytes, bytearray)):
                for afn, aval in codec.walk(ival):
                    if afn != 1 or not isinstance(aval, (bytes, bytearray)):
                        continue
                    kv = codec.walk_dict(aval)
                    attr_id = kv.get(1)
                    value = kv.get(2)
                    if attr_id == _ATTR_LEVEL:
                        info["level"] = value
                    elif attr_id == _ATTR_SERVER:
                        info["server"] = value
                    elif attr_id == _ATTR_POWER:
                        info["power"] = value
        if role_id:
            out[role_id] = info
    return out


def parse_lot_occupants(body: bytes) -> dict:
    """解析車位佔用 -> {"skin_list": [(id, lev)], "spaces": [...]}。

    Top #7(bytes)=一個車位（role_id==0/缺值視為空位，跳過）：
    pos(#1) / role_id(#2) / start_time(#5) / name(#9)。
    Top #8(bytes)=一個外觀 skin：id(#1) / lev(#2)。
    """
    spaces: list[dict] = []
    skin_list: list[tuple] = []
    for fn, val in codec.walk(body):
        if fn == 7 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(val)
            role_id = d.get(2)
            if not role_id:
                continue
            spaces.append({
                "pos": d.get(1),
                "role_id": role_id,
                "start_time": d.get(5),
                "name": _decode_name(d.get(9)),
            })
        elif fn == 8 and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(val)
            skin_list.append((d.get(1), d.get(2)))
    return {"skin_list": skin_list, "spaces": spaces}


# --- 權重評分 ---------------------------------------------------------------

def weight(p: dict, target_guilds: set[str]) -> float:
    """依候選玩家屬性給出權重（越高越優先當坐騎目標）。

    最近命中(host_hits, 上限 5) 權重最重，其次同公會、金幣、等級。
    """
    host = min(p.get("host_hits", 0) or 0, 5)
    coin = p.get("coin") or 0
    in_guild = 1 if p.get("guild") in target_guilds else 0
    level = p.get("level") or 0
    return 100 * host + 1.0 * coin + 30 * in_guild + 0.2 * level

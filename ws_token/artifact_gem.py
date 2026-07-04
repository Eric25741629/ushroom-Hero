"""神器附魔石 (artifact gem) — 純 WS 讀取 + 分解 over a logged-in WSGameClient.

鏡像 ``ws_token/spirit.py``。artifact_gem 是 module 53
(docs/protocol；protoRoot 已驗證 2026-06-19 via 5554)。c2s/s2c 共用 cmd id。

  artifact_gem_info   13569 (0x3501)  c2s {}  ->  s2c {
      pos_list#1:p_key_value[]  (本帳號為空，**不可**當裝備依據),
      gem_list#2:p_artifact_gem[],
      tab#3:uint32  (current tab),
      tab_list#4:p_artifact_gem_tab_info[]  (裝備映射唯一權威來源) }
  artifact_gem_split  13578 (0x350A)  c2s {cost_list#1:uint64[] repeated}  (批量分解/賣)
                                       ->  s2c {cost_list#1} 列出已分解 id；失敗走 0x0201

  p_artifact_gem {id#1, quality#2, pos#3, suit#4, lv#5, exp#6, is_red#7, is_lock#8,
                  base_attr#9:p_key_value[], rand_attr#10:p_key_value[]}
  p_artifact_gem_tab_info {tab#1, name#2, pos_list#3:p_key_value[]}
  p_key_value {k#1, v#2}

# CRITICAL（live 驗證 5554 2026-06-19）:
#   - ``pos``(#3) 是石頭固有的「鑲嵌位置類型 1-6」，**不是**裝備狀態 — 每顆石都有
#     pos 1-6、沒有 pos==0。真正「已裝備」= 該 gem id 出現在任一 tab 的 pos_list 的
#     v(gem_id) 中。分解防呆**必須**用 :func:`select_safe`（排除 equipped + locked），
#     不可用 pos==0。
#   - 分解(split)成功回**自身 cmd 13578**，失敗才回 0x0201 錯誤頻道，故用 ``call_for``。
#   - cost_list 是 repeated uint64；先用 unpacked（proto2 預設）編碼，若 live 被拒
#     再改 packed（見 :func:`build_split_body`）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- cmd ids (c2s/s2c 同 id；失敗回 0x0201) ----------------------------------
CMD_INFO = 13569          # artifact_gem_info (0x3501; empty c2s body) — 倉庫快照
CMD_SPLIT = 13578         # artifact_gem_split (0x350A) — 批量分解/賣
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}


# --- dataclasses ------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactGem:
    """One p_artifact_gem。``pos`` 是鑲嵌位置類型(1-6)，非裝備狀態。"""

    id: int                       # #1 uint64 instance id
    quality: int                  # #2 (3-8 -> configArtifact_gemquality)
    pos: int                      # #3 鑲嵌位置類型 1-6（固有，非裝備狀態）
    suit: int                     # #4 (101-107 -> configArtifact_gemsets)
    lv: int                       # #5
    exp: int                      # #6
    is_red: bool                  # #7
    is_lock: bool                 # #8
    base_attr: dict[int, int]     # #9 主屬 {attr_id: value}
    rand_attr: dict[int, int]     # #10 隨機詞條 {attr_id: value}


@dataclass(frozen=True)
class ArtifactGemInventory:
    """artifact_gem_info_s2c 快照（唯讀）。"""

    tab: int                              # #3 current tab
    gems: tuple[ArtifactGem, ...]         # #2 gem_list
    equipped_ids: frozenset[int]          # 自 #4 tab_list 算出的已裝備 gem id 集合


# --- parsers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _parse_kv_pairs(body: bytes, field_num: int) -> dict[int, int]:
    """收集 ``field_num`` 上每個 repeated p_key_value {k#1, v#2} -> {k: v}。

    ``walk_dict`` 對 repeated 只留最後一筆，故詞條/槽位這種 repeated 必須顯式 walk。
    """
    out: dict[int, int] = {}
    for fnum, val in codec.walk(body):
        if fnum == field_num and isinstance(val, (bytes, bytearray)):
            d = codec.walk_dict(bytes(val))
            out[_as_int(d.get(1))] = _as_int(d.get(2))
    return out


def _parse_gem(entry: bytes) -> ArtifactGem:
    """One p_artifact_gem -> ArtifactGem。"""
    d = codec.walk_dict(entry)
    return ArtifactGem(
        id=_as_int(d.get(1)),
        quality=_as_int(d.get(2)),
        pos=_as_int(d.get(3)),
        suit=_as_int(d.get(4)),
        lv=_as_int(d.get(5)),
        exp=_as_int(d.get(6)),
        is_red=bool(_as_int(d.get(7))),
        is_lock=bool(_as_int(d.get(8))),
        base_attr=_parse_kv_pairs(entry, 9),
        rand_attr=_parse_kv_pairs(entry, 10),
    )


def _parse_equipped_ids(body: bytes) -> frozenset[int]:
    """自頂層 tab_list#4 算出所有 tab 的已裝備 gem id（pos_list 的 v#2，排除 0）。"""
    equipped: set[int] = set()
    for fnum, val in codec.walk(body):
        if fnum == 4 and isinstance(val, (bytes, bytearray)):  # tab_list entry
            for tfn, tval in codec.walk(bytes(val)):
                if tfn == 3 and isinstance(tval, (bytes, bytearray)):  # pos_list kv
                    gem_id = _as_int(codec.walk_dict(bytes(tval)).get(2))  # v = gem_id
                    if gem_id:
                        equipped.add(gem_id)
    return frozenset(equipped)


def parse_gem_info(body: bytes) -> ArtifactGemInventory:
    """artifact_gem_info_s2c -> 倉庫快照（gems + tab + equipped_ids）。"""
    top = codec.walk_dict(body)
    gems = tuple(
        _parse_gem(bytes(v)) for fnum, v in codec.walk(body)
        if fnum == 2 and isinstance(v, (bytes, bytearray)))
    return ArtifactGemInventory(
        tab=_as_int(top.get(3)),
        gems=gems,
        equipped_ids=_parse_equipped_ids(body),
    )


def read_gem_info(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> ArtifactGemInventory:
    """送 artifact_gem_info (cmd 13569, 空 body)，解析整個倉庫 + 裝備映射。唯讀。"""
    body = client.call(CMD_INFO, b"", timeout=timeout)
    inv = parse_gem_info(body)
    logger.info("ws_token artifact_gem: gem_info %d gem(s) tab=%s equipped=%d",
                len(inv.gems), inv.tab, len(inv.equipped_ids))
    return inv


# --- 分解防呆（server-side 硬 guard，路由與 live 測試共用） ------------------

def select_safe(
    inv: ArtifactGemInventory, ids
) -> tuple[list[int], dict[int, str]]:
    """把請求的 ids 分成 (可安全分解, {被擋 id: 原因})。

    擋掉「已裝備(在 tab_list 內)」「鎖定」「不存在」的 id — 這是賣石的唯一防呆來源，
    避免賣到正在用的石。``ids`` 可為 str 或 int。
    """
    by_id = {g.id: g for g in inv.gems}
    safe: list[int] = []
    blocked: dict[int, str] = {}
    for raw in ids:
        gid = int(raw)
        gem = by_id.get(gid)
        if gem is None:
            blocked[gid] = "not_found"
        elif gid in inv.equipped_ids:
            blocked[gid] = "equipped"
        elif gem.is_lock:
            blocked[gid] = "locked"
        else:
            safe.append(gid)
    return safe, blocked


# --- 分解 (mutate) ----------------------------------------------------------

def build_split_body(ids) -> bytes:
    """artifact_gem_split_c2s {cost_list#1: uint64[] repeated} — 批量 id 陣列。

    先用 **unpacked** 編碼（proto2 預設；schema 為 required/rule 風格）：每個 id
    各自一個 field-1 varint。若 live 被伺服器拒（0x0201 / no-op），改 packed：
    ``codec.pb_msg(1, b"".join(codec.pb_varint(int(i)) for i in ids))``。
    """
    return b"".join(codec.pb_uint(1, int(i)) for i in ids)


def _parse_split_removed(body: bytes) -> list[int]:
    """artifact_gem_split_s2c {cost_list#1} -> 已分解的 id 清單（repeated）。"""
    return [int(v) for fnum, v in codec.walk(body)
            if fnum == 1 and isinstance(v, int)]


def decompose(
    client: WSGameClient, ids, *, timeout: Optional[float] = None
) -> dict:
    """批量分解 ``ids`` (artifact_gem_split 0x350A)。回 {ok, error_code, removed}。

    成功回**自身 cmd 13578**（cost_list 列出已分解 id）；被拒回 0x0201。呼叫端必須
    先用 :func:`select_safe` 濾掉 equipped/locked — 本函式不自行防呆，照送 ``ids``。
    """
    ids = [int(i) for i in ids]
    if not ids:
        return {"ok": False, "error_code": 0, "removed": []}
    reply_cmd, reply = client.call_for(
        CMD_SPLIT, build_split_body(ids),
        expect_cmds=(CMD_SPLIT, CMD_ERROR), timeout=timeout)
    if reply_cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(reply).get(1))
        logger.warning("ws_token artifact_gem: split rejected 0x0201 code=%s (%d ids)",
                       code, len(ids))
        return {"ok": False, "error_code": code, "removed": []}
    removed = _parse_split_removed(reply)
    logger.info("ws_token artifact_gem: split ok requested=%d removed=%d",
                len(ids), len(removed))
    return {"ok": True, "error_code": 0, "removed": removed}

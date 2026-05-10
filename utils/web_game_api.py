"""透過 Playwright + 已登入的 web_h5 session 直接呼叫遊戲協議。

避開 OCR / scene-walking，把 cmd 直接送進 game client 的 WebSocket，
client 端的 SocketClient.IOHandler 會幫我們把 protobuf body 加密 + 加 length
prefix + 發送，回應也會被 client 端解密好交回給我們。

入口
----
    from utils.web_game_api import WebGameAPI
    api = WebGameAPI(page)                         # page = Playwright Page
    is_online = api.is_player_online(target_pid)   # True/False
    raw = api.call_raw(0x0f02, b"\\x08\\x01\\x10\\x01")  # 低階 RPC

協議常數
--------
- `0x0f02` = `friend.friend_list_c2s` / `_s2c`
- request body: `[08 01 10 <tab>]`，tab=1 是好友、tab=2 是申請/陌生人
- response: 含每個 friend 的 player_id / name / last_login_ts

詳細結構見 `tmp_ws_capture/RESEARCH.md` §4.1。
"""
from __future__ import annotations

import struct
import time
from typing import Any, Dict, List, Optional

# cmd_id 對照（從 IOHandler.ts + 實際 capture 確認）
CMD_FRIEND_LIST = 0x0F02
CMD_MINE_BOARD = 0x0C01      # tx empty body → rx full board state
CMD_MINE_DIG = 0x0C03        # tx (prop_id, cell_id) → rx newly revealed cells
CMD_INVENTORY_PUSH = 0x0402  # rx-only push when items/currency change

# 挖礦 prop_id（從 0x0c03 tx + 0x0402 rx 交叉比對, 5554 verified 2026-05-11）
# 每個 prop 用一次 → server push 0x0402 evt=9800001 with new_count = old-1
PROP_PICKAXE = 4001          # 鎬子（單格挖）
PROP_DRILL = 4002            # 鑽頭（垂直 + 底排）
PROP_BOMB = 4003             # 炸彈（3x3 + cross）

# 0x0c01 cell feature f4 enum (terrain type) — 5554 verified 2026-05-11.
#
# IMPORTANT: 0x0c01 features f4 ≠ 0x0c03 dig-response f4. The two cmds use
# the same sub-message shape but different terrain semantics:
#   - 0x0c01 f4 = original terrain type (what player sees on the board)
#   - 0x0c03 f4 = post-dig state (often inverted from the cell's terrain)
# Use these constants for 0x0c01 only.
MINE_TERRAIN_STONE = 201     # 石頭（user-dug 11003501 → "石頭" confirmed）
MINE_TERRAIN_DIRT = 202      # 泥土（user-dug 11003901 → "泥土" confirmed）
MINE_TERRAIN_UNKNOWN_100 = 100  # 罕見 f4=100，user 尚未驗證對應為何
MINE_FEATURE_CAVE = 401      # 礦洞（user-confirmed via cell 11003906）

MINE_TERRAIN_NAME = {
    MINE_TERRAIN_DIRT: "泥土",
    MINE_TERRAIN_STONE: "石頭",
    MINE_TERRAIN_UNKNOWN_100: "未知100",
    MINE_FEATURE_CAVE: "礦洞",
}

# 已確認的 item_id（透過 user-OCR + protocol qty 比對）
ITEM_LAMP = 1001             # 神燈（auto open 一次扣 20，0x0402 evt=1001006 推送）

# 0x0402 evt_type 識別（empirical, 2026-05-11 5554 verification）
EVT_INVENTORY_FULL_SYNC = 9700002  # 完整素材庫存快照（21+ items, 開啟背包時觸發）
EVT_INVENTORY_PARTIAL = 9700003    # 部分異動推送
EVT_ITEM_CONSUME = 9800001         # 單一道具消耗（鎬子使用 → 4001=N）
EVT_ITEM_EVENT = 9800004           # 單一道具事件（claim / drop reward）
EVT_LAMP_DELTA = 1001006           # 神燈消耗推送（item_id=1001, qty -= 20 per auto）
EVT_CURRENCY_DELTA = 5011          # 貨幣異動（gold / 7001 etc.）


# 神燈裝備掉落 schema（0x0504 f3.2 = template_id）— 確認於 2026-05-11
# Encoding:  template_id = rarity * 10_000_000 + slot * 100_000 + item_id
# Examples:
#   60315011 → r=6 (史詩), slot=03 (面飾), item=15011
#   111020001 → r=11 (永恆), slot=10 (鞋), item=20001
EQUIP_RARITY = {
    1: "普通", 2: "優秀", 3: "精良", 4: "稀有", 5: "卓越", 6: "史詩",
    7: "傳奇", 8: "不朽", 9: "超越", 10: "鎏金", 11: "永恆",
}
EQUIP_SLOT = {
    1: "武器", 2: "帽子", 3: "面飾", 4: "肩", 5: "胸",
    6: "臂", 7: "手套", 8: "腰", 9: "護膝", 10: "鞋",
}

# 0x0504 f3.6 = base stats (stat_type_id → value)
# 1003 (攻速) 顯示 ÷10000，其餘是整數。1003 只有武器才有。
EQUIP_STAT = {1001: "攻擊", 1002: "生命", 1003: "攻速", 1024: "防禦"}

# 0x0504 f3.7 = 詞條 (sub-properties), value ÷100 = display %
# 1004/1008/1012/1016/1017/1023 是主動詞條
# 1037 = 技能爆擊
# 4001/4005 是同伴詞條（注意 4001 在挖礦 namespace 是鎬子 PROP_PICKAXE,
# 但在這裡是「同伴爆擊」—— ID 命名空間不同，不要混用）
EQUIP_AFFIX = {
    1004: "爆擊", 1008: "閃避", 1012: "回復", 1016: "連擊",
    1017: "反擊", 1023: "擊暈", 1037: "技能爆擊",
    4001: "同伴爆擊", 4005: "同伴連擊",
}


def decode_equip_template(template_id: int) -> Dict[str, Any]:
    """Split 0x0504 f3.2 template_id into (rarity, slot, item) tuple
    plus their human-readable names where known."""
    rarity = template_id // 10_000_000
    slot = (template_id // 100_000) % 100
    item = template_id % 100_000
    return {
        "rarity": rarity,
        "rarity_name": EQUIP_RARITY.get(rarity, f"r{rarity}"),
        "slot": slot,
        "slot_name": EQUIP_SLOT.get(slot, f"slot{slot}"),
        "item_id": item,
    }


# ── protobuf wire-format walker（minimal）─────────────────────────────────────


def _read_varint(data: bytes, off: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while off < len(data):
        b = data[off]
        off += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, off
        shift += 7
        if shift > 63:
            raise ValueError("varint overflow")
    raise ValueError("varint truncated")


def _walk_pb(data: bytes) -> List[tuple[int, int, Any]]:
    """yield (field_number, wire_type, value) — value type depends on wire_type:
    0=int, 1=int(64), 2=bytes, 5=int(32). Unknown wire types abort."""
    out: List[tuple[int, int, Any]] = []
    off = 0
    while off < len(data):
        tag, off = _read_varint(data, off)
        field = tag >> 3
        wire = tag & 7
        if wire == 0:
            v, off = _read_varint(data, off)
            out.append((field, 0, v))
        elif wire == 1:
            v = struct.unpack_from("<Q", data, off)[0]
            off += 8
            out.append((field, 1, v))
        elif wire == 2:
            length, off = _read_varint(data, off)
            out.append((field, 2, data[off : off + length]))
            off += length
        elif wire == 5:
            v = struct.unpack_from("<I", data, off)[0]
            off += 4
            out.append((field, 5, v))
        else:
            raise ValueError(f"unsupported wire type {wire} at offset {off}")
    return out


# ── friend list parser ───────────────────────────────────────────────────────


def parse_friend_list(body: bytes) -> List[Dict[str, Any]]:
    """從 0x0f02 response body 抽出 friend entries。

    對應 schema（已從 capture 確認，見 RESEARCH.md §4.1）：
        field 1 (varint): result code
        field 2 (varint): tab id
        field 4 (varint): total count
        field 5 (length-delim, REPEATED): friend entries

    每個 entry 的 sub-fields：
        sf 1  = player_id (uint64)
        sf 2  = name (utf-8 string)
        sf 3  = avatar/info sub-message
        sf 7  = last_login_ts (Unix seconds)
        sf 8  = level
        sf 9  = combat_power
        sf 10 = favorability
        sf 13 = region_id (區服)

    沒有 pid 或 name 的 entry 會被略過。
    """
    if not body:
        return []
    fields = _walk_pb(body)
    friends: List[Dict[str, Any]] = []
    for fnum, wire, val in fields:
        if fnum != 5 or wire != 2:
            continue
        sub_fields = _walk_pb(val)
        pid: Optional[int] = None
        name: Optional[str] = None
        last_ts: Optional[int] = None
        level: Optional[int] = None
        combat_power: Optional[int] = None
        favorability: Optional[int] = None
        region_id: Optional[int] = None
        for sf, sw, sv in sub_fields:
            if sw != 0 and sf != 2:
                continue
            if sf == 1 and sw == 0:
                pid = sv
            elif sf == 2 and sw == 2:
                try:
                    name = sv.decode("utf-8")
                except UnicodeDecodeError:
                    name = None
            elif sf == 7 and sw == 0:
                last_ts = sv
            elif sf == 8 and sw == 0:
                level = sv
            elif sf == 9 and sw == 0:
                combat_power = sv
            elif sf == 10 and sw == 0:
                favorability = sv
            elif sf == 13 and sw == 0:
                region_id = sv
        if pid is not None and name is not None:
            friends.append(
                {
                    "player_id": pid,
                    "name": name,
                    "last_login_ts": last_ts,
                    "level": level,
                    "combat_power": combat_power,
                    "favorability": favorability,
                    "region_id": region_id,
                }
            )
    return friends


# ── mining (0x0c01 / 0x0c03) parsers ────────────────────────────────────────


def parse_mine_board(body: bytes) -> Dict[str, Any]:
    """Decode `0x0c01` rx (mining board state).

    Schema (empirical, 5554-verified 2026-05-11):

        f1 varint            ← pickaxe_cap（鎬子上限/容量，NOT current
                               count）。user 同時持有 112 個鎬子但 f1 報
                               114。current 鎬子數只能從 0x0402 evt=
                               9800001 f3 push 監聽（每用一鎬會推送）。
        f2 varint            ← server_ts (Unix seconds, may be 0)
        f3 varint            ← round_id
        f4 varint            ← floor_offset (first 6 digits of cell IDs)
        f5 varint REPEATED   ← cells available to dig (cell IDs)
        f6 bytes REPEATED    ← recent floor event log (last ~3 events);
                                sub: {f1=event_id, f2=event_value}.
                                event_id is monotonically increasing per
                                floor — NOT a prop_id. Drill (4002) and
                                bomb (4003) counts live in `0x0402` rx,
                                not here.
        f7 bytes REPEATED    ← cell features (terrain markers);
                                sub: {f1=cell_id, f2=col, f3=depth,
                                      f4=terrain enum (see MINE_TERRAIN_*),
                                      f5=cave-active flag (1 if 礦洞 not
                                      yet entered), f6=reserved}

    `floor_id` key kept for backwards-compat (mirrors `pickaxe_cap`);
    `pickaxe_count` is also aliased for code that was written against the
    earlier (incorrect) label.
    """
    out: Dict[str, Any] = {
        "pickaxe_cap": None,
        "pickaxe_count": None,      # alias of pickaxe_cap (legacy)
        "floor_id": None,           # alias of pickaxe_cap (legacy)
        "server_ts": None,
        "round_id": None,
        "floor_offset": None,
        "cells": [],
        "events": [],
        "features": [],
    }
    if not body:
        return out
    for fnum, wire, val in _walk_pb(body):
        if fnum == 1 and wire == 0:
            out["pickaxe_cap"] = val
            out["pickaxe_count"] = val
            out["floor_id"] = val
        elif fnum == 2 and wire == 0:
            out["server_ts"] = val
        elif fnum == 3 and wire == 0:
            out["round_id"] = val
        elif fnum == 4 and wire == 0:
            out["floor_offset"] = val
        elif fnum == 5 and wire == 0:
            out["cells"].append(val)
        elif fnum == 6 and wire == 2:
            evt = _parse_event_entry(val)
            if evt["event_id"] is not None:
                out["events"].append(evt)
        elif fnum == 7 and wire == 2:
            out["features"].append(_parse_cell_feature(val))
    return out


def _parse_event_entry(sub: bytes) -> Dict[str, Any]:
    """`{f1=event_id, f2=event_value}` — used in `0x0c01` f6 and `0x0c03`
    rx f4. event_id is a per-floor monotonic counter; event_value is
    context-dependent (drop count, score delta, etc.)."""
    out: Dict[str, Any] = {"event_id": None, "value": None}
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            out["event_id"] = sv
        elif sf == 2 and sw == 0:
            out["value"] = sv
    return out


def _parse_cell_feature(sub: bytes) -> Dict[str, Any]:
    """Decode an 18-byte cell feature entry.

    Sub-fields (5554-verified 2026-05-11):
        f1 = cell_id (depth*100 + col)
        f2 = col (1..N, mirrors cell_id % 100)
        f3 = depth (mirrors cell_id // 100)
        f4 = terrain enum — see MINE_TERRAIN_* constants:
             100 = 寶箱/礦石, 201 = 泥土, 202 = 石頭, 401 = 礦洞
        f5 = cave-active flag (1 = 礦洞 not yet entered, only meaningful
             when f4 == MINE_FEATURE_CAVE)
        f6 = reserved (always 0 in observed captures)

    Output adds `terrain`, `terrain_name`, `is_cave` derived helpers.
    Unknown f4 values fall through to terrain_name=f"t{f4}".
    """
    feat: Dict[str, Any] = {
        "cell_id": None,
        "col": None,
        "depth": None,
        "terrain": None,
        "terrain_name": None,
        "is_cave": False,
        "raw": {},
    }
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            feat["cell_id"] = sv
        elif sf == 2 and sw == 0:
            feat["col"] = sv
        elif sf == 3 and sw == 0:
            feat["depth"] = sv
        elif sf == 4 and sw == 0:
            feat["terrain"] = sv
            feat["terrain_name"] = MINE_TERRAIN_NAME.get(sv, f"t{sv}")
            feat["is_cave"] = (sv == MINE_FEATURE_CAVE)
        elif sw == 0:
            feat["raw"][f"f{sf}"] = sv
    return feat


def parse_dig_result(body: bytes) -> Dict[str, Any]:
    """Decode `0x0c03` rx (dig result).

    Schema:
        f1 varint            ← round_id
        f2 varint            ← floor_offset
        f3 varint REPEATED   ← newly revealed cell IDs
        f4 bytes (optional)  ← single new floor event triggered by this dig
                               {f1=event_id, f2=value}; the same entry will
                               appear in the next `0x0c01` rx f6 ring.
        f5 bytes REPEATED    ← updated cell features (same 18-byte shape
                               as `0x0c01` f7)
    """
    out: Dict[str, Any] = {
        "round_id": None,
        "floor_offset": None,
        "newly_revealed": [],
        "new_event": None,
        "feature_updates": [],
    }
    if not body:
        return out
    for fnum, wire, val in _walk_pb(body):
        if fnum == 1 and wire == 0:
            out["round_id"] = val
        elif fnum == 2 and wire == 0:
            out["floor_offset"] = val
        elif fnum == 3 and wire == 0:
            out["newly_revealed"].append(val)
        elif fnum == 4 and wire == 2:
            evt = _parse_event_entry(val)
            if evt["event_id"] is not None:
                out["new_event"] = evt
        elif fnum == 5 and wire == 2:
            out["feature_updates"].append(_parse_cell_feature(val))
    return out


# ── inventory (0x0402) parser ───────────────────────────────────────────────


def parse_inventory_push(body: bytes) -> Dict[str, Any]:
    """Decode `0x0402` rx (inventory/currency change push).

    Schema (empirical):
        f1 varint            ← event_type
        f2 bytes REPEATED    ← per-item entry; sub:
            sf1  item_id
            sf2  uid_or_ts (large varint, ignored)
            sf3  current_qty   ← quantity AFTER the change
            sf4..sf7  flags (e.g. delta sign, slot, source) — surfaced as raw

    Single-item samples (size 28B) carry one entry; multi-item pushes
    bundle several together. Both shapes share the same `f2` schema.
    """
    out: Dict[str, Any] = {
        "event_type": None,
        "items": [],
    }
    if not body:
        return out
    for fnum, wire, val in _walk_pb(body):
        if fnum == 1 and wire == 0:
            out["event_type"] = val
        elif fnum == 2 and wire == 2:
            out["items"].append(_parse_inventory_entry(val))
    return out


def _parse_inventory_entry(sub: bytes) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"item_id": None, "qty": None, "raw": {}}
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            entry["item_id"] = sv
        elif sf == 3 and sw == 0:
            entry["qty"] = sv
        elif sw == 0:
            entry["raw"][f"f{sf}"] = sv
    return entry


# ── lamp drops (0x0504) parser ──────────────────────────────────────────────


def parse_lamp_drops(body: bytes) -> Dict[str, Any]:
    """Decode `0x0504` rx — a batch of lamp-open results.

    Schema (empirical, verified 2026-05-11 against live 5554 session):
        f1 varint            ← round_kind (2/3)
        f2 varint            ← flag (0/1)
        f3 bytes REPEATED    ← one entry PER LAMP OPENED
            sf1 varint           ← drop_uid (sequentially decreasing)
            sf2 varint           ← template_id (drop pool variant)
            sf3 varint           ← lamp position (200, 199, 198... per cycle)
            sf4 varint           ← 0 (placeholder)
            sf5 varint           ← 0 (placeholder)
            sf6 bytes REPEATED   ← pool composition: {f1=item_id, f2=weight}
            sf7 bytes REPEATED   ← ACTUAL DROPS gained: {f1=item_id, f2=count}
            sf8 varint           ← 0
            sf9 varint           ← bonus / exp (e.g. 172545)

    The `f3.6` entries describe the pool the lamp drew from (large weights
    like 14000 / 62615) — useful for understanding rarity but NOT
    rewards. The actual gained items are exclusively in `f3.7`.

    Returns:
        {
          'round_kind': int,
          'flag': int,
          'lamps': List[{
              'drop_uid': int,
              'template_id': int,
              'position': int,
              'pool_weights': Dict[item_id, weight],
              'rewards': Dict[item_id, count],   # ← what user actually got
              'bonus': int | None,
          }],
          'total_rewards': Dict[item_id, count],  # aggregated across all lamps
        }
    """
    out: Dict[str, Any] = {
        "round_kind": None, "flag": None, "lamps": [],
        "total_rewards": {},
    }
    if not body:
        return out
    for fnum, wire, val in _walk_pb(body):
        if fnum == 1 and wire == 0:
            out["round_kind"] = val
        elif fnum == 2 and wire == 0:
            out["flag"] = val
        elif fnum == 3 and wire == 2:
            lamp = _parse_lamp_entry(val)
            out["lamps"].append(lamp)
            for iid, n in lamp["rewards"].items():
                out["total_rewards"][iid] = out["total_rewards"].get(iid, 0) + n
    return out


def _parse_lamp_entry(sub: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "drop_uid": None, "template_id": None, "position": None,
        "pool_weights": {}, "rewards": {}, "bonus": None,
    }
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            out["drop_uid"] = sv
        elif sf == 2 and sw == 0:
            out["template_id"] = sv
        elif sf == 3 and sw == 0:
            out["position"] = sv
        elif sf == 6 and sw == 2:
            iid, n = _parse_item_pair(sv)
            if iid is not None:
                out["pool_weights"][iid] = n
        elif sf == 7 and sw == 2:
            iid, n = _parse_item_pair(sv)
            if iid is not None:
                out["rewards"][iid] = out["rewards"].get(iid, 0) + n
        elif sf == 9 and sw == 0:
            out["bonus"] = sv
    return out


def _parse_item_pair(sub: bytes) -> tuple[Optional[int], Optional[int]]:
    iid, n = None, None
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            iid = sv
        elif sf == 2 and sw == 0:
            n = sv
    return iid, n


# ── request body builders ────────────────────────────────────────────────────


def _build_friend_list_request_body(tab: int = 1) -> bytes:
    """`{ field1: 1, field2: tab }` — 兩個 varint，4 bytes 即可。"""
    return bytes([0x08, 0x01, 0x10, tab & 0xFF])


def _encode_varint(v: int) -> bytes:
    """Standard protobuf varint encoder."""
    out = bytearray()
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out) if out else b"\x00"


def _build_dig_request_body(prop_id: int, cell_id: int) -> bytes:
    """`{ f1=prop_id, f2=cell_id }` — both varint, both wire-type 0."""
    return b"\x08" + _encode_varint(prop_id) + b"\x10" + _encode_varint(cell_id)


# ── RPC injected JS ──────────────────────────────────────────────────────────

# 把這段 JS 注進 page。等 game client 自己的 WebSocket 通道把 cmd 送出，
# 並在 IOHandler 解密 response 之後（透過 monkey-patch SocketClient.reciveMsg）
# 攔下指定 cmd_id 的 body 回傳。
#
# 開頭的 wait loop 是給「page 剛 navigate / 切 tab，netManager 還沒掛上」
# 的情境用 — 最多等 5 秒；超過就拋。
_RPC_JS = r"""
async ([cmd, bodyArr, timeoutSec, netWaitMs]) => {
  const waitDeadline = Date.now() + (netWaitMs || 5000);
  while (!window.netManager?._cnet) {
    if (Date.now() > waitDeadline) throw 'netManager not ready';
    await new Promise(r => setTimeout(r, 100));
  }
  const sock = netManager._cnet;
  if (sock.state !== 1 /* SocketState.Connected */ &&
      sock.state !== 2 /* SocketState.LoggedIn-ish */ &&
      sock._socket?.readyState !== 1) {
    // try anyway; sendMessage will silently drop if not OPEN, then we'll timeout
  }
  return new Promise((resolve, reject) => {
    const orig = sock.reciveMsg.bind(sock);
    let cleared = false;
    const cleanup = () => {
      if (cleared) return;
      cleared = true;
      sock.reciveMsg = orig;
    };
    const tid = setTimeout(() => { cleanup(); reject('timeout'); }, timeoutSec * 1000);
    sock.reciveMsg = function (c, b) {
      if (c === cmd && !cleared) {
        clearTimeout(tid);
        cleanup();
        // b is Uint8Array (already XOR-decrypted by IOHandler.dispatchRequest)
        resolve(Array.from(b));
      }
      return orig(c, b);
    };
    try {
      sock.sendMessage(cmd, new Uint8Array(bodyArr));
    } catch (e) {
      clearTimeout(tid);
      cleanup();
      reject(String(e));
    }
  });
}
"""


# ── game-state probe ─────────────────────────────────────────────────────────


# Snapshot of `window.netManager._cnet` plus the underlying WebSocket. All
# fields are best-effort — game JS internals may differ; missing fields
# come back undefined (→ None on the Python side). Cheap (no RPC).
#
# The first call also installs a one-time hook on `_cnet._socket.onclose`
# that records close code + reason into `window.__bot_ws_close` for the
# is_login_conflict() probe.
_GAME_STATE_JS = r"""
() => {
  const out = {
    has_netmgr: false,
    has_cnet: false,
    sock_state: null,         // _cnet.state (game-defined enum)
    ws_ready_state: null,     // 0=CONN 1=OPEN 2=CLOSING 3=CLOSED
    has_scene: false,
    scene_name: null,
    url: location.href,
    close_event: null,        // {ts, code, reason, was_clean} from onclose hook
  };
  try {
    if (window.netManager) {
      out.has_netmgr = true;
      const sock = window.netManager._cnet;
      if (sock) {
        out.has_cnet = true;
        if (typeof sock.state !== 'undefined') out.sock_state = sock.state;
        const ws = sock._socket;
        if (ws && typeof ws.readyState !== 'undefined') {
          out.ws_ready_state = ws.readyState;
          // Install once: capture close events into a window-level slot
          // so a later poll can read code+reason even after WS is gone.
          if (ws && !ws.__bot_close_hooked) {
            ws.__bot_close_hooked = true;
            const origClose = ws.onclose;
            ws.onclose = function (ev) {
              try {
                window.__bot_ws_close = {
                  ts: Date.now(),
                  code: ev && ev.code,
                  reason: ev && ev.reason,
                  was_clean: ev && ev.wasClean,
                };
              } catch (_) {}
              if (typeof origClose === 'function') return origClose.call(this, ev);
            };
          }
        }
      }
    }
    if (window.__bot_ws_close) out.close_event = window.__bot_ws_close;
    if (window.cc?.director?.getScene) {
      const sc = cc.director.getScene();
      if (sc) {
        out.has_scene = true;
        out.scene_name = sc.name || sc._name || null;
      }
    }
  } catch (e) { out.error = String(e); }
  return out;
}
"""


# Scene names the game uses for login/server-select/disconnect screens —
# expand as new ones are observed. Detection logic falls back to a
# substring match so different builds with prefixed names still match.
_LOGIN_SCENE_HINTS = (
    "Login",
    "ServerSelect",
    "Server",
    "Reconnect",
    "Disconnect",
)


# ── public API ───────────────────────────────────────────────────────────────


class WebGameAPI:
    """Lightweight RPC over the running game's WebSocket via Playwright.

    The Playwright `page` must be the live game tab where `window.netManager`
    is attached and the WebSocket session is logged-in.
    """

    def __init__(self, page) -> None:
        self._page = page

    def game_state(self) -> Dict[str, Any]:
        """Cheap read-only probe of game/WS readiness.

        Returns a dict with at least these keys (any value may be `None`):
            has_netmgr     bool — `window.netManager` exists
            has_cnet       bool — `netManager._cnet` exists
            sock_state     int  — game-defined `_cnet.state` (None if missing)
            ws_ready_state int  — WebSocket.readyState; 1 = OPEN
            has_scene      bool — Cocos scene root exists
            scene_name     str  — current scene name (e.g. 'GameScene')
            url            str  — `location.href`

        Raises on Playwright eval error (page closed, etc.).
        """
        result = self._page.evaluate(_GAME_STATE_JS)
        return result if isinstance(result, dict) else {}

    def is_in_game(self) -> bool:
        """True when the game canvas is alive AND the WS is OPEN AND the
        client has finished its login handshake.

        Heuristic:
          - WS readyState == 1 (OPEN)
          - sock_state >= 1   (Connected; >=2 also acceptable for LoggedIn)
          - scene exists
        Returns False on any missing/error condition; never raises.
        """
        try:
            st = self.game_state()
        except Exception:
            return False
        if not st.get("has_cnet"):
            return False
        if st.get("ws_ready_state") != 1:
            return False
        s = st.get("sock_state")
        if s is None or int(s) < 1:
            return False
        if not st.get("has_scene"):
            return False
        return True

    def is_login_conflict(self) -> bool:
        """True when the page shows symptoms of 異地登入 (kicked by another login).

        Heuristic — any of these:
          - WS was closed with abnormal/server-initiated code (not 1000/1001)
          - WS readyState is 3 (CLOSED) AND has_cnet still true (game saw kick)
          - Scene name contains a Login/ServerSelect-style hint while we expect game

        False on any error. Designed for "should I bail out and sleep?" — a
        false negative just means we keep retrying RPC until timeout, which
        is safe; a false positive would skip a usable session.
        """
        try:
            st = self.game_state()
        except Exception:
            return False
        if not isinstance(st, dict):
            return False

        ce = st.get("close_event") or {}
        code = ce.get("code")
        if code is not None:
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None
        # 1000 = normal, 1001 = going-away (page reload). Anything else after
        # we successfully connected is treated as kick-likely.
        if code is not None and code not in (1000, 1001):
            return True

        # WS now CLOSED but cnet object still exists → game hasn't re-init'd
        # because something forcibly disconnected us.
        if st.get("has_cnet") and st.get("ws_ready_state") == 3:
            return True

        scene = (st.get("scene_name") or "")
        if scene:
            for hint in _LOGIN_SCENE_HINTS:
                if hint.lower() in scene.lower():
                    return True

        return False

    def kick_reason(self) -> Optional[str]:
        """Return a short string describing the kick if `is_login_conflict()`,
        else None. Pulls (code, reason) from the WS close hook for logging.
        """
        try:
            st = self.game_state()
        except Exception:
            return None
        if not isinstance(st, dict):
            return None
        ce = st.get("close_event") or {}
        if ce:
            return f"ws_close code={ce.get('code')} reason={ce.get('reason')!r}"
        if st.get("has_cnet") and st.get("ws_ready_state") == 3:
            return "ws_closed (no close_event captured)"
        scene = st.get("scene_name") or ""
        for hint in _LOGIN_SCENE_HINTS:
            if hint.lower() in scene.lower():
                return f"scene={scene}"
        return None

    def wait_until_in_game(
        self,
        timeout_sec: float = 25.0,
        on_poll: Optional[Any] = None,
    ) -> bool:
        """Poll `is_in_game()` until True or `timeout_sec` elapses.

        Returns True if the game became ready in time, False otherwise.
        Polls every 250 ms — Python-side because Playwright's sync API is
        thread-affine; running the wait in JS would block the page.

        `on_poll`, if given, is invoked once per iteration (best-effort,
        exceptions swallowed). Use it to interleave bot_state.check_pause
        so a paused device still observes the pause within ~250 ms.
        """
        deadline = time.time() + float(timeout_sec)
        while time.time() < deadline:
            if on_poll is not None:
                try:
                    on_poll()
                except Exception:
                    pass
            if self.is_in_game():
                return True
            time.sleep(0.25)
        return False

    def call_raw(
        self,
        cmd_id: int,
        body: bytes,
        timeout_sec: float = 5.0,
        net_wait_ms: int = 5000,
    ) -> bytes:
        """Send (cmd_id, body) and wait for the response with the same cmd_id.

        `net_wait_ms` controls how long we wait for `window.netManager._cnet`
        to materialize before giving up — useful when the call site has just
        woken a long-sleeping tab whose WebSocket needs to reconnect.

        Returns the decrypted protobuf body bytes.
        Raises on timeout or page evaluation error.
        """
        result = self._page.evaluate(
            _RPC_JS,
            [cmd_id, list(body), float(timeout_sec), int(net_wait_ms)],
        )
        return bytes(result)

    def fetch_friend_list(
        self,
        tab: int = 1,
        timeout_sec: float = 5.0,
        net_wait_ms: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Returns parsed friend list. tab=1 (好友) by default."""
        body = self.call_raw(
            CMD_FRIEND_LIST,
            _build_friend_list_request_body(tab=tab),
            timeout_sec=timeout_sec,
            net_wait_ms=net_wait_ms,
        )
        return parse_friend_list(body)

    def fetch_mine_board(
        self,
        timeout_sec: float = 5.0,
        net_wait_ms: int = 5000,
    ) -> Dict[str, Any]:
        """Returns parsed mining board state via cmd `0x0c01` (empty req body).

        Result keys: `floor_id`, `server_ts`, `round_id`, `floor_offset`,
        `cells` (list of un-dug cell IDs), `props` (prop_id → count),
        `features` (list of cell feature dicts).
        """
        body = self.call_raw(
            CMD_MINE_BOARD, b"",
            timeout_sec=timeout_sec, net_wait_ms=net_wait_ms,
        )
        return parse_mine_board(body)

    def dig_cell(
        self,
        cell_id: int,
        prop_id: int = PROP_PICKAXE,
        timeout_sec: float = 5.0,
        net_wait_ms: int = 5000,
    ) -> Dict[str, Any]:
        """Dig a single cell via cmd `0x0c03`. Returns parsed dig result.

        `cell_id` must come from a prior `fetch_mine_board()['cells']` (or
        `newly_revealed` from the previous dig). `prop_id` defaults to
        4001 (鎬子). The response includes any newly visible cells and a
        `prop_drop` field when a pickup happened.
        """
        body = self.call_raw(
            CMD_MINE_DIG,
            _build_dig_request_body(prop_id=prop_id, cell_id=cell_id),
            timeout_sec=timeout_sec, net_wait_ms=net_wait_ms,
        )
        return parse_dig_result(body)

    def is_player_online(
        self,
        target_pid: int,
        threshold_sec: int = 60,
        timeout_sec: float = 5.0,
        net_wait_ms: int = 5000,
    ) -> bool:
        """Returns True if `target_pid` is currently online (or was active
        within `threshold_sec`).

        The server uses `last_login_ts == 0` as a presence sentinel —
        currently-online friends have no meaningful "last login" so the
        field is zeroed. Confirmed empirically against the live friend
        list (5556 view): 5 ONLINE friends all carried ts=0 while every
        offline friend had a populated Unix-second timestamp matching
        the in-game UI's "X 分鐘前 / X 小時前" exactly.

        Returns False when:
          - target not in this account's friend list
          - target has no `last_login_ts` field at all (parser miss)
          - last_login_ts is a positive value older than `threshold_sec`
        """
        friends = self.fetch_friend_list(
            tab=1, timeout_sec=timeout_sec, net_wait_ms=net_wait_ms,
        )
        for f in friends:
            if f["player_id"] != target_pid:
                continue
            ts = f.get("last_login_ts")
            if ts is None:
                return False
            ts_int = int(ts)
            # ts == 0 → currently online (presence sentinel from server).
            if ts_int == 0:
                return True
            return (int(time.time()) - ts_int) < int(threshold_sec)
        return False

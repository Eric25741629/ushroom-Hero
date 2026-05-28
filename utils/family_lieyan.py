"""烈焰山洞 (家族 league_solo) 每日寶箱自動領取。

機制（見 docs/protocol/LIEYAN_CAVE.md）:
  1. send `dungeon.dungeon_league_solo_info_c2s` {} → s2c 0x0e0e 含 box_info
  2. 對每個 box_info[type].got_count < chest_limit 的 type，
     send `dungeon.dungeon_league_solo_get_reward_c2s` {type}
  3. 服務端回 0x0e10 update_box (success) 或 0x0201 error (skip)

box_info type 對應:
  1 = 普通模式 普通寶箱
  2 = 普通模式 稀有寶箱
  3 = 噩夢模式 普通寶箱
  4 = 噩夢模式 稀有寶箱

僅執行在 web_h5 backend 且裝有 cocos navigation 的裝置。其他裝置呼叫端 fallback。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# JS 安裝接收 hook 並把指定 s2c 事件丟進 ring buffer
_INSTALL_BOX_HOOK_JS = r"""
() => {
  if (window.__lieyan_box_hook) return 'already';
  if (!window.netManager) return 'no_netmgr';
  window.__lieyan_box_ring = [];
  const push = (ev, data) => {
    try { window.__lieyan_box_ring.push({ev, ts: Date.now(), data: JSON.stringify(data || {}).slice(0, 4000)}); } catch(e) {}
  };
  // info 回應 → box_info 初始狀態
  netManager.addEventListener('dungeon.dungeon_league_solo_info_s2c', function(d) { push('info', d); }, window);
  // 領取後的更新
  netManager.addEventListener('dungeon.dungeon_league_solo_update_box_s2c', function(d) { push('update_box', d); }, window);
  window.__lieyan_box_hook = true;
  return 'installed';
}
"""

_DRAIN_BOX_RING_JS = "() => { const r = window.__lieyan_box_ring || []; window.__lieyan_box_ring = []; return r; }"

_SEND_INFO_JS = (
    "() => { try { netManager.send('dungeon.dungeon_league_solo_info_c2s', {}); return 'sent'; }"
    " catch(e) { return String(e); } }"
)

_SEND_GET_REWARD_JS = (
    "(t) => { try { netManager.send('dungeon.dungeon_league_solo_get_reward_c2s', {type: t}); return 'sent'; }"
    " catch(e) { return String(e); } }"
)


# Wait helpers
_INFO_WAIT_SEC = 2.0
_REWARD_WAIT_SEC = 1.5

_BOX_TYPES = (1, 2, 3, 4)
_BOX_LABEL = {
    1: "普通模式 普通寶箱",
    2: "普通模式 稀有寶箱",
    3: "噩夢模式 普通寶箱",
    4: "噩夢模式 稀有寶箱",
}


def _parse_box_info(data_json: dict) -> dict[int, dict]:
    """從 info_s2c 的 JSON-stringified body 解出 4 個 type 的 got/limit。

    s2c proto field 命名因 obfuscation 可能變動，這裡用既知的 schema:
      data.box_info 為 list of {type, got_count, chest_limit, ext}
    若 box_info 不在 data 根，嘗試 data.list / data.boxes 等備援欄位。
    """
    out: dict[int, dict] = {}
    if not isinstance(data_json, dict):
        return out
    for candidate_key in ("box_info", "boxes", "list", "info_list"):
        items = data_json.get(candidate_key)
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                t = it.get("type") or it.get("kind")
                if t is None:
                    continue
                out[int(t)] = {
                    "got_count": int(it.get("got_count", it.get("got", 0))),
                    "chest_limit": int(it.get("chest_limit", it.get("limit", 0))),
                }
            if out:
                return out
    return out


def claim_lieyan_daily(page: Any) -> dict:
    """執行一次烈焰山洞每日寶箱領取。

    Returns:
        {
            "ok": bool,
            "claimed": [type_int, ...],   # 成功領取
            "skipped": [(type, reason), ...],  # 已滿/錯誤
            "error": str | None,
        }
    """
    if page is None:
        return {"ok": False, "claimed": [], "skipped": [], "error": "no_page"}

    try:
        page.evaluate(_INSTALL_BOX_HOOK_JS)
        page.evaluate(_DRAIN_BOX_RING_JS)  # clear stale
    except Exception as e:
        return {"ok": False, "claimed": [], "skipped": [], "error": f"hook_install_failed:{e}"}

    # 1. Send info query → s2c update
    try:
        page.evaluate(_SEND_INFO_JS)
    except Exception as e:
        return {"ok": False, "claimed": [], "skipped": [], "error": f"send_info_failed:{e}"}
    time.sleep(_INFO_WAIT_SEC)

    # Drain to find the 'info' frame
    try:
        frames = page.evaluate(_DRAIN_BOX_RING_JS) or []
    except Exception as e:
        return {"ok": False, "claimed": [], "skipped": [], "error": f"drain_failed:{e}"}

    info_frame = next((f for f in frames if f.get("ev") == "info"), None)
    if not info_frame:
        logger.warning("[lieyan] no info response received within %.1fs", _INFO_WAIT_SEC)
        return {"ok": False, "claimed": [], "skipped": [], "error": "no_info_response"}

    import json as _json
    try:
        info_data = _json.loads(info_frame.get("data") or "{}")
    except Exception as e:
        return {"ok": False, "claimed": [], "skipped": [], "error": f"info_parse:{e}"}

    box_info = _parse_box_info(info_data)
    logger.info("[lieyan] box_info: %s", box_info)

    # 2. Claim each unclaimed type
    claimed: list[int] = []
    skipped: list[tuple[int, str]] = []
    for t in _BOX_TYPES:
        entry = box_info.get(t)
        if entry is None:
            skipped.append((t, "not_in_box_info"))
            continue
        if entry["got_count"] >= entry["chest_limit"]:
            skipped.append((t, f"maxed_{entry['got_count']}/{entry['chest_limit']}"))
            continue
        # Send claim
        try:
            page.evaluate(_DRAIN_BOX_RING_JS)
            r = page.evaluate(_SEND_GET_REWARD_JS, t)
            logger.info("[lieyan] claim type=%d (%s) → %s", t, _BOX_LABEL[t], r)
        except Exception as e:
            skipped.append((t, f"send_failed:{e}"))
            continue
        time.sleep(_REWARD_WAIT_SEC)
        # Check for update_box ack
        try:
            ack_frames = page.evaluate(_DRAIN_BOX_RING_JS) or []
        except Exception as e:
            logger.warning("[lieyan] drain after claim failed: %s", e)
            ack_frames = []
        update_frame = next((f for f in ack_frames if f.get("ev") == "update_box"), None)
        if update_frame:
            claimed.append(t)
            logger.info("[lieyan] type=%d claimed", t)
        else:
            skipped.append((t, "no_update_box_ack"))

    return {
        "ok": True,
        "claimed": claimed,
        "skipped": skipped,
        "error": None,
    }

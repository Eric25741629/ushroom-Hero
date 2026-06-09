"""開神燈 v2 — combo->套裝 auto-equip over pure WS.

Open equipment boxes, and for each drop: match its affix combo to the player's
saved 套裝 (equip tab) that uses that combo, compare 詞條 % vs that set's same-slot
item (opengold's SkillEvaluator), and if better equip it into that 套裝 and sell the
displaced old item; otherwise sell the drop. Restore the active 套裝 afterward.
No App / screen / OCR; no forced compare window.

The combo->tab map and per-tab 連閃 flag are derived live from equip_info, so it
adapts to the account. Sells/equips are irreversible -> dry_run by default.

Schema (docs/protocol/EQUIP_PROTO_SCHEMA.json):
  equip_tab_info 0x0510 -> {tab#1=active, unlock_list#2}
  equip_info     0x0501 -> {equip_list#1:p_equip[]}
  equip_box_open_all 0x0509 {num#1,quality#2} -> {equip_ids#1:uint64[]}
  equip_change_s2c 0x0504 {change_list#3:p_equip[]}    (drop detail push)
  equip_wear     0x0502 {tab_id#1, equip_id#2}
  equip_shop     0x0505 {equip_ids#1:uint64[]}
  equip_choose_tab 0x0511 {tab#1}
  p_equip {equip_id#1, config_id#2, location#4, tab#5, rand_attr#7:p_key_value[]...}
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass

from opengold_v2.config import OpenGoldConfig
from opengold_v2.models import Equipment
from opengold_v2.ocr_parser import OCRParser
from opengold_v2.skill_evaluator import SkillEvaluator
from utils.equipment_cache import parse_equipment_lamp_drops
from utils.web_game_api import EQUIP_AFFIX, decode_equip_template
from ws_token import codec
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

CMD_EQUIP_INFO = 0x0501
CMD_WEAR = 0x0502
CMD_EQUIP_CHANGE = 0x0504   # drop detail push
CMD_SELL = 0x0505
CMD_OPEN_ALL = 0x0509
CMD_TAB_INFO = 0x0510
CMD_CHOOSE_TAB = 0x0511
CMD_ERROR = 0x0201

_COMPANION_AFFIXES = {4001, 4005}
_SELL_CHUNK = 10            # sell <=N uids per 0x0505 (avoids the bulk-sell timeout)
_BS = frozenset({"爆", "閃"})  # 爆閃
_LS = frozenset({"連", "閃"})  # 連閃


# --- request builders -------------------------------------------------------

def build_open_all(num: int, quality: int = 0) -> bytes:
    return codec.pb_uint(1, num) + codec.pb_uint(2, quality)


def build_sell(uids) -> bytes:
    return b"".join(codec.pb_uint(1, int(u)) for u in uids)


def build_wear(tab_id: int, equip_id: int) -> bytes:
    return codec.pb_uint(1, tab_id) + codec.pb_uint(2, equip_id)


def build_choose_tab(tab: int) -> bytes:
    return codec.pb_uint(1, tab)


def parse_tab_info(body: bytes) -> int:
    """equip_tab_info_s2c {tab#1=active}."""
    return _as_int(codec.walk_dict(body).get(1))


# --- p_equip parsing --------------------------------------------------------

def parse_drops(body: bytes) -> list[dict]:
    """Equipment entries from a 0x0504 change body (verified parser)."""
    return parse_equipment_lamp_drops(body)


def _parse_p_equip(entry: bytes) -> dict | None:
    """Parse one p_equip (uid, template->rarity/slot, location, tab, affixes)."""
    uid = tmpl = None
    location = 0
    tab = 0
    affixes: dict[int, int] = {}
    for fnum, val in codec.walk(entry):
        if fnum == 1 and isinstance(val, int):
            uid = val
        elif fnum == 2 and isinstance(val, int):
            tmpl = val
        elif fnum == 4 and isinstance(val, int):
            location = val
        elif fnum == 5 and isinstance(val, int):
            tab = val
        elif fnum == 7 and isinstance(val, (bytes, bytearray)):
            kv = codec.walk_dict(bytes(val))
            aid = kv.get(1)
            if aid is not None:
                affixes[int(aid)] = int(kv.get(2, 0))
    if uid is None or tmpl is None:
        return None
    info = decode_equip_template(tmpl)
    return {"uid": uid, "template_id": tmpl, "rarity": info["rarity"],
            "slot": info["slot"], "location": location, "tab": tab, "affixes": affixes}


def parse_worn(equip_info_body: bytes) -> dict[int, dict]:
    """Worn items keyed by slot (collapses tabs; for rough display only)."""
    worn: dict[int, dict] = {}
    for fnum, val in codec.walk(equip_info_body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            item = _parse_p_equip(bytes(val))
            if item and item["location"]:
                worn[item["slot"]] = item
    return worn


# --- affix -> Equipment / combo --------------------------------------------

def drop_to_equipment(item: dict, parser: OCRParser | None = None) -> Equipment:
    parser = parser or OCRParser()
    pairs: list[tuple[str, float]] = []
    for aid, val in (item.get("affixes") or {}).items():
        if int(aid) in _COMPANION_AFFIXES:
            continue
        name = EQUIP_AFFIX.get(int(aid))
        if not name:
            continue
        code = parser.text_to_skill_code(name)
        if not code:
            continue
        pairs.append((code, val / 100.0))
        if len(pairs) == 2:
            break
    return Equipment.from_pairs(pairs)


def _item_combo(item: dict, parser: OCRParser) -> frozenset:
    return frozenset(e.code for e in drop_to_equipment(item, parser).entries)


# --- set-map derivation -----------------------------------------------------

def derive_set_map(equip_info_body: bytes, parser: OCRParser | None = None):
    """Derive {combo_frozenset: tab}, lian_shan_tabs, and worn[tab][slot] from
    equip_info. Each tab's identity = its dominant worn combo (outliers from past
    mis-swaps ignored). A tab holding both 爆閃 and 連閃 is the 連閃 build: both
    combos map to it and it is flagged 連閃 (merge 連+爆 when comparing)."""
    parser = parser or OCRParser()
    worn: dict[int, dict[int, dict]] = {}
    for fnum, val in codec.walk(equip_info_body):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            item = _parse_p_equip(bytes(val))
            if item and item["location"]:
                worn.setdefault(item["tab"], {})[item["slot"]] = item

    tab_counts: dict[int, Counter] = {}
    for tab, slots in worn.items():
        combos = [c for c in (_item_combo(it, parser) for it in slots.values())
                  if len(c) == 2]
        tab_counts[tab] = Counter(combos)

    lian_shan_tabs = {t for t, c in tab_counts.items()
                      if c.get(_BS, 0) > 0 and c.get(_LS, 0) > 0}

    # combo -> the tab with the MOST items of that combo (the complete/canonical
    # set), so an incomplete tab sharing a dominant combo never steals it.
    best: dict[frozenset, tuple[int, int]] = {}  # combo -> (count, tab)
    for tab, cnt in tab_counts.items():
        if tab in lian_shan_tabs or not cnt:
            continue
        dominant, dcount = cnt.most_common(1)[0]
        prev = best.get(dominant)
        if prev is None or dcount > prev[0] or (dcount == prev[0] and tab < prev[1]):
            best[dominant] = (dcount, tab)
    set_map: dict[frozenset, int] = {combo: tab for combo, (_, tab) in best.items()}
    for tab in lian_shan_tabs:  # the 連閃 build owns both 爆閃 and 連閃
        set_map[_BS] = tab
        set_map[_LS] = tab
    return set_map, lian_shan_tabs, worn


# --- decision ---------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    action: str               # "sell" | "equip" | "leave"
    reason: str
    tab: int | None = None
    displaced_uid: int | None = None


def decide_v2(drop: dict, set_map: dict, worn: dict, lian_shan_tabs: set,
              config: OpenGoldConfig, parser: OCRParser) -> Decision:
    rarity = int(drop.get("rarity", 0))
    if rarity < config.wanted_rarity:
        return Decision("sell", f"rarity {rarity} < {config.wanted_rarity}")

    eq = drop_to_equipment(drop, parser)
    codes = frozenset(e.code for e in eq.entries)
    tab = set_map.get(codes)

    if tab is None:
        # not one of the player's sets -> fall back to the unwanted heuristic
        if len(codes) == 2 and codes in config.unwanted_combos:
            return Decision("sell", f"unmapped unwanted combo {''.join(sorted(codes))}")
        return Decision("leave", f"no 套裝 for combo {''.join(sorted(codes)) or '-'}")

    worn_item = worn.get(tab, {}).get(drop.get("slot"))
    if worn_item is None:
        return Decision("equip", f"fill empty slot in tab {tab}", tab=tab)

    evaluator = SkillEvaluator(config, has_lian_shan_equip=(tab in lian_shan_tabs))
    result = evaluator.compare_skill_pairs(
        rolled=eq, original=drop_to_equipment(worn_item, parser))
    if result.should_replace:
        return Decision("equip", result.reason, tab=tab,
                        displaced_uid=worn_item["uid"])
    return Decision("sell", result.reason)


# --- open loop --------------------------------------------------------------

def _extract_repeated_uint(body: bytes, field: int) -> list[int]:
    out: list[int] = []
    for fnum, val in codec.walk(body):
        if fnum != field:
            continue
        if isinstance(val, int):
            out.append(val)
        elif isinstance(val, (bytes, bytearray)):  # packed varints
            data = bytes(val)
            off = 0
            while off < len(data):
                v = shift = 0
                while True:
                    b = data[off]
                    off += 1
                    v |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                out.append(v)
    return out


def _try_call(client: WSGameClient, cmd: int, body: bytes, *, timeout: float,
              what: str) -> bool:
    try:
        client.call_for(cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout)
        return True
    except Exception as exc:  # timeout OR connection drop — never abort the run
        logger.warning("ws_token lamp: %s failed (%s); continuing", what, exc)
        return False


def open_lamp(
    client: WSGameClient,
    *,
    config: OpenGoldConfig | None = None,
    dry_run: bool = True,
    batch_num: int = 20,
    max_batches: int = 1,
    quality: int = 0,
    push_wait: float = 2.0,
    sell_timeout: float = 8.0,
) -> dict:
    """Open boxes and auto-equip winners into their matching 套裝.

    Returns {opened, equipped:[(tab,uid,reason)], sold:[(uid,reason)],
    left:[(uid,combo)], dry_run}. dry_run (default) computes + logs everything
    but sends no wear/sell/choose-tab.
    """
    config = config or OpenGoldConfig()
    parser = OCRParser(config)

    active_tab = parse_tab_info(client.call(CMD_TAB_INFO, b""))
    set_map, lian_shan_tabs, worn = derive_set_map(
        client.call(CMD_EQUIP_INFO, b""), parser)
    logger.info("ws_token lamp: active_tab=%s sets=%s lian=%s",
                active_tab, {"".join(sorted(k)): v for k, v in set_map.items()},
                lian_shan_tabs)

    drops: dict[int, dict] = {}
    lock = threading.Lock()

    def _push(cmd: int, body: bytes) -> None:
        if cmd == CMD_EQUIP_CHANGE:
            with lock:
                for d in parse_drops(body):
                    drops[d["uid"]] = d

    client.set_push_handler(_push)
    equipped: list[tuple[int, int, str]] = []
    sold: list[tuple[int, str]] = []
    left: list[tuple[int, str]] = []
    opened = 0
    try:
        for _ in range(max_batches):
            try:
                cmd, s2c = client.call_for(
                    CMD_OPEN_ALL, build_open_all(batch_num, quality),
                    expect_cmds=(CMD_OPEN_ALL, CMD_ERROR), timeout=10.0)
            except WSTimeoutError:
                logger.info("ws_token lamp: open timed out (out of lamps?); stop")
                break
            except Exception as exc:  # connection drop / unexpected — stop cleanly
                logger.warning("ws_token lamp: open failed (%s); stopping", exc)
                break
            if cmd == CMD_ERROR:
                logger.info("ws_token lamp: open error code=%s (out of lamps?); stop",
                            codec.walk_dict(s2c).get(1))
                break
            new_uids = _extract_repeated_uint(s2c, 1)
            if not new_uids:
                break
            opened += len(new_uids)

            deadline = time.time() + push_wait
            while time.time() < deadline:
                with lock:
                    if all(u in drops for u in new_uids):
                        break
                time.sleep(0.02)

            batch_equips: list[tuple[int, int]] = []
            batch_sells: list[int] = []
            for uid in new_uids:
                with lock:
                    detail = drops.get(uid)
                if detail is None:
                    left.append((uid, "no drop detail"))
                    continue
                d = decide_v2(detail, set_map, worn, lian_shan_tabs, config, parser)
                logger.info("ws_token lamp uid=%s -> %s (%s)", uid, d.action.upper(), d.reason)
                if d.action == "equip":
                    equipped.append((d.tab, uid, d.reason))
                    batch_equips.append((d.tab, uid))
                    worn.setdefault(d.tab, {})[detail["slot"]] = detail  # new worn baseline
                    if d.displaced_uid is not None:
                        sold.append((d.displaced_uid, "displaced by " + str(uid)))
                        batch_sells.append(d.displaced_uid)
                elif d.action == "sell":
                    sold.append((uid, d.reason))
                    batch_sells.append(uid)
                else:
                    left.append((uid, "".join(sorted(
                        frozenset(e.code for e in drop_to_equipment(detail, parser).entries)))))

            if not dry_run:
                for tab, uid in batch_equips:  # equip first so the old piece frees up
                    _try_call(client, CMD_WEAR, build_wear(tab, uid),
                              timeout=sell_timeout, what=f"wear tab={tab} uid={uid}")
                for i in range(0, len(batch_sells), _SELL_CHUNK):
                    chunk = batch_sells[i:i + _SELL_CHUNK]
                    _try_call(client, CMD_SELL, build_sell(chunk),
                              timeout=sell_timeout, what=f"sell {chunk}")

        if not dry_run and active_tab:
            _try_call(client, CMD_CHOOSE_TAB, build_choose_tab(active_tab),
                      timeout=sell_timeout, what=f"restore active tab {active_tab}")
    finally:
        client.set_push_handler(None)

    logger.info("ws_token lamp: opened=%d equipped=%d sold=%d left=%d dry_run=%s",
                opened, len(equipped), len(sold), len(left), dry_run)
    return {"opened": opened, "equipped": equipped, "sold": sold, "left": left,
            "dry_run": dry_run}


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

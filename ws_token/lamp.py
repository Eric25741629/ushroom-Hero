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
from typing import Callable

from opengold_v2.config import OpenGoldConfig
from opengold_v2.models import Equipment
from opengold_v2.ocr_parser import OCRParser
from opengold_v2.skill_evaluator import SkillEvaluator
from utils.equipment_cache import parse_equipment_lamp_drops
from utils.web_game_api import EQUIP_AFFIX, decode_equip_template
from ws_token import codec
from ws_token.abort import WSRunAborted
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)


def _resolve_logger(device_id: str | None) -> logging.Logger:
    if device_id:
        try:
            from utils.logging_utils import get_or_create_ws_lamp_logger
            return get_or_create_ws_lamp_logger(device_id)
        except Exception:
            return logger
    return logger


CMD_EQUIP_INFO = 0x0501
CMD_WEAR = 0x0502
CMD_EQUIP_CHANGE = 0x0504   # drop detail push
CMD_SELL = 0x0505
CMD_OPEN_ALL = 0x0509
CMD_TAB_INFO = 0x0510
CMD_CHOOSE_TAB = 0x0511
CMD_ERROR = 0x0201
CMD_INVENTORY_PUSH = 0x0402  # item/currency delta push (shares mining's value)

ITEM_LAMP = 1001            # 神燈 item_id (EQUIPMENT_SCHEMA §9-10)
_LAMP_BATCH = 20            # one auto-open consumes 20 lamps

_COMPANION_AFFIXES = {4001, 4005}
_SELL_CHUNK = 20            # 遊戲一次可賣 20 件以下，對齊單批開燈上限。
_SELL_DELAY_SEC = 0.3       # 賣出後等伺服器處理完，再送下一個指令。
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


# --- 神燈 數量 / 百分比 + 最低保留 ------------------------------------------

def round_to_nearest_20(n: float) -> int:
    """Nearest NON-NEGATIVE multiple of 20.

    e.g. 10001->10000, 10011->10020, 19->20, 9->0, -5->0. The half-step (10)
    rounds up to 20 (banker's-rounding-free ``int(round())`` keeps it simple).
    """
    if n <= 0:
        return 0
    return int((n + 10) // 20) * 20


def extract_lamp_count(body: bytes) -> int | None:
    """神燈(item 1001) 當前剩餘 from a 0x0402 body, or None if absent.

    Mirrors ``mining.InventoryTracker.on_push``: ``walk`` yields (fnum, val)
    with f1=evt_type and f2(repeated)=item sub bytes; within a sub,
    ``walk_dict`` gives field1=item_id and field3=current count. Works for ANY
    evt_type (the 1001006 consume push AND a login snapshot that carries 1001).
    """
    for fnum, val in codec.walk(body):
        if fnum != 2 or not isinstance(val, (bytes, bytearray)):
            continue
        d = codec.walk_dict(bytes(val))
        if d.get(1) == ITEM_LAMP:
            qty = d.get(3)
            if isinstance(qty, int):
                return qty
    return None


def compute_lamp_target(total: int, *, lamp_percent: float, lamp_min_keep: int,
                        max_open: int,
                        lamp_daily_min: int = 0, opened_today: int = 0) -> int:
    """Lamps to open this run: a multiple of 20 clamped to ``[0, max_open]``.

    floor_cap = max(0, total - lamp_min_keep)
    - both percent & min_keep set -> raw = min(percent_amt, floor_cap)
    - percent only                -> raw = percent_amt
    - min_keep only               -> raw = floor_cap
    - neither                     -> raw = total (open the whole lot)

    ``lamp_daily_min`` (>0) is a HARD daily floor: if today's opened count has
    not reached the daily minimum, the target is boosted to cover the remaining
    daily quota. This OVERRIDES both the percentage limit and the
    ``lamp_min_keep`` reserve (it may dig below the reserve), capped only by
    ``max_open``.
    """
    floor_cap = max(0, total - lamp_min_keep)
    if lamp_percent > 0 and lamp_min_keep > 0:
        raw: float = min(total * lamp_percent / 100.0, floor_cap)
    elif lamp_percent > 0:
        raw = total * lamp_percent / 100.0
    elif lamp_min_keep > 0:
        raw = floor_cap
    else:
        raw = total
    normal = min(max(0, round_to_nearest_20(raw)), max_open)
    if lamp_daily_min > 0:
        remaining_daily = max(0, lamp_daily_min - opened_today)
        if remaining_daily > normal:
            # Hard daily floor: overrides the min_keep reserve (may dig below it).
            return min(max(0, round_to_nearest_20(remaining_daily)), max_open)
    return normal


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
              what: str, expect_extra: tuple[int, ...] = (),
              _log: logging.Logger | None = None) -> str:
    """Send a fire-and-effect equip op. Returns "ok" | "presumed" | "failed".

    wear (0x0502) has NO self-cmd echo (schema defines equip_wear_c2s but no
    equip_wear_s2c; live-probed 2026-07-05 via CDP on 5554). A SUCCESSFUL wear
    is signaled by the equip_change_s2c 0x0504 push, a REJECTED wear by the
    generic error 0x0201 (code in field 1). The wear site therefore passes
    ``expect_extra=(CMD_EQUIP_CHANGE,)`` so the waiter fires on the real
    success signal (client `_route` matches waiters by cmd before the push
    handler). sell (0x0505) also never echoed in live logs (2/2 timeouts, items
    gone) but has no probed success signal yet.

    Residual timeouts are treated as "presumed" applied (belt-and-suspenders:
    live 31/31 wear timeouts pre-fix were all real successes), so the run
    summary stops reporting these as failures. A CMD_ERROR reply is a genuine
    rejection; a non-timeout exception (dropped connection) is a real "failed".
    """
    try:
        rcmd, rbody = client.call_for(
            cmd, body, expect_cmds=(cmd, CMD_ERROR) + tuple(expect_extra),
            timeout=timeout)
    except WSTimeoutError:
        # ponytail: presumed-applied on no-reply — sell path still UNVERIFIED;
        # if a sell ever times out AND the items persist, probe sell's success
        # signal (0x0504? inventory push?) like the wear probe did.
        return "presumed"
    except Exception as exc:  # connection drop / unexpected — real failure
        (_log or logger).warning("ws_token lamp: %s failed (%s); continuing", what, exc)
        return "failed"
    if rcmd == CMD_ERROR:
        (_log or logger).warning("ws_token lamp: %s rejected (error code=%s)",
                                 what, codec.walk_dict(rbody).get(1))
        return "failed"
    return "ok"


def open_lamp(
    client: WSGameClient,
    *,
    config: OpenGoldConfig | None = None,
    dry_run: bool = True,
    batch_num: int = 20,
    max_batches: int = 1,
    batch_delay: float = 0.0,
    quality: int = 0,
    push_wait: float = 2.0,
    sell_timeout: float = 8.0,
    lamp_percent: float = 0.0,
    lamp_min_keep: int = 0,
    lamp_daily_min: int = 0,
    opened_today: int = 0,
    initial_count: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    device_id: str | None = None,
) -> dict:
    """Open boxes and auto-equip winners into their matching 套裝.

    Returns {opened, equipped:[(tab,uid,reason)], sold:[(uid,reason)],
    left:[(uid,combo)], dry_run, target, initial_count, remaining}. dry_run
    (default) computes + logs everything but sends no wear/sell/choose-tab.

    Percent / min-keep (feature ON when ``lamp_percent > 0`` OR
    ``lamp_min_keep > 0`` OR ``lamp_daily_min > 0``):
      - ``target`` = how many lamps to open this run, a multiple of 20 capped
        at ``max_batches * batch_num`` (see :func:`compute_lamp_target`).
      - ``total`` comes from ``initial_count`` when given; otherwise it is
        derived lazily after the first batch from that batch's 1001006 push
        (``total = remaining + lamps_opened_in_first_batch``). In that fallback
        the min-keep floor may overshoot by up to one batch (20), because the
        true total is only known once a batch has already been opened.
      - The loop stops on ANY of: ``opened >= target``; out-of-lamps (the
        existing timeout / CMD_ERROR / empty-drops paths); or, when
        ``lamp_min_keep > 0`` and ``remaining`` is known,
        ``remaining <= lamp_min_keep`` — except this reserve stop is suspended
        while today's ``lamp_daily_min`` quota is still unmet (the daily floor
        overrides the reserve).
      - ``on_progress(opened, target)`` fires after each batch once target is
        known (a raising callback never aborts the loop).

    ``lamp_daily_min`` (>0) overrides the percentage limit when today's opened
    count (``opened_today``) has not reached the daily minimum.

    Feature OFF (all <= 0): byte-for-byte the legacy behaviour — open up to
    ``max_batches`` and stop when the server runs out of lamps; ``target`` is
    reported as ``max_batches * batch_num`` and no count is required.
    """
    config = config or OpenGoldConfig()
    parser = OCRParser(config)
    log = _resolve_logger(device_id)

    feature_on = lamp_percent > 0 or lamp_min_keep > 0 or lamp_daily_min > 0
    max_open = max_batches * batch_num
    # remaining = last-known 神燈 現量 from a 0x0402 push (None until first seen).
    remaining: int | None = None
    # total used for the target: the resolved/used 神燈 count (None until known).
    total: int | None = initial_count if feature_on else None
    # target: known up-front when feature OFF or initial_count given; otherwise
    # derived after the first batch. -1 = "not yet known" sentinel for progress.
    if not feature_on:
        target = max_open
    elif initial_count is not None:
        target = compute_lamp_target(initial_count, lamp_percent=lamp_percent,
                                     lamp_min_keep=lamp_min_keep,
                                     max_open=max_open,
                                     lamp_daily_min=lamp_daily_min,
                                     opened_today=opened_today)
    else:
        target = -1  # derive lazily from the first batch's 1001006 push

    # initial_count known and nothing to open this run -> return before any RPC.
    if feature_on and target == 0:
        log.info("ws_token lamp: target=0 (total=%s percent=%s min_keep=%s); "
                 "open nothing", initial_count, lamp_percent, lamp_min_keep)
        return {"opened": 0, "equipped": [], "sold": [], "left": [],
                "presumed_ops": 0, "failed_ops": 0,
                "dry_run": dry_run, "target": 0, "initial_count": initial_count,
                "remaining": None}

    active_tab = parse_tab_info(client.call(CMD_TAB_INFO, b""))
    set_map, lian_shan_tabs, worn = derive_set_map(
        client.call(CMD_EQUIP_INFO, b""), parser)
    log.debug("ws_token lamp: active_tab=%s sets=%s lian=%s",
              active_tab, {"".join(sorted(k)): v for k, v in set_map.items()},
              lian_shan_tabs)

    drops: dict[int, dict] = {}
    lock = threading.Lock()
    # one-element holder so the reader-thread push handler and the main loop
    # share the live 神燈 現量 without nonlocal juggling.
    remaining_box: list[int | None] = [None]

    def _push(cmd: int, body: bytes) -> None:
        if cmd == CMD_EQUIP_CHANGE:
            with lock:
                for d in parse_drops(body):
                    drops[d["uid"]] = d
        elif cmd == CMD_INVENTORY_PUSH:
            qty = extract_lamp_count(body)
            if qty is not None:
                with lock:
                    remaining_box[0] = qty

    client.set_push_handler(_push)
    equipped: list[tuple[int, int, str]] = []
    sold: list[tuple[int, str]] = []
    left: list[tuple[int, str]] = []
    opened = 0
    presumed_ops = 0   # wear/sell/choose-tab ops applied with no reply frame
    failed_ops = 0     # ops that hit a real error (connection drop etc.)

    def _run_op(cmd: int, body: bytes, what: str,
                expect_extra: tuple[int, ...] = ()) -> None:
        nonlocal presumed_ops, failed_ops
        status = _try_call(client, cmd, body, timeout=sell_timeout, what=what,
                           expect_extra=expect_extra, _log=log)
        if status == "presumed":
            presumed_ops += 1
        elif status == "failed":
            failed_ops += 1
    try:
        for batch_index in range(max_batches):
            # 開瀏覽器請求優先：每批前讓出，已開的箱是伺服器端已落地，續做時接續。
            if should_abort is not None and should_abort():
                raise WSRunAborted("開神燈中途收到中斷請求（開啟瀏覽器）")
            try:
                cmd, s2c = client.call_for(
                    CMD_OPEN_ALL, build_open_all(batch_num, quality),
                    expect_cmds=(CMD_OPEN_ALL, CMD_ERROR), timeout=10.0)
            except WSTimeoutError:
                log.info("ws_token lamp: open timed out (out of lamps?); stop")
                break
            except Exception as exc:
                log.warning("ws_token lamp: open failed (%s); stopping", exc)
                break
            if cmd == CMD_ERROR:
                log.info("ws_token lamp: open error code=%s (out of lamps?); stop",
                         codec.walk_dict(s2c).get(1))
                break
            new_uids = _extract_repeated_uint(s2c, 1)
            if not new_uids:
                break
            opened += len(new_uids)

            # Wait for this batch's drop details AND (feature ON) its 1001006
            # 神燈 現量 push, so the min-keep guard and lazy total are accurate.
            need_remaining = feature_on
            deadline = time.time() + push_wait
            while time.time() < deadline:
                with lock:
                    have_drops = all(u in drops for u in new_uids)
                    have_remaining = remaining_box[0] is not None
                if have_drops and (have_remaining or not need_remaining):
                    break
                time.sleep(0.02)
            with lock:
                remaining = remaining_box[0]

            # Lazy total derivation (initial_count was None): the first batch's
            # remaining + the lamps it just opened = the pre-run total. The
            # min-keep floor can overshoot by up to one batch here, since the
            # true total is only known after a batch has already been opened.
            if feature_on and target < 0:
                if remaining is not None:
                    total = remaining + len(new_uids)
                    target = compute_lamp_target(
                        total, lamp_percent=lamp_percent,
                        lamp_min_keep=lamp_min_keep, max_open=max_open,
                        lamp_daily_min=lamp_daily_min,
                        opened_today=opened_today)
                else:  # no count ever arrived — fall back to opening once
                    total = opened
                    target = opened

            batch_equips: list[tuple[int, int]] = []
            batch_sells: list[int] = []
            for uid in new_uids:
                with lock:
                    detail = drops.get(uid)
                if detail is None:
                    left.append((uid, "no drop detail"))
                    continue
                d = decide_v2(detail, set_map, worn, lian_shan_tabs, config, parser)
                if d.action == "equip":
                    log.info("ws_token lamp uid=%s -> EQUIP (%s)", uid, d.reason)
                    equipped.append((d.tab, uid, d.reason))
                    batch_equips.append((d.tab, uid))
                    worn.setdefault(d.tab, {})[detail["slot"]] = detail  # new worn baseline
                    if d.displaced_uid is not None:
                        sold.append((d.displaced_uid, "displaced by " + str(uid)))
                        batch_sells.append(d.displaced_uid)
                elif d.action == "sell":
                    log.debug("ws_token lamp uid=%s -> SELL (%s)", uid, d.reason)
                    sold.append((uid, d.reason))
                    batch_sells.append(uid)
                else:
                    log.debug("ws_token lamp uid=%s -> LEAVE (%s)", uid, d.reason)
                    left.append((uid, "".join(sorted(
                        frozenset(e.code for e in drop_to_equipment(detail, parser).entries)))))

            if not dry_run:
                for tab, uid in batch_equips:  # equip first so the old piece frees up
                    # Success signal for wear is the 0x0504 equip_change push,
                    # not a 0x0502 echo (which never comes) — see _try_call.
                    _run_op(CMD_WEAR, build_wear(tab, uid),
                            f"wear tab={tab} uid={uid}",
                            expect_extra=(CMD_EQUIP_CHANGE,))
                for i in range(0, len(batch_sells), _SELL_CHUNK):
                    chunk = batch_sells[i:i + _SELL_CHUNK]
                    _run_op(CMD_SELL, build_sell(chunk), f"sell {chunk}")
                    time.sleep(_SELL_DELAY_SEC)

            if feature_on and target >= 0 and on_progress is not None:
                try:
                    on_progress(opened, target)
                except Exception:
                    log.exception("ws_token lamp: on_progress callback failed")

            # Per-batch stop conditions (any one ends the run).
            if feature_on and target >= 0 and opened >= target:
                break
            # The min_keep reserve stop is suspended while today's daily floor
            # is still unmet — lamp_daily_min is a hard guarantee that overrides
            # the reserve (the target stop above caps it at the daily quota).
            daily_unmet = (lamp_daily_min > 0
                           and opened_today + opened < lamp_daily_min)
            if (lamp_min_keep > 0 and not daily_unmet and remaining is not None
                    and remaining <= lamp_min_keep):
                log.info("ws_token lamp: remaining=%d <= min_keep=%d; stop",
                         remaining, lamp_min_keep)
                break

            if batch_delay > 0 and batch_index < max_batches - 1:
                time.sleep(batch_delay)

    finally:
        if not dry_run and active_tab:
            try:
                client.call_for(CMD_CHOOSE_TAB, build_choose_tab(active_tab),
                                expect_cmds=(CMD_CHOOSE_TAB, CMD_ERROR), timeout=sell_timeout)
            except Exception as exc:
                log.warning("ws_token lamp: restore active tab %s failed (%s)", active_tab, exc)
        client.set_push_handler(None)

    if presumed_ops:
        # Silent-apply is the norm for wear/sell — log it once as INFO instead
        # of a WARNING per item. equipped/sold counts assume these applied.
        log.info(
            "ws_token lamp: %d op(s) got no reply — presumed applied "
            "(server silent-apply, unverified)", presumed_ops)
    reported_target = target if target >= 0 else 0
    if equipped or left:
        log.info("ws_token lamp: opened=%d/%d equipped=%d left=%d "
                 "presumed_ops=%d failed_ops=%d dry_run=%s",
                 opened, reported_target, len(equipped), len(left),
                 presumed_ops, failed_ops, dry_run)
    else:
        log.debug("ws_token lamp: opened=%d/%d equipped=0 sold=%d left=0 "
                  "presumed_ops=%d failed_ops=%d dry_run=%s",
                  opened, reported_target, len(sold),
                  presumed_ops, failed_ops, dry_run)
    return {"opened": opened, "equipped": equipped, "sold": sold, "left": left,
            "presumed_ops": presumed_ops, "failed_ops": failed_ops,
            "dry_run": dry_run, "target": reported_target,
            "initial_count": total, "remaining": remaining}


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

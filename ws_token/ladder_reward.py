"""雙章節天梯「每週獎勵」選擇 (double_ladder, module 64).

The double-ladder ("雲纏天梯試煉") lets each account pre-pick which weekly rewards
to claim per cleared difficulty tier. The whole selection is one WS packet:

  double_ladder_select  0x4001 (16385)
    c2s body = repeated pick { difficulty#1:uint32, index#2:uint32 }
    s2c echo = the stored pick list, with a trailing {result#2:0} on success.

Captured live (小寶 2026-06-21, CDP 9226): 25 picks across difficulties 16-25;
re-sending the SAME body re-stores the same picks (echo identical + 0x10 0x00),
so the call is idempotent. 5558 accepted the same body live (CDP 9224, echo 25).

The selection resets each weekly settlement, so it is re-applied **once every
Tuesday** (user policy 2026-06-21). The send goes over the H5 page WS
(``WebGameAPI.call_raw`` — the CDP path) so it works for plain web_h5 devices
without ws_token creds; ``apply_if_due`` also accepts a pure-WS ``client`` for
ws_token devices that prefer the headless path.

This module owns: pick (de)serialisation, the per-device captured body store
(``ws_token/data/ladder_reward.json``), the Tuesday-once gate, and the send.
It never reads the clock itself — callers pass ``today`` (a ``datetime.date``).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

CMD_SELECT = 0x4001        # double_ladder_select c2s/s2c
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}
TUESDAY = 2                # date.isocalendar() weekday: Mon=1 .. Sun=7
TEMPLATE_DEVICE = "7fe98fc6"  # 同一選擇 body 已 live 驗證可跨帳號套用

_STORE = Path(__file__).with_name("data") / "ladder_reward.json"

Pick = tuple[int, int]     # (difficulty, index)


# --- protobuf varint + pick (de)serialisation -----------------------------

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        b = buf[i]; i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7


def encode_picks(picks: Iterable[Pick]) -> bytes:
    """Serialise (difficulty, index) pairs into a double_ladder_select body."""
    out = bytearray()
    for diff, idx in picks:
        sub = b"\x08" + _varint(diff) + b"\x10" + _varint(idx)
        out += b"\x0a" + _varint(len(sub)) + sub   # field 1, length-delimited
    return bytes(out)


def decode_picks(body: bytes) -> list[Pick]:
    """Parse a double_ladder_select body into (difficulty, index) pairs.

    Stops at the first non field-1 tag (e.g. the trailing top-level result), so
    it reads both c2s requests and s2c echoes.
    """
    picks: list[Pick] = []
    i = 0
    while i < len(body):
        if body[i] != 0x0a:    # not field-1 embedded msg -> end of pick list
            break
        i += 1
        ln, i = _read_varint(body, i)
        sub, i = body[i:i + ln], i + ln
        diff = idx = 0
        j = 0
        while j < len(sub):
            tag = sub[j]; j += 1
            v, j = _read_varint(sub, j)
            if tag == 0x08:
                diff = v
            elif tag == 0x10:
                idx = v
        picks.append((diff, idx))
    return picks


def merge_picks(base: Iterable[Pick], fill: Iterable[Pick]) -> list[Pick]:
    """Union of two pick sets; ``base`` wins, ``fill`` adds any missing pair.

    Used to top up a device that has not selected all its tiers, using another
    account's picks as the template. Sorted by (difficulty desc, index).
    """
    seen = set(base)
    seen.update(fill)
    return sorted(seen, key=lambda p: (-p[0], p[1]))


# --- per-device record store ----------------------------------------------

def load_store() -> dict:
    if _STORE.exists():
        return json.loads(_STORE.read_text(encoding="utf-8"))
    return {}


def save_store(store: dict) -> None:
    # Atomic write (tmp + os.replace): this is a single shared file for all
    # devices, so a torn write would corrupt every device's record at once.
    # ponytail: atomic write only; concurrent read-modify-write lost-update is
    # benign here (the select is idempotent and runs once/week), add an
    # in-process lock if cross-device same-second writes ever matter.
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{_STORE}.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, _STORE)


def get_body_hex(device: str) -> Optional[str]:
    store = load_store()
    rec = store.get(device) or store.get(TEMPLATE_DEVICE)
    return rec.get("body_hex") if rec else None


def _record_for_device(store: dict, device: str) -> Optional[dict]:
    """裝置無專屬選擇時沿用 live 驗證過的共用模板。

    0x4001 body 只描述「難度→獎勵索引」，不含 role id；同一 body 已在小寶與
    5558 兩帳號成功回聲。成功套用後會複製成裝置自己的紀錄與週 marker。
    """
    rec = store.get(device)
    if rec:
        return dict(rec)
    template = store.get(TEMPLATE_DEVICE)
    if not template or not template.get("body_hex"):
        return None
    return {
        "body_hex": template["body_hex"],
        "picks": template.get("picks"),
        "captured": template.get("captured"),
        "source": f"shared template: {TEMPLATE_DEVICE}",
        "enabled": True,
    }


def record_device(device: str, body_hex: str, *, captured: str,
                  source: Optional[str] = None) -> dict:
    """Persist a device's captured selection. ``captured`` is a YYYY-MM-DD str
    (callers pass it; this module never reads the clock)."""
    store = load_store()
    store[device] = {
        "body_hex": body_hex,
        "picks": len(decode_picks(bytes.fromhex(body_hex))),
        "captured": captured,
        "enabled": (store.get(device) or {}).get("enabled", True),
        **({"source": source} if source else {}),
    }
    save_store(store)
    return store[device]


# --- Tuesday-once gate -----------------------------------------------------

def _marker(today) -> tuple[str, int]:
    """Return (ISO-week marker 'YYYY-Www', isocalendar weekday 1..7)."""
    iso = today.isocalendar()
    year, week, weekday = iso[0], iso[1], iso[2]
    return f"{year}-W{week:02d}", weekday


def is_due(device: str, today, *, weekday_target: int = TUESDAY) -> tuple[bool, str]:
    """True iff ``device`` should apply its selection on ``today`` (a date).

    Gate: it is the target weekday, a record exists + is enabled + has a body,
    and this ISO week has not been applied yet. Returns (due, reason/marker).
    """
    marker, weekday = _marker(today)
    if weekday != weekday_target:
        return False, "not_target_weekday"
    rec = _record_for_device(load_store(), device)
    if not rec:
        return False, "no_record"
    if not rec.get("enabled", True):
        return False, "disabled"
    if not rec.get("body_hex"):
        return False, "no_body"
    if rec.get("last_applied_week") == marker:
        return False, "already_this_week"
    return True, marker


# --- send + apply ----------------------------------------------------------

def _send_via_page(page, body: bytes) -> bool:
    """Send 0x4001 over the H5 page WS (CDP path). True iff the echo has picks."""
    from utils.web_game_api import WebGameAPI
    try:
        resp = WebGameAPI(page).call_raw(CMD_SELECT, body)
        return len(decode_picks(resp)) > 0
    except Exception as exc:  # noqa: BLE001 - never abort the surrounding pipeline
        logger.warning("ladder_reward: page send failed: %s", exc)
        return False


def _send_via_client(client, body: bytes) -> bool:
    """Send 0x4001 over a pure-WS client. True iff the reply is the echo (not error)."""
    try:
        reply_cmd, _reply = client.call_for(
            CMD_SELECT, body, expect_cmds=(CMD_SELECT, CMD_ERROR))
        return reply_cmd == CMD_SELECT
    except Exception as exc:  # noqa: BLE001
        logger.warning("ladder_reward: ws client send failed: %s", exc)
        return False


def apply_if_due(device: str, today, *, page=None, client=None,
                 force: bool = False) -> dict:
    """Apply ``device``'s recorded selection if due (or ``force``).

    Pass ``page`` for the CDP path (web_h5) or ``client`` for pure WS. Marks the
    ISO week on success so the same week never re-applies (this dedupes the case
    where both a ws-phase and the daily pipeline reach this for one device).
    """
    if force:
        rec = _record_for_device(load_store(), device)
        if not rec or not rec.get("body_hex"):
            return {"skipped": "no_body"}
        marker = _marker(today)[0]
    else:
        due, reason = is_due(device, today)
        if not due:
            return {"skipped": reason}
        marker = reason
        rec = _record_for_device(load_store(), device)
        if not rec:
            return {"skipped": "no_record"}

    body = bytes.fromhex(rec["body_hex"])
    if page is not None:
        ok = _send_via_page(page, body)
    elif client is not None:
        ok = _send_via_client(client, body)
    else:
        return {"skipped": "no_transport"}
    if not ok:
        return {"ok": False, "error": "send_failed"}

    store = load_store()
    store.setdefault(device, rec)
    store[device]["last_applied_week"] = marker
    store[device]["last_applied_date"] = today.isoformat()
    save_store(store)
    logger.info("ladder_reward: applied %d picks for %s (%s)",
                len(decode_picks(body)), device, marker)
    return {"ok": True, "picks": len(decode_picks(body)), "week": marker}

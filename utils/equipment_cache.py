"""Equipment cache — passively builds a UID→affix lookup table from
0x0504 lamp drop captures.

Use case: bot needs to compare a new lamp drop against the currently-
equipped item in the same slot. The currently-equipped UID can be queried
via 0x0e0a (preset detail), but the affixes / base stats are not exposed
on demand. Instead we cache them as they fly past in 0x0504 push frames
and persist to disk so the cache grows across bot sessions.

Schema mismatch note: ``parse_lamp_drops`` in ``web_game_api`` was
originally written for non-equipment lamp results (sf6 = pool weights,
sf7 = rewards). The same wire shape encodes equipment differently —
sf6 = base stats, sf7 = affixes. This module re-parses the body using
the equipment interpretation.

Usage::

    cache = EquipmentCache.load_default()
    n = cache.feed_lamp_drop_body(open("cmd_0x0504_rx_*.bin", "rb").read())
    print(cache.stats())
    item = cache.lookup_by_uid(89617608041738)

Used by ``tools/build_equipment_cache.py`` for batch backfill from
on-disk samples produced by ``WSFrameTracker``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.json_io import read_json_bom_safe
from utils.web_game_api import (
    _walk_pb,
    decode_equip_template,
    EQUIP_AFFIX,
    EQUIP_RARITY,
    EQUIP_SLOT,
    EQUIP_STAT,
)


# Default location, relative to project root.
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "equipment_cache.json"


def _is_equipment_template(template_id: int) -> bool:
    """Equipment template_ids encode (rarity, slot, item) as
    rarity*1e7 + slot*1e5 + item. Valid ranges: rarity 1..11, slot 1..10."""
    if template_id < 10_000_000 or template_id >= 130_000_000:
        return False
    info = decode_equip_template(template_id)
    return 1 <= info["rarity"] <= 11 and 1 <= info["slot"] <= 10


def parse_equipment_lamp_drops(body: bytes) -> List[Dict[str, Any]]:
    """Extract equipment entries from a 0x0504 rx body.

    Returns a list of dicts, one per equipment lamp drop. Non-equipment
    lamp entries (currency / item-only drops) are skipped.

    Each entry::

        {
          'uid': int,                  # sf1 drop_uid
          'template_id': int,          # sf2
          'rarity': int, 'rarity_name': str,
          'slot': int, 'slot_name': str,
          'item_id': int,
          'position': int,             # sf3
          'base_stats': {stat_id: value},     # sf6 entries (1001 攻 / 1002 血 / 1003 攻速 / 1024 防)
          'affixes': {affix_id: value},       # sf7 entries (1004 爆 / 1008 閃 / etc.) — value÷100 = display %
          'bonus': int | None,                # sf9
        }
    """
    out: List[Dict[str, Any]] = []
    if not body:
        return out
    for fnum, wire, val in _walk_pb(body):
        if fnum == 3 and wire == 2:
            entry = _parse_equipment_entry(val)
            if entry is not None:
                out.append(entry)
    return out


def _parse_equipment_entry(sub: bytes) -> Optional[Dict[str, Any]]:
    uid: Optional[int] = None
    template_id: Optional[int] = None
    position: Optional[int] = None
    base_stats: Dict[int, int] = {}
    affixes: Dict[int, int] = {}
    bonus: Optional[int] = None
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            uid = sv
        elif sf == 2 and sw == 0:
            template_id = sv
        elif sf == 3 and sw == 0:
            position = sv
        elif sf == 6 and sw == 2:
            stat_id, value = _parse_id_value(sv)
            if stat_id is not None:
                base_stats[stat_id] = value
        elif sf == 7 and sw == 2:
            affix_id, value = _parse_id_value(sv)
            if affix_id is not None:
                affixes[affix_id] = value
        elif sf == 9 and sw == 0:
            bonus = sv
    if template_id is None or uid is None:
        return None
    if not _is_equipment_template(template_id):
        return None
    info = decode_equip_template(template_id)
    return {
        "uid": uid,
        "template_id": template_id,
        "rarity": info["rarity"],
        "rarity_name": info["rarity_name"],
        "slot": info["slot"],
        "slot_name": info["slot_name"],
        "item_id": info["item_id"],
        "position": position,
        "base_stats": base_stats,
        "affixes": affixes,
        "bonus": bonus,
    }


def _parse_id_value(sub: bytes) -> tuple:
    """Inner {f1=id, f2=value} pair."""
    fid: Optional[int] = None
    val: int = 0
    for sf, sw, sv in _walk_pb(sub):
        if sf == 1 and sw == 0:
            fid = sv
        elif sf == 2 and sw == 0:
            val = sv
    return fid, val


class EquipmentCache:
    """UID→equipment-detail lookup, persisted as JSON.

    Bot pipeline::

        cache = EquipmentCache.load_default()
        # When 0x0504 body arrives (lamp drop push):
        cache.feed_lamp_drop_body(body)
        # When deciding keep vs sell, look up currently-equipped item:
        equipped = cache.lookup_by_uid(equipped_uid)
        if equipped is None:
            # No cache yet → fall back to default rule (e.g. always keep
            # higher rarity).
            ...
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_CACHE_PATH
        self.by_uid: Dict[int, Dict[str, Any]] = {}
        self.created_at: Optional[int] = None
        self.last_update: Optional[int] = None

    # ── persistence ─────────────────────────────────────────────────

    @classmethod
    def load_default(cls) -> "EquipmentCache":
        cache = cls()
        cache.load()
        return cache

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = read_json_bom_safe(self.path)
        except (OSError, json.JSONDecodeError):
            return
        if data.get("schema_version") != self.SCHEMA_VERSION:
            return
        self.created_at = data.get("created_at")
        self.last_update = data.get("last_update")
        # JSON object keys are str — convert UIDs back to int.
        self.by_uid = {int(k): v for k, v in data.get("by_uid", {}).items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time() * 1000)
        if self.created_at is None:
            self.created_at = now
        self.last_update = now
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "created_at": self.created_at,
            "last_update": self.last_update,
            "by_uid": {str(k): v for k, v in self.by_uid.items()},
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── ingest ──────────────────────────────────────────────────────

    def feed_lamp_drop_body(self, body: bytes, *, save: bool = True) -> int:
        """Parse a 0x0504 body and add equipment entries. Returns count
        of new (not previously cached) UIDs."""
        entries = parse_equipment_lamp_drops(body)
        added = 0
        now = int(time.time() * 1000)
        for entry in entries:
            uid = entry["uid"]
            # Convert int-keyed sub-dicts to str-keyed for JSON compat.
            entry_record = {
                **entry,
                "base_stats": {str(k): v for k, v in entry["base_stats"].items()},
                "affixes": {str(k): v for k, v in entry["affixes"].items()},
                "captured_at": now,
            }
            if uid not in self.by_uid:
                added += 1
            self.by_uid[uid] = entry_record
        if added and save:
            self.save()
        return added

    # ── query ───────────────────────────────────────────────────────

    def lookup_by_uid(self, uid: int) -> Optional[Dict[str, Any]]:
        return self.by_uid.get(int(uid))

    def stats(self) -> Dict[str, Any]:
        from collections import Counter
        rarity = Counter()
        slot = Counter()
        for v in self.by_uid.values():
            rarity[v.get("rarity_name", "?")] += 1
            slot[v.get("slot_name", "?")] += 1
        return {
            "total": len(self.by_uid),
            "by_rarity": dict(rarity),
            "by_slot": dict(slot),
            "created_at": self.created_at,
            "last_update": self.last_update,
        }

    def describe(self, entry: Dict[str, Any]) -> str:
        """Short human-readable string for a cached entry."""
        rarity = entry.get("rarity_name", "?")
        slot = entry.get("slot_name", "?")
        affixes_str = ", ".join(
            f"{EQUIP_AFFIX.get(int(k), f'a{k}')}={v/100:.2f}%"
            for k, v in entry.get("affixes", {}).items()
        )
        return f"{rarity} {slot} [{affixes_str}]"

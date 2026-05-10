"""Scan auto-captured 0x0504 lamp drops, populate equipment cache.

Backfills ``cache/equipment_cache.json`` from samples that
``WSFrameTracker`` writes to ``tmp_ws_capture/auto/<device>/``.

Run periodically (e.g. nightly cron, or on bot startup) to keep cache
warm. New lamp drops also feed it automatically when the bot's WS
listener is active and ``EquipmentCache`` is wired into the lamp pipeline.

Usage::

    python -m tools.build_equipment_cache
    python -m tools.build_equipment_cache --capture-dir tmp_ws_capture/auto
    python -m tools.build_equipment_cache --reset  # discard existing cache first

The bot itself is not touched — this is a passive backfill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python -m tools.build_equipment_cache` from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.equipment_cache import EquipmentCache, DEFAULT_CACHE_PATH


def _iter_0x0504_files(root: Path):
    """Yield 0x0504_rx files under root, recursively, oldest-first by ts."""
    if not root.exists():
        return
    files = list(root.rglob("cmd_0x0504_rx_*.bin"))
    # Skip Syncthing conflict copies — they may be truncated.
    files = [f for f in files if "sync-conflict" not in f.name]
    # Filename ts is the 4th underscore segment.
    def _ts(p: Path) -> int:
        try:
            return int(p.stem.split("_")[3])
        except (IndexError, ValueError):
            return 0
    files.sort(key=_ts)
    yield from files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture-dir",
        default="tmp_ws_capture/auto",
        help="Directory containing per-device captures (default: tmp_ws_capture/auto)",
    )
    parser.add_argument(
        "--cache-path",
        default=str(DEFAULT_CACHE_PATH),
        help=f"Cache JSON path (default: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard existing cache before populating",
    )
    args = parser.parse_args()

    cache_path = Path(args.cache_path)
    if args.reset and cache_path.exists():
        cache_path.unlink()

    cache = EquipmentCache(path=cache_path)
    cache.load()
    before = len(cache.by_uid)

    capture_root = Path(args.capture_dir)
    files = list(_iter_0x0504_files(capture_root))
    if not files:
        print(f"no 0x0504 files under {capture_root}", file=sys.stderr)
        return 1

    total_added = 0
    files_with_equip = 0
    for fp in files:
        try:
            body = fp.read_bytes()
        except OSError as exc:
            print(f"  skip {fp}: {exc}", file=sys.stderr)
            continue
        # Don't save per-file — single batch save at end is faster.
        added = cache.feed_lamp_drop_body(body, save=False)
        if added:
            files_with_equip += 1
            total_added += added

    if total_added:
        cache.save()

    print(f"scanned {len(files)} files, {files_with_equip} contained equipment")
    print(f"cache: {before} → {len(cache.by_uid)} entries (+{len(cache.by_uid) - before})")
    print()
    s = cache.stats()
    print(f"by rarity: {s['by_rarity']}")
    print(f"by slot:   {s['by_slot']}")
    print(f"saved to:  {cache_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

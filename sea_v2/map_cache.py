"""Shared, cross-account cache of the season map.

The season map is server-global (static ``configMap`` / season-4 layout), so the target
tile coordinates parsed by any H5 account are valid for every account on the same server.
We persist them — plus each account's own base position — to a shared JSON so the lone
ADB account, which cannot read the cocos scene, can still navigate.

Schema::

    {
      "season": "s4",
      "targets": [{"name": "resource_1", "wx": -31364, "wy": -1709}, ...],
      "account_base": {"emulator-5554": {"wx": -31910, "wy": -1867}}
    }

Writes are atomic (temp file + replace) and never emit ``.pyc`` — safe on the NAS/SMB
checkout. All mutation helpers operate on a plain dict so they are trivially testable.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from sea_v2.tiles import Tile

Point = Tuple[float, float]


def empty_cache(season: Optional[str] = None) -> dict:
    return {"season": season, "targets": [], "account_base": {}}


def default_cache_path() -> Path:
    """Shared location alongside the package (the repo dir is what Syncthing replicates)."""
    return Path(__file__).resolve().parent / "shared_map.json"


def load(path) -> dict:
    """Read the cache, returning a fresh empty cache on missing/corrupt/partial files."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return empty_cache()
    if not isinstance(raw, dict):
        return empty_cache()
    base = empty_cache()
    base.update({
        "season": raw.get("season"),
        "targets": raw.get("targets") or [],
        "account_base": raw.get("account_base") or {},
    })
    return base


def save(path, cache: dict) -> None:
    """Atomically write ``cache`` as UTF-8 JSON, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".sea_map_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def record_account_base(cache: dict, account_id: str, base_wp: Point) -> dict:
    cache.setdefault("account_base", {})[account_id] = {"wx": base_wp[0], "wy": base_wp[1]}
    return cache


def get_account_base(cache: dict, account_id: str) -> Optional[Point]:
    rec = (cache.get("account_base") or {}).get(account_id)
    if not rec:
        return None
    return (rec["wx"], rec["wy"])


def record_targets(cache: dict, tiles: Sequence[Tile]) -> dict:
    """Merge server-global target tiles into the cache, deduping by (name, wx, wy)."""
    targets = cache.setdefault("targets", [])
    seen = {(t["name"], t["wx"], t["wy"]) for t in targets}
    for tile in tiles:
        key = (tile.name, tile.wx, tile.wy)
        if key not in seen:
            targets.append({"name": tile.name, "wx": tile.wx, "wy": tile.wy})
            seen.add(key)
    return cache


def get_targets(cache: dict, type_name: str) -> List[Point]:
    return [(t["wx"], t["wy"]) for t in cache.get("targets", []) if t["name"] == type_name]

"""Tiny per-device JSON state store for ws_token runner cadence tracking.

Used by the runner for things that must survive across runs without a server
read — e.g. the workshop 12h recipe-rotation timestamp/parity. One file per
device under ``ws_state/`` (repo root, gitignored-friendly), UTF-8, BOM
tolerated on read. Corrupt/missing state degrades to {} — callers must treat
state as advisory, never required.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parents[1] / "ws_state"


def load_state(device: str, *, state_dir: Path = STATE_DIR) -> dict:
    """Load ``ws_state/<device>.json``; missing or corrupt -> {}."""
    path = Path(state_dir) / f"{device}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        logger.warning("ws_token state: corrupt state file %s — treating as empty",
                       path, exc_info=True)
        return {}


def save_state(device: str, data: dict, *, state_dir: Path = STATE_DIR) -> None:
    """Write ``ws_state/<device>.json`` (creates the dir on first use)."""
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{device}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

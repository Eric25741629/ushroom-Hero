"""JSONL recorder for every coordinate click issued by carpark_auto.

Purpose: capture (x, y) + context for each click during a Playwright-driven
carpark flow so the same sequence can be replayed (or at least informed) on
an ADB device that has no cocos JS access. The game rejects raw WS sends
(protobuf wrapper enforced), so an ADB port of carpark would still need to
drive the UI — these recordings are the closest thing to a replay tape.

Usage:

    from utils.carpark_click_recorder import (
        CarparkClickRecorder, set_recorder, clear_recorder,
    )
    rec = CarparkClickRecorder("emulator-5554", run_tag="manual-1")
    set_recorder(rec)
    try:
        park_one_silver(page, ...)  # carpark_auto picks up the thread-local
    finally:
        rec.close()
        clear_recorder()

Each line in the JSONL: {ts, device, run_tag, action, x, y, in_view,
button_path?, lot_idx?, slot_idx?, ...extras}.

Thread-local — different device threads can set their own recorder without
collision.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from utils.log_paths import LogPaths

logger = logging.getLogger(__name__)

_local = threading.local()


class CarparkClickRecorder:
    """Append-only JSONL recorder for one carpark run."""

    def __init__(self, device_id: str, run_tag: str = "") -> None:
        self.device_id = device_id
        self.run_tag = run_tag or time.strftime("%Y%m%d_%H%M%S")
        path = LogPaths.device_root(device_id) / "carpark_clicks.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("a", encoding="utf-8", buffering=1)  # line-buffered
        self._lock = threading.Lock()
        self.record(action="run_start", x=None, y=None, in_view=None)

    def record(
        self,
        *,
        action: str,
        x: Optional[int],
        y: Optional[int],
        in_view: Optional[bool] = None,
        **extras: Any,
    ) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "device": self.device_id,
            "run_tag": self.run_tag,
            "action": action,
            "x": x,
            "y": y,
            "in_view": in_view,
        }
        for k, v in extras.items():
            if v is not None:
                entry[k] = v
        try:
            with self._lock:
                self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[carpark_click_recorder] write failed: {e}")

    def close(self) -> None:
        try:
            self.record(action="run_end", x=None, y=None, in_view=None)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass


def set_recorder(rec: Optional[CarparkClickRecorder]) -> None:
    _local.rec = rec


def get_recorder() -> Optional[CarparkClickRecorder]:
    return getattr(_local, "rec", None)


def clear_recorder() -> None:
    _local.rec = None

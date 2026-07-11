"""Per-account mining map recorder.

Persistently records every mining round (board snapshot, planned steps, execution
outcome, inventory) so that, per device/account, we can:

1. Rebuild the cumulative vertical map explored so far (``global_map.json``).
2. Replay any single mining session step by step (``session_*.jsonl``).

Layout (via :class:`utils.log_paths.LogPaths`):

    logs/<device>/mining_map/
    ├── session_YYYYMMDD_HHMMSS.jsonl   # one mining run = one file
    └── global_map.json                 # cumulative map (kept forever)

Hard rule: recording must NEVER interfere with the live mining loop. Every public
method swallows exceptions and logs a single warning. When ``enabled`` is False the
recorder creates nothing on disk.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.log_paths import LogPaths

logger = logging.getLogger(__name__)

# session JSONL retention (independent of the 7-day device-log retention — the
# user wants long-lived replayable history). global_map.json is never purged.
MAP_SESSION_RETENTION_DAYS = 90

# 盤面壓縮字元對照表（雙向；記錄/回放共用）。大寫 = 不可達，小寫/符號 = 可達。
#   .  可達空氣      ,  不可達空氣
#   d  dirt         D  unreachable_dirt
#   r  rock         R  unreachable_rock
#   P  可達礦坑      p  不可達礦坑
#   x  已挖礦坑      ?  未知
LABEL_TO_CHAR: Dict[str, str] = {
    "empty": ".",
    "void": ".",
    "unreachable_empty": ",",
    "unreachable_void": ",",
    "dirt": "d",
    "unreachable_dirt": "D",
    "rock": "r",
    "unreachable_rock": "R",
    "reachable_pit": "P",
    "pit": "P",
    "unreachable_pit": "p",
    "dug_pit": "x",
}
UNKNOWN_CHAR = "?"

# 反向表回傳「典範標籤」；多對一的來源標籤（void/empty）會收斂到單一典範值，
# 因此 char 層級的 round-trip（compress→decompress→compress）恆等。
CHAR_TO_LABEL: Dict[str, str] = {
    ".": "empty",
    ",": "unreachable_empty",
    "d": "dirt",
    "D": "unreachable_dirt",
    "r": "rock",
    "R": "unreachable_rock",
    "P": "reachable_pit",
    "p": "unreachable_pit",
    "x": "dug_pit",
    UNKNOWN_CHAR: "unknown",
}


def compress_row(cells: Sequence[str]) -> str:
    """Map a row of planner/CNN labels to a compact char string."""
    return "".join(LABEL_TO_CHAR.get(str(cell), UNKNOWN_CHAR) for cell in cells)


def decompress_row(row: str) -> List[str]:
    """Inverse of :func:`compress_row`, returning canonical labels."""
    return [CHAR_TO_LABEL.get(ch, "unknown") for ch in row]


def compress_board(board: Sequence[Sequence[str]]) -> List[str]:
    return [compress_row(row) for row in board]


class MiningMapRecorder:
    """Records one mining run's rounds + updates the cumulative global map.

    All disk work is best-effort; a single failure warns once and then the
    recorder goes quiet for the rest of the run (``self._broken``).
    """

    def __init__(
        self,
        device_id: str,
        backend: str,
        *,
        enabled: bool = True,
        log_paths: Any = None,
    ) -> None:
        self.device_id = device_id
        self.backend = backend
        self.enabled = bool(enabled)
        self._lp = log_paths or LogPaths
        self._session_path: Optional[Path] = None
        self._broken = False
        self._totals: Dict[str, int] = {
            "rounds": 0,
            "shovels": 0,
            "bombs": 0,
            "drills": 0,
        }

    # -- factory ---------------------------------------------------------
    @classmethod
    def for_device(
        cls,
        device_id: str,
        backend: str,
        *,
        log_paths: Any = None,
    ) -> "MiningMapRecorder":
        """Build a recorder honouring the device's ``mining_map_record`` flag.

        Never raises; if config can't be read the default is ON (the user wants
        every account recorded going forward)."""
        enabled = True
        try:
            import config_manager

            cfg = config_manager.get_device_config(device_id)
            enabled = bool(cfg.get("mining_map_record", True))
        except Exception:
            enabled = True
        return cls(device_id, backend, enabled=enabled, log_paths=log_paths)

    # -- internal helpers ------------------------------------------------
    def _warn_once(self, action: str, exc: Exception) -> None:
        if not self._broken:
            self._broken = True
            logger.warning(
                "[MiningMapRecorder] %s failed for %s, disabling recorder for this run: %s",
                action, self.device_id, exc,
            )

    @property
    def _map_dir(self) -> Path:
        return self._lp.mining_map_dir(self.device_id)

    def _global_map_path(self) -> Path:
        return self._map_dir / "global_map.json"

    def _write_event(self, event: Dict[str, Any]) -> None:
        if self._session_path is None:
            return
        line = json.dumps(event, ensure_ascii=False)
        with open(self._session_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _purge_old_sessions(self) -> None:
        cutoff = time.time() - MAP_SESSION_RETENTION_DAYS * 86400
        for path in glob.glob(str(self._map_dir / "session_*.jsonl")):
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                continue

    def _update_global_map(
        self,
        depth: int,
        board_rows: List[str],
        below_rows: List[str],
    ) -> None:
        path = self._global_map_path()
        data: Dict[str, Any] = {"rows": {}, "max_depth": 0, "updated_at": 0}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data["rows"] = dict(loaded.get("rows") or {})
                    data["max_depth"] = int(loaded.get("max_depth") or 0)
            except (OSError, ValueError):
                data = {"rows": {}, "max_depth": 0, "updated_at": 0}
        rows: Dict[str, str] = data["rows"]
        # 後寫覆蓋先寫：可見列最權威，故最後才寫；below 先鋪、可見列覆蓋重疊處。
        for j, row in enumerate(below_rows):
            rows[str(depth + len(board_rows) + j)] = row
        for i, row in enumerate(board_rows):
            rows[str(depth + i)] = row
        max_key = max((int(k) for k in rows), default=0)
        data["rows"] = rows
        data["max_depth"] = max(data["max_depth"], max_key)
        data["updated_at"] = time.time()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    # -- public API ------------------------------------------------------
    def start(
        self,
        *,
        planner: Optional[str] = None,
        depth_base: Optional[int] = None,
        inv: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled or self._broken:
            return
        try:
            self._map_dir.mkdir(parents=True, exist_ok=True)
            self._purge_old_sessions()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._session_path = self._map_dir / f"session_{stamp}.jsonl"
            self._write_event({
                "ev": "start",
                "ts": time.time(),
                "backend": self.backend,
                "planner": planner,
                "depth_base": depth_base,
                "inv": dict(inv) if inv else {},
            })
        except Exception as exc:  # never break mining
            self._warn_once("start", exc)

    def round(
        self,
        *,
        depth: int,
        uncertain: bool,
        board: Sequence[Sequence[str]],
        below: Optional[Sequence[Sequence[str]]] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        exec: Optional[Dict[str, Any]] = None,  # noqa: A002 - matches spec field
        inv: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled or self._broken or self._session_path is None:
            return
        try:
            board_rows = compress_board(board)
            below_rows = compress_board(below) if below else []
            event: Dict[str, Any] = {
                "ev": "round",
                "ts": time.time(),
                "depth": int(depth),
                "uncertain": bool(uncertain),
                "board": board_rows,
            }
            if below_rows:
                event["below"] = below_rows
            if steps is not None:
                event["steps"] = steps
            if exec is not None:
                event["exec"] = exec
            if inv is not None:
                event["inv"] = dict(inv)
            self._write_event(event)

            self._totals["rounds"] += 1
            if isinstance(exec, dict):
                self._totals["shovels"] += int(exec.get("shovels") or 0)
                self._totals["bombs"] += int(exec.get("bombs") or 0)
                self._totals["drills"] += int(exec.get("drills") or 0)

            # uncertain 深度不對齊全圖（可能重複列），只留 session。
            if not uncertain:
                self._update_global_map(int(depth), board_rows, below_rows)
        except Exception as exc:
            self._warn_once("round", exc)

    def end(self, totals: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled or self._broken or self._session_path is None:
            return
        try:
            merged = dict(self._totals)
            if totals:
                merged.update(totals)
            self._write_event({"ev": "end", "ts": time.time(), "totals": merged})
        except Exception as exc:
            self._warn_once("end", exc)
        finally:
            self._session_path = None


__all__ = [
    "MiningMapRecorder",
    "LABEL_TO_CHAR",
    "CHAR_TO_LABEL",
    "UNKNOWN_CHAR",
    "MAP_SESSION_RETENTION_DAYS",
    "compress_row",
    "decompress_row",
    "compress_board",
]

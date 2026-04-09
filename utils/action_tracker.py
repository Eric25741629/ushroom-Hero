from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional
import bot_state


def _sanitize_filename_part(value: object) -> str:
    text = str(value or "unknown").strip()
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        text = text.replace(ch, "_")
    text = "_".join(text.split())
    return text[:80] or "unknown"


class ActionTraceRecorder:
    """Append-only tracker for click/screenshot/action events."""

    def __init__(self, base_dir: str = "logs/action_trace") -> None:
        self.base_dir = Path(base_dir)
        self._lock = threading.Lock()

    def _event_file(self, device_id: str) -> Path:
        return self.base_dir / _sanitize_filename_part(device_id) / "events.jsonl"

    def _caller_info(self, skip_files: Optional[set[str]] = None) -> dict[str, Any]:
        skip = {"action_tracker.py", "device_wrapper.py", "smart_screenshot.py"}
        skip.update(skip_files or set())
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back
        while frame is not None:
            path = os.path.abspath(frame.f_code.co_filename)
            name = os.path.basename(path).lower()
            if name not in skip:
                return {
                    "file": path,
                    "line": int(frame.f_lineno),
                    "function": frame.f_code.co_name,
                    "module": frame.f_globals.get("__name__", ""),
                }
            frame = frame.f_back
        return {"file": "", "line": 0, "function": "", "module": ""}

    def log(
        self,
        *,
        device_id: str,
        event_type: str,
        source: str,
        payload: Optional[dict[str, Any]] = None,
        meaning: str = "",
        actor: str = "",
    ) -> None:
        caller = self._caller_info()
        evt = {
            "timestamp": dt.datetime.now().isoformat(),
            "device_id": str(device_id),
            "event_type": str(event_type),
            "source": str(source),
            "meaning": str(meaning or ""),
            "actor": str(actor or ""),
            "caller": caller,
            "thread": {
                "name": threading.current_thread().name,
                "id": threading.get_ident(),
            },
            "payload": payload or {},
        }
        try:
            st = (bot_state.get_all_states() or {}).get(str(device_id), {})
            evt["device_context"] = {
                "task": st.get("task", ""),
                "step": st.get("step", ""),
                "status": st.get("status", ""),
            }
        except Exception:
            evt["device_context"] = {"task": "", "step": "", "status": ""}
        fp = self._event_file(device_id)
        fp.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(evt, ensure_ascii=False)
        with self._lock:
            with fp.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

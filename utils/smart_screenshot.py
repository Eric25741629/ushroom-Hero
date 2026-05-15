from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from utils.action_tracker import ActionTraceRecorder
from utils.log_paths import LogPaths


def _sanitize_filename_part(value: object) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", text)
    return text[:80] or "unknown"


class SmartScreenshotRecorder:
    """Capture screenshot + persist structured event + annotation metadata.

    Layout: logs/<device>/error_screenshots/{*.jpg, events.jsonl, annotations.json}.
    Pass `base_dir` only for tests — production code should leave it None
    so paths flow through `LogPaths`.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else None
        self._tracker = ActionTraceRecorder()

    def _device_dir(self, device_id: str) -> Path:
        if self.base_dir is not None:
            return self.base_dir / _sanitize_filename_part(device_id)
        return LogPaths.error_screenshots_dir(device_id)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _save_image_unicode_safe(self, image_path: str, img: Any) -> bool:
        # cv2.imwrite may fail on Windows non-ASCII paths; use imencode + tofile fallback.
        try:
            if cv2.imwrite(image_path, img):
                return True
        except Exception:
            pass

        ext = Path(image_path).suffix.lower() or ".jpg"
        ext = ext if ext.startswith(".") else f".{ext}"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        if not isinstance(buf, np.ndarray):
            return False
        buf.tofile(image_path)
        return Path(image_path).exists() and Path(image_path).stat().st_size > 0

    def _caller_info(self) -> dict[str, Any]:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back
        skip = {"smart_screenshot.py"}
        while frame is not None:
            path = os.path.abspath(frame.f_code.co_filename)
            if os.path.basename(path).lower() not in skip:
                return {
                    "file": path,
                    "line": int(frame.f_lineno),
                    "function": frame.f_code.co_name,
                    "module": frame.f_globals.get("__name__", ""),
                }
            frame = frame.f_back
        return {"file": "", "line": 0, "function": "", "module": ""}

    def _upsert_annotation(self, device_dir: Path, image_path: str, note: dict[str, Any]) -> None:
        ann_path = device_dir / "annotations.json"
        current: dict[str, Any] = {}
        if ann_path.exists():
            try:
                current = json.loads(ann_path.read_text(encoding="utf-8-sig"))
                if not isinstance(current, dict):
                    current = {}
            except Exception:
                current = {}
        current[image_path] = note
        ann_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def capture(
        self,
        device_obj: Any,
        ip: str,
        stage: str,
        reason: str,
        task: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        # 記錄截圖請求開始時間
        request_start = dt.datetime.now()
        request_start_iso = request_start.isoformat()

        if hasattr(device_obj, "screenshot"):
            img = device_obj.screenshot(format="opencv")
        else:
            img = device_obj.capture_screenshot()

        # 記錄截圖完成時間並計算延遲
        request_end = dt.datetime.now()
        request_end_iso = request_end.isoformat()
        latency_ms = (request_end - request_start).total_seconds() * 1000

        if img is None:
            return None

        device_dir = self._device_dir(ip)
        device_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = (
            f"{timestamp}_{_sanitize_filename_part(reason)}_{_sanitize_filename_part(stage)}.jpg"
        )
        image_path = str(device_dir / filename)
        if not self._save_image_unicode_safe(image_path, img):
            raise RuntimeError("cv2.imwrite returned False")

        caller = self._caller_info()
        event = {
            "timestamp": request_end_iso,
            "device_id": ip,
            "task": task,
            "stage": stage,
            "reason": reason,
            "image_path": image_path,
            "trigger": caller,
            "actor": type(device_obj).__name__,
            "source": "SmartScreenshotRecorder.capture",
            "request_timing": {
                "request_start": request_start_iso,
                "request_end": request_end_iso,
                "latency_ms": round(latency_ms, 3),
            },
        }
        if extra:
            event["extra"] = extra
        self._append_jsonl(device_dir / "events.jsonl", event)
        self._tracker.log(
            device_id=ip,
            event_type="screenshot_saved",
            source="smart_screenshot.capture",
            meaning=reason,
            actor=type(device_obj).__name__,
            payload={
                "task": task,
                "stage": stage,
                "reason": reason,
                "image_path": image_path,
                "extra": extra or {},
            },
        )

        annotation_note = {
            "updated_at": request_end_iso,
            "task": task,
            "stage": stage,
            "reason": reason,
            "status": "auto_captured",
            "comment": "",
        }
        self._upsert_annotation(device_dir, image_path, annotation_note)
        return image_path

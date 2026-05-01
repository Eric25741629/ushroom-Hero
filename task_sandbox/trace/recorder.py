"""Recorder - writes structured events to runs/<run-id>/trace.jsonl
and snaps screenshots only on key moments (stage change, assertion fail,
ocr miss, error, explicit request).
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

import cv2

from .schema import EventKind, TraceEvent


class Recorder:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self._fp = (self.run_dir / "trace.jsonl").open("a", encoding="utf-8")
        self._seq = 0
        self._device: Any = None
        self._span_stack: list[str] = []

    def bind_device(self, device: Any) -> None:
        self._device = device

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def event(
        self,
        kind: EventKind,
        *,
        ok: bool = True,
        stage_before: str | None = None,
        stage_after: str | None = None,
        elapsed_ms: int = 0,
        msg: str = "",
        screenshot_reason: str | None = None,
        **args: Any,
    ) -> TraceEvent:
        screenshot_path: str | None = None
        if screenshot_reason:
            screenshot_path = self._snap(reason=screenshot_reason)
        ev: TraceEvent = {
            "ts": time.time(),
            "seq": self._seq,
            "kind": kind,
            "args": args,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "stage_before": stage_before,
            "stage_after": stage_after,
            "screenshot_path": screenshot_path,
            "parent_span": self._span_stack[-1] if self._span_stack else None,
            "msg": msg,
        }
        self._fp.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._fp.flush()
        self._seq += 1
        return ev

    def assertion(self, name: str, *, ok: bool, detail: str = "") -> None:
        self.event(
            "assertion",
            ok=ok,
            name=name,
            detail=detail,
            screenshot_reason=None if ok else f"assertion_fail:{name}",
        )

    def stage_check(self, *, before: str, after: str) -> None:
        changed = before != after
        self.event(
            "stage_check",
            stage_before=before,
            stage_after=after,
            screenshot_reason="stage_change" if changed else None,
        )

    def ocr_miss(self, target: str, detail: str = "") -> None:
        self.event(
            "ocr",
            ok=False,
            target=target,
            detail=detail,
            screenshot_reason=f"ocr_miss:{target}",
        )

    def error(self, exc: BaseException) -> None:
        self.event(
            "error",
            ok=False,
            type=type(exc).__name__,
            msg=str(exc),
            screenshot_reason="error",
        )

    @contextlib.contextmanager
    def span(self, name: str) -> Iterator[None]:
        self.event("span_start", name=name)
        self._span_stack.append(name)
        try:
            yield
        finally:
            self._span_stack.pop()
            self.event("span_end", name=name)

    def screenshot(self, reason: str) -> str | None:
        return self._snap(reason=reason)

    def _snap(self, reason: str) -> str | None:
        if self._device is None:
            return None
        try:
            img = self._device.screenshot(format="opencv")
        except Exception:
            return None
        if img is None:
            return None
        safe_reason = reason.replace("/", "_").replace(":", "_")[:60]
        filename = f"{self._seq:03d}_{safe_reason}.png"
        path = self.run_dir / "screenshots" / filename
        cv2.imwrite(str(path), img)
        rel = f"screenshots/{filename}"
        return rel

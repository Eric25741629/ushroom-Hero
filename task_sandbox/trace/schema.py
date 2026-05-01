"""Trace event format. Each line in trace.jsonl is one TraceEvent JSON."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

EventKind = Literal[
    "click", "swipe", "screenshot", "ocr", "wait_for",
    "stage_check", "assertion", "nav_step", "error",
    "span_start", "span_end", "video_start", "video_end",
    "task_start", "task_end",
]


class TraceEvent(TypedDict, total=False):
    ts: float
    seq: int
    kind: EventKind
    args: dict[str, Any]
    stage_before: str | None
    stage_after: str | None
    ok: bool
    elapsed_ms: int
    screenshot_path: str | None
    parent_span: str | None
    msg: str

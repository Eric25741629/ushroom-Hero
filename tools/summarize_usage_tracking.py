#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Iterable


TRACKED_EVENT_TYPES = {"ocr_request", "classifier_model_load", "classifier_inference"}


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def load_events(log_root: Path) -> Iterable[dict[str, Any]]:
    if not log_root.exists():
        return
    for path in sorted(log_root.rglob("events_????????.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict) and event.get("event_type") in TRACKED_EVENT_TYPES:
                yield event


def summarize_events(
    events: Iterable[dict[str, Any]],
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, int, str], dict[str, Any]] = {}
    for event in events:
        event_time = _parse_time(event.get("timestamp"))
        if event_time is None or event_time < since:
            continue
        payload = event.get("payload") or {}
        caller = event.get("caller") or {}
        key = (
            str(event.get("event_type", "")),
            str(payload.get("component", "")),
            str(caller.get("file", "")),
            int(caller.get("line", 0) or 0),
            str(caller.get("function", "")),
        )
        row = groups.setdefault(
            key,
            {
                "event_type": key[0],
                "component": key[1],
                "caller_file": key[2],
                "caller_line": key[3],
                "caller_function": key[4],
                "calls": 0,
                "success": 0,
                "errors": 0,
                "total_elapsed_ms": 0.0,
                "devices": set(),
                "tasks": set(),
            },
        )
        row["calls"] += 1
        status = str(payload.get("status", ""))
        if status == "success":
            row["success"] += 1
        else:
            row["errors"] += 1
        try:
            row["total_elapsed_ms"] += float(payload.get("elapsed_ms", 0) or 0)
        except (TypeError, ValueError):
            pass
        row["devices"].add(str(event.get("device_id", "unknown")))
        task = str((event.get("device_context") or {}).get("task", "")).strip()
        if task:
            row["tasks"].add(task)

    rows = []
    for row in groups.values():
        calls = int(row["calls"])
        rows.append(
            {
                **{k: v for k, v in row.items() if k not in {"total_elapsed_ms", "devices", "tasks"}},
                "avg_ms": round(row["total_elapsed_ms"] / calls, 1) if calls else 0.0,
                "devices": ",".join(sorted(row["devices"])),
                "tasks": ",".join(sorted(row["tasks"])),
            }
        )
    return sorted(rows, key=lambda item: (-item["calls"], item["event_type"], item["caller_file"]))


def _short_path(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except (OSError, ValueError):
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="彙整 OCR 與 PyTorch 分類器的實際使用位置。")
    parser.add_argument("--days", type=int, default=7, help="統計最近幾天，預設 7 天。")
    parser.add_argument("--log-root", default="logs", help="日誌根目錄。")
    args = parser.parse_args()

    since = datetime.now() - timedelta(days=max(1, args.days))
    rows = summarize_events(load_events(Path(args.log_root)), since=since)
    if not rows:
        print("指定期間內沒有 OCR／分類器使用事件。")
        return 0

    print("calls ok err avg_ms event/component caller devices tasks")
    for row in rows:
        caller = f"{_short_path(row['caller_file'])}:{row['caller_line']}::{row['caller_function']}"
        print(
            f"{row['calls']:>5} {row['success']:>2} {row['errors']:>3} "
            f"{row['avg_ms']:>7.1f} {row['event_type']}/{row['component']} "
            f"{caller} devices={row['devices']} tasks={row['tasks'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

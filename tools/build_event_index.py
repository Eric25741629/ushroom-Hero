#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build unified event index from action/screenshot logs.")
    p.add_argument("--days", type=int, default=7, help="Analyze recent N days.")
    p.add_argument("--devices", default="", help="Comma-separated device ids (optional).")
    # New layout: per-device subdirs under `logs/`, walked recursively.
    p.add_argument("--action-dir", default="logs", help="Root containing action_trace events (recursed).")
    p.add_argument("--shot-dir", default="logs", help="Root containing error_screenshots events (recursed).")
    p.add_argument("--output-dir", default="reports/event_index", help="Output directory.")
    return p.parse_args()


def _safe_iso(ts: str) -> datetime | None:
    text = str(ts or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except Exception:
        pass
    return rows


def _iter_events(root: Path, pattern: str = "events.jsonl") -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for fp in sorted(root.rglob(pattern)):
        events.extend(_load_jsonl(fp))
    return events


def _allow_device(device: str, allow_set: set[str]) -> bool:
    if not allow_set:
        return True
    return str(device) in allow_set


def build_index(
    action_events: list[dict[str, Any]],
    screenshot_events: list[dict[str, Any]],
    since: datetime,
    allow_devices: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for e in action_events:
        ts = _safe_iso(e.get("timestamp", ""))
        if ts is None or ts < since:
            continue
        device = str(e.get("device_id", "unknown"))
        if not _allow_device(device, allow_devices):
            continue
        caller = e.get("caller") or {}
        ctx = e.get("device_context") or {}
        payload = e.get("payload") or {}
        screenshot_path = ""
        if str(e.get("event_type", "")) == "screenshot_saved":
            screenshot_path = str(payload.get("image_path", "") or "")
        rows.append(
            {
                "event_time": ts.isoformat(),
                "device_id": device,
                "event_type": str(e.get("event_type", "")),
                "meaning": str(e.get("meaning", "")),
                "caller_file": str(caller.get("file", "")),
                "caller_line": int(caller.get("line", 0) or 0),
                "caller_function": str(caller.get("function", "")),
                "task": str(ctx.get("task", "")),
                "step": str(ctx.get("step", "")),
                "status": str(ctx.get("status", "")),
                "actor": str(e.get("actor", "")),
                "source": str(e.get("source", "")),
                "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "screenshot_path": screenshot_path,
                "trigger_file": "",
                "trigger_line": 0,
                "trigger_function": "",
            }
        )

    for e in screenshot_events:
        ts = _safe_iso(e.get("timestamp", ""))
        if ts is None or ts < since:
            continue
        device = str(e.get("device_id", "unknown"))
        if not _allow_device(device, allow_devices):
            continue
        trig = e.get("trigger") or {}
        rows.append(
            {
                "event_time": ts.isoformat(),
                "device_id": device,
                "event_type": "smart_screenshot_event",
                "meaning": str(e.get("reason", "")),
                "caller_file": str(trig.get("file", "")),
                "caller_line": int(trig.get("line", 0) or 0),
                "caller_function": str(trig.get("function", "")),
                "task": str(e.get("task", "")),
                "step": "",
                "status": "",
                "actor": str(e.get("actor", "")),
                "source": str(e.get("source", "SmartScreenshotRecorder.capture")),
                "payload_json": json.dumps(e.get("extra", {}), ensure_ascii=False, separators=(",", ":")),
                "screenshot_path": str(e.get("image_path", "")),
                "trigger_file": str(trig.get("file", "")),
                "trigger_line": int(trig.get("line", 0) or 0),
                "trigger_function": str(trig.get("function", "")),
            }
        )

    rows.sort(key=lambda x: (x["event_time"], x["device_id"], x["event_type"]))
    return rows


def write_outputs(rows: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = output_dir / f"event_index_{stamp}.jsonl"
    csv_path = output_dir / f"event_index_{stamp}.csv"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    fieldnames = [
        "event_time",
        "device_id",
        "event_type",
        "meaning",
        "caller_file",
        "caller_line",
        "caller_function",
        "task",
        "step",
        "status",
        "actor",
        "source",
        "payload_json",
        "screenshot_path",
        "trigger_file",
        "trigger_line",
        "trigger_function",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return jsonl_path, csv_path


def main() -> int:
    args = parse_args()
    since = datetime.now() - timedelta(days=max(1, int(args.days)))
    allow_devices = {x.strip() for x in str(args.devices or "").split(",") if x.strip()}

    # action_tracker writes events_YYYYMMDD.jsonl; smart_screenshot writes events.jsonl
    action_events = _iter_events(Path(args.action_dir), pattern="events_????????.jsonl")
    screenshot_events = _iter_events(Path(args.shot_dir), pattern="events.jsonl")
    rows = build_index(action_events, screenshot_events, since, allow_devices)
    jsonl_path, csv_path = write_outputs(rows, Path(args.output_dir))

    print(f"rows: {len(rows)}")
    print(f"jsonl: {jsonl_path}")
    print(f"csv: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


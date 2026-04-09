#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Batch analyzer for SmartScreenshot events.")
    p.add_argument("--days", type=int, default=7, help="Analyze recent N days.")
    p.add_argument("--input-dir", default="logs/error_screenshots", help="SmartScreenshot root dir.")
    p.add_argument("--output-dir", default="reports/smart_screenshot", help="Report output dir.")
    p.add_argument("--use-llm", action="store_true", help="Enable LLM diagnosis.")
    p.add_argument("--llm-model", default="gpt-4.1-mini", help="LLM model for diagnosis.")
    return p.parse_args()


def load_events(root: Path, since: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for fp in sorted(root.rglob("events.jsonl")):
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue
                    ts_raw = str(evt.get("timestamp", "")).strip()
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except Exception:
                        continue
                    if ts >= since:
                        events.append(evt)
        except Exception:
            continue
    return events


def build_summary(events: list[dict[str, Any]], since: datetime) -> dict[str, Any]:
    by_device: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "reasons": Counter(), "stages": Counter(), "tasks": Counter()})
    all_reasons = Counter()
    all_stages = Counter()
    all_tasks = Counter()

    for e in events:
        device = str(e.get("device_id", "unknown"))
        reason = str(e.get("reason", ""))
        stage = str(e.get("stage", ""))
        task = str(e.get("task", ""))
        by_device[device]["count"] += 1
        by_device[device]["reasons"][reason] += 1
        by_device[device]["stages"][stage] += 1
        by_device[device]["tasks"][task] += 1
        all_reasons[reason] += 1
        all_stages[stage] += 1
        all_tasks[task] += 1

    device_payload: dict[str, Any] = {}
    for dev, data in by_device.items():
        device_payload[dev] = {
            "count": data["count"],
            "top_reasons": data["reasons"].most_common(10),
            "top_stages": data["stages"].most_common(10),
            "top_tasks": data["tasks"].most_common(10),
        }

    return {
        "window": {"since": since.isoformat(), "until": datetime.now().isoformat()},
        "total_events": len(events),
        "top_reasons": all_reasons.most_common(20),
        "top_stages": all_stages.most_common(20),
        "top_tasks": all_tasks.most_common(20),
        "devices": device_payload,
    }


def llm_diagnosis(summary: dict[str, Any], model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "LLM skipped: OPENAI_API_KEY not set."
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return "LLM skipped: openai package not installed."

    prompt = (
        "你是遊戲自動化維運分析師。請用繁體中文分析這份 SmartScreenshot 統計，"
        "列出最常見異常原因、可能根因、與可執行修正建議。"
        "請優先指出高頻且可快速修復的問題。\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
    )
    try:
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(model=model, input=prompt, temperature=0.2, max_output_tokens=1000)
        text = getattr(resp, "output_text", None)
        return text or "LLM returned empty response."
    except Exception as exc:
        return f"LLM failed: {exc}"


def to_markdown(summary: dict[str, Any], diagnosis: str | None) -> str:
    lines = [
        "# SmartScreenshot Batch Report",
        "",
        f"- since: `{summary['window']['since']}`",
        f"- until: `{summary['window']['until']}`",
        f"- total_events: `{summary['total_events']}`",
        "",
        "## Top Reasons",
        "",
    ]
    for reason, count in summary["top_reasons"]:
        lines.append(f"- {reason or '(empty)'}: `{count}`")
    lines.extend(["", "## Top Stages", ""])
    for stage, count in summary["top_stages"]:
        lines.append(f"- {stage or '(empty)'}: `{count}`")
    lines.extend(["", "## Per Device", ""])
    for dev, data in summary["devices"].items():
        lines.extend([f"### {dev}", "", f"- count: `{data['count']}`", ""])
        if data["top_reasons"]:
            lines.append("- top_reasons:")
            for reason, count in data["top_reasons"][:5]:
                lines.append(f"  - {reason or '(empty)'}: `{count}`")
            lines.append("")
    if diagnosis is not None:
        lines.extend(["## LLM Diagnosis", "", diagnosis, ""])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    since = datetime.now() - timedelta(days=max(1, int(args.days)))
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    events = load_events(in_dir, since)
    summary = build_summary(events, since)
    diagnosis = llm_diagnosis(summary, args.llm_model) if args.use_llm else None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"smart_screenshot_report_{stamp}.json"
    md_path = out_dir / f"smart_screenshot_report_{stamp}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(summary, diagnosis), encoding="utf-8")
    print(f"Generated: {json_path}")
    print(f"Generated: {md_path}")
    if diagnosis is None:
        print("LLM diagnosis skipped (use --use-llm to enable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


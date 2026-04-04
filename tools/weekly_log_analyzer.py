#!/usr/bin/env python3
"""Weekly log analyzer for bot and mining metrics.

Outputs:
1) JSON metrics snapshot
2) Markdown summary report
3) Optional LLM diagnosis section
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any


LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (?P<level>[A-Z]+) - \[[^\]]+\] (?P<msg>.*)$"
)

# Keyword-based heuristics to estimate issue types from text logs.
MISJUDGE_KEYWORDS = (
    "誤判",
    "misclass",
    "分類器可能有問題",
    "no air cell found",
    "fatal",
)
OCR_ISSUE_KEYWORDS = (
    "OCR",
    "ocr",
    "解析失敗",
    "返回預設",
    "未返回有效",
)
SCREENSHOT_KEYWORDS = (
    "[MiningService] Current Board:",
    "screenshot",
)


@dataclass
class Window:
    start: datetime
    end: datetime

    @classmethod
    def from_args(cls, week_start: str | None, days: int) -> "Window":
        if week_start:
            start = datetime.strptime(week_start, "%Y-%m-%d")
        else:
            today = datetime.now().date()
            # Monday of current week
            start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        end = start + timedelta(days=days)
        return cls(start=start, end=end)

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekly bot/mine log analyzer with optional LLM diagnosis.")
    p.add_argument("--week-start", help="Week start date (YYYY-MM-DD). Default: this week's Monday.")
    p.add_argument("--days", type=int, default=7, help="Window days. Default 7.")
    p.add_argument("--logs-dir", default="logs", help="Text log directory.")
    p.add_argument("--rl-dir", default="miner/rl_logs", help="RL JSONL directory.")
    p.add_argument("--output-dir", default="reports/weekly", help="Output report directory.")
    p.add_argument("--use-llm", action="store_true", help="Enable LLM diagnosis (requires OPENAI_API_KEY).")
    p.add_argument("--llm-model", default="gpt-4.1-mini", help="LLM model name for diagnosis.")
    return p.parse_args()


def parse_text_logs(logs_dir: Path, window: Window) -> dict[str, Any]:
    metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "lines": 0,
            "info": 0,
            "warning": 0,
            "error": 0,
            "ocr_issue_count": 0,
            "misjudge_count": 0,
            "screenshot_count": 0,
            "item_status_count": 0,
            "timestamps": [],
            "hour_histogram": Counter(),
            "top_messages": Counter(),
        }
    )

    if not logs_dir.exists():
        return {"devices": {}, "files_scanned": 0}

    files = sorted(p for p in logs_dir.glob("*.log") if p.is_file())
    files_scanned = 0
    for fp in files:
        files_scanned += 1
        device = fp.stem.replace("miner_", "")
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    raw = raw.rstrip("\n")
                    m = LINE_RE.match(raw)
                    if not m:
                        continue
                    ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
                    if not window.contains(ts):
                        continue
                    level = m.group("level")
                    msg = m.group("msg")

                    d = metrics[device]
                    d["lines"] += 1
                    d[level.lower()] = d.get(level.lower(), 0) + 1
                    d["timestamps"].append(ts.isoformat())
                    d["hour_histogram"][ts.strftime("%Y-%m-%d %H:00")] += 1

                    if any(k.lower() in msg.lower() for k in OCR_ISSUE_KEYWORDS):
                        d["ocr_issue_count"] += 1
                    if any(k.lower() in msg.lower() for k in MISJUDGE_KEYWORDS):
                        d["misjudge_count"] += 1
                    if any(k.lower() in msg.lower() for k in SCREENSHOT_KEYWORDS):
                        d["screenshot_count"] += 1
                    if "[ITEM STATUS]" in msg:
                        d["item_status_count"] += 1

                    normalized = msg.strip()
                    if normalized:
                        d["top_messages"][normalized] += 1
        except Exception:
            continue

    for d in metrics.values():
        ts_list = [datetime.fromisoformat(x) for x in d["timestamps"]]
        d["active_hours"] = len(d["hour_histogram"])
        d["time_span_seconds"] = int((max(ts_list) - min(ts_list)).total_seconds()) if len(ts_list) >= 2 else 0
        d["top_messages"] = [{"msg": k, "count": v} for k, v in d["top_messages"].most_common(10)]
        d.pop("timestamps", None)
        d["hour_histogram"] = dict(d["hour_histogram"])

    return {"devices": dict(metrics), "files_scanned": files_scanned}


def parse_rl_logs(rl_dir: Path, window: Window) -> dict[str, Any]:
    devices: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "events": 0,
            "verify_fail_count": 0,
            "terminated_floor7_count": 0,
            "step_cost_values": [],
            "actions": Counter(),
        }
    )

    if not rl_dir.exists():
        return {"devices": {}, "files_scanned": 0}

    files = sorted(p for p in rl_dir.rglob("events*.jsonl") if p.is_file())
    files_scanned = 0
    for fp in files:
        files_scanned += 1
        device = fp.parent.name
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    ts_val = evt.get("timestamp")
                    if ts_val is None:
                        continue
                    try:
                        ts = datetime.fromtimestamp(float(ts_val))
                    except Exception:
                        continue
                    if not window.contains(ts):
                        continue

                    d = devices[device]
                    d["events"] += 1
                    d["actions"][str(evt.get("plan_action", "unknown"))] += 1

                    cost = evt.get("step_cost_expected")
                    if isinstance(cost, (int, float)):
                        d["step_cost_values"].append(float(cost))

                    if str(evt.get("terminated", "")).lower() == "floor7":
                        d["terminated_floor7_count"] += 1

                    cell_events = evt.get("cell_events") or []
                    for ce in cell_events:
                        if ce.get("verify_success") is False:
                            d["verify_fail_count"] += 1
        except Exception:
            continue

    for d in devices.values():
        d["avg_step_cost_expected"] = round(mean(d["step_cost_values"]), 4) if d["step_cost_values"] else 0.0
        d["actions"] = dict(d["actions"])
        d.pop("step_cost_values", None)

    return {"devices": dict(devices), "files_scanned": files_scanned}


def build_summary(window: Window, text_metrics: dict[str, Any], rl_metrics: dict[str, Any]) -> dict[str, Any]:
    all_devices = set(text_metrics.get("devices", {}).keys()) | set(rl_metrics.get("devices", {}).keys())
    by_device: dict[str, Any] = {}

    totals = Counter()
    for dev in sorted(all_devices):
        td = text_metrics.get("devices", {}).get(dev, {})
        rd = rl_metrics.get("devices", {}).get(dev, {})
        merged = {
            "text_lines": td.get("lines", 0),
            "warning": td.get("warning", 0),
            "error": td.get("error", 0),
            "ocr_issue_count": td.get("ocr_issue_count", 0),
            "misjudge_count": td.get("misjudge_count", 0),
            "screenshot_count": td.get("screenshot_count", 0),
            "active_hours": td.get("active_hours", 0),
            "rl_events": rd.get("events", 0),
            "verify_fail_count": rd.get("verify_fail_count", 0),
            "terminated_floor7_count": rd.get("terminated_floor7_count", 0),
            "avg_step_cost_expected": rd.get("avg_step_cost_expected", 0.0),
            "actions": rd.get("actions", {}),
            "top_messages": td.get("top_messages", []),
        }
        by_device[dev] = merged
        totals.update(
            {
                "text_lines": merged["text_lines"],
                "warning": merged["warning"],
                "error": merged["error"],
                "ocr_issue_count": merged["ocr_issue_count"],
                "misjudge_count": merged["misjudge_count"],
                "screenshot_count": merged["screenshot_count"],
                "rl_events": merged["rl_events"],
                "verify_fail_count": merged["verify_fail_count"],
                "terminated_floor7_count": merged["terminated_floor7_count"],
            }
        )

    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat(), "days": (window.end - window.start).days},
        "scanned": {"text_log_files": text_metrics.get("files_scanned", 0), "rl_files": rl_metrics.get("files_scanned", 0)},
        "totals": dict(totals),
        "devices": by_device,
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
        "你是遊戲自動化維運分析師。請用繁體中文分析這份每週指標，"
        "找出可能問題、根因假設、風險等級，並給出下週可執行改善清單。"
        "重點關注：時間分布、截圖次數、誤判次數、OCR問題、verify_fail。\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
    )

    try:
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=model,
            input=prompt,
            temperature=0.2,
            max_output_tokens=1200,
        )
        text = getattr(resp, "output_text", None)
        if text:
            return text
        return "LLM returned empty response."
    except Exception as exc:
        return f"LLM failed: {exc}"


def render_markdown(summary: dict[str, Any], diagnosis: str | None) -> str:
    window = summary["window"]
    totals = summary["totals"]
    lines = [
        "# Weekly Bot Log Report",
        "",
        f"- Window: `{window['start']}` ~ `{window['end']}` ({window['days']} days)",
        f"- Text log files scanned: `{summary['scanned']['text_log_files']}`",
        f"- RL files scanned: `{summary['scanned']['rl_files']}`",
        "",
        "## Totals",
        "",
        f"- text_lines: `{totals.get('text_lines', 0)}`",
        f"- warnings: `{totals.get('warning', 0)}`",
        f"- errors: `{totals.get('error', 0)}`",
        f"- screenshot_count: `{totals.get('screenshot_count', 0)}`",
        f"- misjudge_count: `{totals.get('misjudge_count', 0)}`",
        f"- ocr_issue_count: `{totals.get('ocr_issue_count', 0)}`",
        f"- rl_events: `{totals.get('rl_events', 0)}`",
        f"- verify_fail_count: `{totals.get('verify_fail_count', 0)}`",
        "",
        "## Per Device",
        "",
    ]

    for dev, d in summary.get("devices", {}).items():
        lines.extend(
            [
                f"### {dev}",
                "",
                f"- text_lines: `{d.get('text_lines', 0)}`",
                f"- warning/error: `{d.get('warning', 0)}` / `{d.get('error', 0)}`",
                f"- active_hours: `{d.get('active_hours', 0)}`",
                f"- screenshot_count: `{d.get('screenshot_count', 0)}`",
                f"- misjudge_count: `{d.get('misjudge_count', 0)}`",
                f"- ocr_issue_count: `{d.get('ocr_issue_count', 0)}`",
                f"- rl_events: `{d.get('rl_events', 0)}`",
                f"- verify_fail_count: `{d.get('verify_fail_count', 0)}`",
                f"- terminated_floor7_count: `{d.get('terminated_floor7_count', 0)}`",
                f"- avg_step_cost_expected: `{d.get('avg_step_cost_expected', 0.0)}`",
                "",
            ]
        )

    if diagnosis is not None:
        lines.extend(["## LLM Diagnosis", "", diagnosis, ""])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    window = Window.from_args(args.week_start, args.days)

    logs_dir = Path(args.logs_dir)
    rl_dir = Path(args.rl_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text_metrics = parse_text_logs(logs_dir, window)
    rl_metrics = parse_rl_logs(rl_dir, window)
    summary = build_summary(window, text_metrics, rl_metrics)
    diagnosis = llm_diagnosis(summary, args.llm_model) if args.use_llm else None

    stamp = window.start.strftime("%Y%m%d")
    json_path = out_dir / f"weekly_report_{stamp}.json"
    md_path = out_dir / f"weekly_report_{stamp}.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(summary, diagnosis), encoding="utf-8")

    print(f"Generated: {json_path}")
    print(f"Generated: {md_path}")
    if diagnosis is None:
        print("LLM diagnosis skipped (use --use-llm to enable).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Replay / inspect the per-account mining maps recorded by
``utils.mining_map_recorder``.

Usage (run as a script — ``tools/`` is not an importable package here):
    python tools/replay_mining_map.py --device <id>
        List recorded sessions for a device.

    python tools/replay_mining_map.py --device <id> --session <file>
        ASCII frame-by-frame replay of one session (board + steps + inventory).
        --fps controls speed; --no-anim dumps every frame at once.

    python tools/replay_mining_map.py --device <id> --map
        Print the cumulative global map, top (shallow) to bottom (deep).

Char legend (see mining_map_recorder.LABEL_TO_CHAR):
    . reachable-air   , unreachable-air   d dirt   D unreachable-dirt
    r rock   R unreachable-rock   P pit   p unreachable-pit   x dug-pit   ? unknown
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# tools/ is not a package here (a root-level tools.py shadows it), so a direct
# `python tools/replay_mining_map.py` puts tools/ — not the repo root — on
# sys.path. Add the repo root so the utils package resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.log_paths import LogPaths  # noqa: E402


def _resolve_map_dir(device: str, logs_root: Optional[str]) -> Path:
    lp = LogPaths.with_root(logs_root) if logs_root else LogPaths
    return lp.mining_map_dir(device)


def list_sessions(map_dir: Path) -> List[Path]:
    return sorted(map_dir.glob("session_*.jsonl"))


def load_session(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def _session_summary(path: Path) -> str:
    events = load_session(path)
    start = next((e for e in events if e.get("ev") == "start"), {})
    rounds = sum(1 for e in events if e.get("ev") == "round")
    end = next((e for e in reversed(events) if e.get("ev") == "end"), {})
    totals = end.get("totals", {}) if end else {}
    return (
        f"{path.name}  backend={start.get('backend', '?')} "
        f"planner={start.get('planner', '?')} rounds={rounds} "
        f"totals={totals or '-'}"
    )


def _fmt_steps(steps: Optional[List[Dict[str, Any]]]) -> str:
    if not steps:
        return "(no steps)"
    parts = []
    for s in steps[:8]:
        kind = s.get("type", "?")
        item = s.get("item") or s.get("action") or "pickaxe"
        pos = s.get("pos") or s.get("target") or s.get("block_id")
        parts.append(f"{kind}:{item}@{pos}")
    if len(steps) > 8:
        parts.append(f"... (+{len(steps) - 8})")
    return " ".join(parts)


def render_round(event: Dict[str, Any], index: int) -> str:
    lines: List[str] = []
    depth = event.get("depth", "?")
    flag = " (uncertain)" if event.get("uncertain") else ""
    lines.append(f"== round #{index}  depth={depth}{flag} ==")
    exec_info = event.get("exec")
    if exec_info is not None:
        lines.append(f"exec: {exec_info}")
    lines.append(f"steps: {_fmt_steps(event.get('steps'))}")
    inv = event.get("inv")
    if inv is not None:
        lines.append(f"inv: {inv}")
    board = event.get("board") or []
    for row in board:
        lines.append(f"  {row}")
    below = event.get("below")
    if below:
        lines.append("  --- below (WS known terrain) ---")
        for row in below:
            lines.append(f"  {row}")
    return "\n".join(lines)


def replay_session(
    path: Path,
    *,
    fps: float = 2.0,
    animate: bool = True,
    out=None,
) -> None:
    out = out or sys.stdout
    events = load_session(path)
    rounds = [e for e in events if e.get("ev") == "round"]
    start = next((e for e in events if e.get("ev") == "start"), {})
    print(f"session={path.name} backend={start.get('backend', '?')} "
          f"planner={start.get('planner', '?')} rounds={len(rounds)}", file=out)
    delay = 1.0 / fps if (animate and fps > 0) else 0.0
    for idx, ev in enumerate(rounds, 1):
        if animate and out is sys.stdout and out.isatty():
            # ANSI clear+home (no shell — avoids os.system injection sink)
            out.write("\033[2J\033[H")
        print(render_round(ev, idx), file=out)
        print("", file=out)
        if delay:
            time.sleep(delay)
    end = next((e for e in reversed(events) if e.get("ev") == "end"), None)
    if end:
        print(f"totals: {end.get('totals', {})}", file=out)


def render_global_map(map_dir: Path) -> str:
    path = map_dir / "global_map.json"
    if not path.exists():
        return "(no global_map.json)"
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    rows: Dict[str, str] = data.get("rows") or {}
    if not rows:
        return "(empty map)"
    lines = [f"global map: max_depth={data.get('max_depth', '?')} rows={len(rows)}"]
    for key in sorted(rows, key=lambda k: int(k)):
        lines.append(f"{int(key):>6}  {rows[key]}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", required=True)
    parser.add_argument("--session", help="session_*.jsonl filename (or full path)")
    parser.add_argument("--map", action="store_true", help="print cumulative global map")
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--no-anim", action="store_true", help="dump all frames at once")
    parser.add_argument("--logs-root", help="override logs root (testing)")
    args = parser.parse_args(argv)

    map_dir = _resolve_map_dir(args.device, args.logs_root)

    if args.map:
        print(render_global_map(map_dir))
        return 0

    if args.session:
        candidate = Path(args.session)
        session_path = candidate if candidate.is_file() else map_dir / args.session
        if not session_path.is_file():
            print(f"session not found: {session_path}", file=sys.stderr)
            return 2
        replay_session(session_path, fps=args.fps, animate=not args.no_anim)
        return 0

    sessions = list_sessions(map_dir)
    if not sessions:
        print(f"no sessions under {map_dir}")
        return 0
    print(f"sessions for {args.device} ({len(sessions)}):")
    for path in sessions:
        print("  " + _session_summary(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

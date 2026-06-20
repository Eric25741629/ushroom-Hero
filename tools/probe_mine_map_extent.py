"""Read-only: how many look-ahead rows of terrain does WS 0x0c01 actually send?

Attaches to an EXISTING browser via CDP (no cold ws_token login -> never kicks
the live session), reads the mine board (0x0c01) once, and prints:
  - baseline / area / viewport_top_depth
  - per-depth-row terrain map built from blocks (config_id), relative to the
    7-row viewport top, so we can see if undug terrain (201/202) is transmitted
    BELOW the visible window (look-ahead) and how deep it reaches.

Decides: do we already get full terrain from WS (use a taller grid, no template
matching), or only sparse pits (need template reconstruction)?

  conda activate mushroom1
  python tools/probe_mine_map_extent.py --port 9223
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from ws_token import mining  # noqa: E402
from ws_token.mining_adapter import viewport_top_depth  # noqa: E402
from utils.web_game_api import WebGameAPI  # noqa: E402

GAME_HOST = "mushroomh5.acenetgame.com"
CMD_MINE_INFO = 0x0C01

_SYM = {100: ".", 201: "D", 202: "R", 401: "X"}


def _attach(port: int):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in browser.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                return pw, browser, p
    pw.stop()
    raise SystemExit(f"no page matching host={GAME_HOST} on CDP {port}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()

    pw, browser, page = _attach(args.port)
    try:
        api = WebGameAPI(page)
        body = api.call_raw(CMD_MINE_INFO, b"", timeout_sec=8.0)
        board = mining.parse_board(bytes(body))
    finally:
        pw.stop()

    top = viewport_top_depth(board.baseline)
    print(f"area={board.area} baseline={board.baseline} top_depth={top} "
          f"blocks={len(board.blocks)} actives={len(board.actives)} "
          f"holes={len(board.holes)}")

    # per (depth,col) terrain from blocks
    cell: dict[tuple[int, int], tuple[int, int]] = {}
    cfg_count: dict[int, int] = defaultdict(int)
    ys = []
    for b in board.blocks:
        cell[(int(b.y), int(b.x))] = (int(b.config_id), int(b.count))
        cfg_count[int(b.config_id)] += 1
        ys.append(int(b.y))
    actives_set = {int(a) for a in board.actives}

    print(f"config_id histogram (blocks): {dict(sorted(cfg_count.items()))}")
    print(f"block depth(y) range: {min(ys) if ys else None}..{max(ys) if ys else None} "
          f"(viewport rows 0..6 = depth {top}..{top + 6})")

    # render every transmitted depth row, cols 1..6, rel row vs viewport top
    if ys:
        for y in range(min(ys), max(ys) + 1):
            rel = y - top
            row_syms = []
            n_undug = 0
            for x in range(1, 7):
                v = cell.get((y, x))
                if v is None:
                    bid = y * 100 + x
                    row_syms.append("a" if bid in actives_set else " ")
                    continue
                cfg, cnt = v
                s = _SYM.get(cfg, "?")
                if cnt <= 0:
                    s = s.lower() if s.isupper() else s  # dug -> lowercase marker
                    s = "·" if cfg in (201, 202) else s
                else:
                    n_undug += 1
                row_syms.append(s)
            tag = "  <-- viewport" if 0 <= rel <= 6 else ("  (below/upcoming)" if rel > 6 else "  (above/passed)")
            print(f"  row{rel:+03d} depth={y}: |{''.join(row_syms)}|  undug={n_undug}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

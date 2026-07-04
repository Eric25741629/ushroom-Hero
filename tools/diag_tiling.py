"""Decisive diagnostic: do the WS-revealed terrain cells fit the static templates
under SOME (phase, orientation) as clean 7-row bands? Reads the current board,
collects every revealed 201/202 cell, and for each phase/orientation reports how
many templates are consistent with each band's reveals.

  python tools/diag_tiling.py --port 9223
"""
from __future__ import annotations
import argparse, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from ws_token import mining, mine_terrain as mt
from utils.web_game_api import WebGameAPI
GAME_HOST = "mushroomh5.acenetgame.com"


def _attach(port):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in b.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                return pw, p
    pw.stop(); raise SystemExit("no game page")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args()
    pw, page = _attach(a.port)
    board = mining.parse_board(bytes(WebGameAPI(page).call_raw(0x0C01, b"", timeout_sec=8.0)))
    pw.stop()
    reveals = {}
    for blk in board.blocks:
        cfg = int(blk.config_id)
        if cfg in (mt.DIRT, mt.STONE):
            reveals[(int(blk.y), int(blk.x) - 1)] = cfg
    print(f"baseline={board.baseline} revealed terrain cells={len(reveals)}")
    depths = sorted({d for d, _ in reveals})
    print(f"depth span: {min(depths)}..{max(depths)}")
    for orientation in ("row", "col"):
        grids = mt.templates(orientation)
        print(f"\n=== orientation={orientation} ===")
        for phase in range(mt.ROWS):
            by_band = {}
            for (d, c), t in reveals.items():
                b, rr = mt._band_of(d, phase)
                by_band.setdefault(b, []).append((rr, c, t))
            line = []
            contradiction = False
            for b in sorted(by_band):
                cells = by_band[b]
                cons = mt._consistent_templates(cells, grids)
                if not cons:
                    contradiction = True
                line.append(f"b{b}:n{len(cells)}/c{len(cons)}")
            flag = "  <-- CONTRADICTION" if contradiction else ""
            print(f"  phase={phase}: " + " ".join(line) + flag)


if __name__ == "__main__":
    raise SystemExit(main())

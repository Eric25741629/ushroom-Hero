"""LIVE proof of pure-WS terrain reconstruction (5556, paused, via CDP).

Drives real pickaxe digs over CDP (no cold ws_token login -> no session kick),
feeding each board into a TerrainModel. For every cell it is about to dig it
records the model's PRE-DIG prediction (terrain_at, derived purely from OTHER
already-revealed cells via the static templates); after the run it compares each
prediction to the real terrain that cell turned out to be. A high match rate on
cells the model predicted = the pure-WS reconstruction works on live data.

  python tools/verify_terrain_live.py --port 9223 --rounds 10
"""
from __future__ import annotations
import argparse, io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from ws_token import mining
from ws_token.mining import build_use_goods_body, GOODS_PICKAXE, CMD_MINE_INFO, CMD_USE_GOODS
from ws_token.mine_terrain import TerrainModel
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


def _diggable(board):
    actives = {int(a) for a in (board.actives or [])}
    blk_by_id = {int(b.block_id) or (int(b.y) * 100 + int(b.x)): b for b in (board.blocks or [])}
    out = []
    for a in actives:
        b = blk_by_id.get(a)
        if b is None or int(getattr(b, "count", 0) or 0) > 0:
            out.append(a)
    return sorted(out)  # shallow-first


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--per-round", type=int, default=6)
    a = ap.parse_args()
    pw, page = _attach(a.port)
    api = WebGameAPI(page)
    m = TerrainModel()
    predictions = {}  # (depth,col) -> predicted terrain (captured pre-dig)
    digs = 0
    try:
        for rnd in range(a.rounds):
            body = api.call_raw(CMD_MINE_INFO, b"", timeout_sec=8.0)
            board = mining.parse_board(bytes(body))
            m.observe_board(board)
            st = m.stats()
            cand = _diggable(board)
            print(f"round {rnd}: baseline={board.baseline} diggable={len(cand)} "
                  f"terrain={st}", flush=True)
            if not cand:
                break
            for block_id in cand[:a.per_round]:
                depth, col1 = divmod(block_id, 100)
                col0 = col1 - 1
                pred = m.terrain_at(depth, col0)
                if pred is not None and (depth, col0) not in predictions:
                    predictions[(depth, col0)] = pred
                try:
                    api.call_raw(CMD_USE_GOODS,
                                 build_use_goods_body(GOODS_PICKAXE, block_id),
                                 timeout_sec=3.0)
                except Exception:
                    pass
                digs += 1
                time.sleep(0.15)
            time.sleep(0.3)
        # final board to reveal the last digs
        board = mining.parse_board(bytes(api.call_raw(CMD_MINE_INFO, b"", timeout_sec=8.0)))
        m.observe_board(board)
    finally:
        pw.stop()

    st = m.stats()
    print(f"\n=== RESULT after {digs} digs ===")
    print("terrain stats:", st)
    # score predictions against the real terrain each cell turned out to be
    hits = miss = unknown = 0
    details = []
    for (d, c), pred in sorted(predictions.items()):
        actual = m.reveals.get((d, c))
        if actual is None:
            unknown += 1
        elif actual == pred:
            hits += 1
        else:
            miss += 1
            details.append((d, c, pred, actual))
    print(f"pre-dig predictions: {len(predictions)} | correct={hits} wrong={miss} "
          f"not-yet-revealed={unknown}")
    for d, c, pred, actual in details[:20]:
        print(f"  WRONG depth={d} col={c} predicted={pred} actual={actual}")
    if hits + miss > 0:
        print(f"ACCURACY on revealed predictions: {hits}/{hits+miss} = "
              f"{100*hits/(hits+miss):.0f}%")


if __name__ == "__main__":
    raise SystemExit(main())

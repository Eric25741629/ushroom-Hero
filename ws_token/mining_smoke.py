"""Live read-only mining smoke: connect, read the board, print grid + plan.

  python -m ws_token.mining_smoke --device 7fe98fc6

This ONLY reads the mine board (0x0c01) and prints the decoded board, the 7x6
planner grid, and (if the planner imports cleanly) a v4 plan. It NEVER digs:
mining live mutates the real board and burns pickaxes, so there is no dig flag
here — live digging is human-supervised.

WARNING: logging in over WS kicks that account's active session (App / web /
bot). The board read itself is harmless.
"""
from __future__ import annotations

import argparse

from ws_token import mining
from ws_token.client import WSGameClient
from ws_token.creds import load_creds
from ws_token.mining_adapter import board_to_grid


def _print_grid(grid):
    width = max((len(c) for row in grid for c in row), default=5)
    for r, row in enumerate(grid):
        print(f"  r{r}: " + " ".join(c.ljust(width) for c in row), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[mining] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    tracker = mining.InventoryTracker()
    client.set_push_handler(tracker.on_push)
    info = client.connect()
    print(f"[mining] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        board = mining.read_board(client)
        print(f"[mining] board: max_num(cap)={board.max_num} area={board.area} "
              f"baseline={board.baseline} blocks={len(board.blocks)} "
              f"holes={len(board.holes)} actives={board.actives}", flush=True)
        for h in board.holes:
            print(f"  hole cfg={h.config_id} {h.last_num}/{h.max_num} hole_num={h.hole_num}",
                  flush=True)

        grid = board_to_grid(board)
        print("[mining] 7x6 planner grid:", flush=True)
        _print_grid(grid)

        inv = tracker.as_props()
        print(f"[mining] inventory (from 0x0402 push): {inv}", flush=True)

        try:
            from ws_token.mining_adapter import plan
        except Exception as exc:  # pragma: no cover - defensive only
            print(f"[mining] planner import failed ({exc}); grid printed above.",
                  flush=True)
            return 0
        result = plan(board, inv)
        print(f"[mining] plan: {result.get('message')}", flush=True)
        for s in result.get("ws_steps", []):
            print(f"  step {s.get('action')} pos=({s.get('row')},{s.get('col')}) "
                  f"-> block_id={s.get('block_id')}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

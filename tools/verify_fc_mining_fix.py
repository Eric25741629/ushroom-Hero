"""Live WS verification of the fc hold_floor mining-deadlock fix.

Imports the FIXED ws_token.mining_adapter + mining_supervised (fresh process →
picks up the working-tree patch, no bot restart needed), connects to the device
over WS, and proves on the REAL board:

  1. hold_floor is now computed from raw blocks (count>0) — collected row-0 pits
     no longer pin it True (the deadlock root cause).
  2. _select_dig_step returns a server-VALID frontier target (not the rejected
     baseline rock).
  3. a few REAL digs confirm (board change) and the pickaxe count drops — i.e.
     mining is no longer stuck at <cap>/<cap>.

Run in a window where the bot's own WS phase for the device is NOT active (same
account → concurrent WS logins kick each other). For fc the bot sleeps ~2h
between wakes; run mid-sleep.

    python tools/verify_fc_mining_fix.py --device <dev> --max-steps 8
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ws_token import mining, mining_adapter, mining_supervised  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.creds import load_creds  # noqa: E402


def _row0_pit_dump(board) -> list:
    top = mining_adapter.viewport_top_depth(int(getattr(board, "baseline", 0) or 0))
    out = []
    for b in getattr(board, "blocks", []) or []:
        if int(getattr(b, "y", 0) or 0) == top:
            out.append((b.block_id, b.config_id, b.count, b.is_reward))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--no-dig", action="store_true",
                    help="只讀盤面 + 印 hold_floor/選格，不送任何真實 dig")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    tracker = mining.InventoryTracker()
    creds = load_creds(args.device)
    client = WSGameClient(creds, push_handler=tracker.on_push)
    info = client.connect()
    print(f"[verify] login code={info.get('code')} role_id={info.get('role_id')}",
          flush=True)
    try:
        time.sleep(2.5)  # drain login pushes
        board = mining.read_board(client, timeout=8.0)
        seen = tracker.has_item(mining.GOODS_PICKAXE)
        inv = dict(tracker.as_props())
        if not seen:
            inv["pickaxe"] = mining_supervised._SEED_UNKNOWN_PICKAXE
        plan = mining_adapter.plan(board, inv)
        uncollected = mining_adapter.has_uncollected_row0_pit(board)
        step = mining_supervised._select_dig_step(
            board, plan.get("ws_steps", []),
            hold_floor=bool(plan.get("hold_floor")), grid=plan.get("grid"))

        print(f"[verify] baseline={board.baseline} actives={len(board.actives)} "
              f"blocks={len(board.blocks)} pickaxe_seen={seen} "
              f"pickaxe={tracker.counts.get(mining.GOODS_PICKAXE)}", flush=True)
        print(f"[verify] row0_pits(block_id,cfg,count,reward)={_row0_pit_dump(board)}",
              flush=True)
        print(f"[verify] has_uncollected_row0_pit={uncollected}  "
              f"hold_floor={plan.get('hold_floor')}", flush=True)
        print(f"[verify] selected_dig_step={step}", flush=True)

        if args.no_dig:
            print("[verify] --no-dig: skipping real digs", flush=True)
            return 0

        before = tracker.counts.get(mining.GOODS_PICKAXE)
        result = mining_supervised.mine_until_pickaxe_empty(
            client, tracker, max_steps=args.max_steps, timeout=8.0,
            device_id=args.device)
        confirmed = sum(1 for it in result.get("executed", []) if it.get("confirmed"))
        print(f"[verify] stopped_reason={result.get('stopped_reason')} "
              f"executed={len(result.get('executed', []))} confirmed_digs={confirmed} "
              f"skipped_sentinel={'skipped' in result}", flush=True)
        print(f"[verify] pickaxe before={before} -> after="
              f"{tracker.counts.get(mining.GOODS_PICKAXE)} "
              f"(initial_inv={result.get('initial_inventory')} "
              f"final_inv={result.get('final_inventory')})", flush=True)
        print("[verify] VERDICT:",
              "FIX WORKS — confirmed dig(s) happened, deadlock broken"
              if confirmed > 0 else
              "no confirmed dig (check board state / actives)", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Live runner for 守護靈 (guardian spirit) draws over pure WS — dry by default.

  python -m ws_token.spirit_smoke --device 7fe98fc6                      # read pools (dry)
  python -m ws_token.spirit_smoke --device 7fe98fc6 --draw               # draw all free
  python -m ws_token.spirit_smoke --device 7fe98fc6 --buy-summon 3:4201:10  # buy 招喚貨幣

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Drawing consumes the day's FREE draws and is irreversible. ``--buy-summon`` SPENDS
premium currency and is also irreversible — the shop_id is live-confirm (a
configMall value); pass SHOP_TYPE:SHOP_ID:NUM explicitly, nothing is hardcoded.
Dry run only reads each pool's draw_id / free_times.
"""
from __future__ import annotations

import argparse

from ws_token import spirit
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_buy(spec: str) -> tuple[int, int, int]:
    """Parse a SHOP_TYPE:SHOP_ID:NUM spec into three ints."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--buy-summon must be SHOP_TYPE:SHOP_ID:NUM")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--buy-summon parts must be ints: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--draw", action="store_true",
                    help="actually draw every pool's free_times (free draws only)")
    ap.add_argument("--buy-summon", metavar="SHOP_TYPE:SHOP_ID:NUM", type=_parse_buy,
                    help="actually buy 招喚貨幣 (SPENDS; shop_id is live-confirm)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[spirit] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[spirit] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        pools = spirit.read_draw_info(client)
        print(f"[spirit] draw_info (0x4D1F): {len(pools)} pool(s)", flush=True)
        for p in pools:
            print(f"  draw_id={p.draw_id} free_times={p.free_times} "
                  f"has_free={p.has_free}", flush=True)

        if args.buy_summon is not None:
            shop_type, shop_id, num = args.buy_summon
            out = spirit.buy_summon_currency(client, shop_type=shop_type,
                                             shop_id=shop_id, num=num)
            print(f"[spirit] buy_summon: ok={out['ok']} error_code={out['error_code']} "
                  f"(shop_type={shop_type} shop_id={shop_id} num={num})", flush=True)

        if args.draw:
            out = spirit.draw_all_free(client)
            print(f"[spirit] draw_all_free: pools_drawn={out['pools_drawn']} "
                  f"rewards={out['rewards']}", flush=True)
            for r in out["results"]:
                print(f"  draw_id={r['draw_id']} free_times={r['free_times']} "
                      f"drew={r['drew']} ok={r['ok']} error_code={r['error_code']} "
                      f"rewards={r['rewards']}", flush=True)
        elif args.buy_summon is None:
            free = sum(1 for p in pools if p.has_free)
            print(f"[spirit] (dry run) {free} pool(s) with free draws; "
                  f"pass --draw to draw.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

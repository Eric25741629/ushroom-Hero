"""Live runner: open lamp + combo->套裝 auto-equip over pure WS (dry-run default).

  python -m ws_token.lamp_smoke --device 7fe98fc6 --map-only         # print derived 套裝 map only
  python -m ws_token.lamp_smoke --device 7fe98fc6 --batches 1        # open, DRY-RUN (no equip/sell)
  python -m ws_token.lamp_smoke --device 7fe98fc6 --batches 1 --sell # actually equip/sell (IRREVERSIBLE)

WARNING: WS login kicks the account's session. Opening consumes lamp items
EVEN in dry-run (dry-run only skips the equip/sell of the drops); --sell also
equips winners + sells the rest (irreversible). Default is dry-run.
"""
from __future__ import annotations

import argparse
import time

from ws_token import lamp
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

# How long to wait after connect so the login-time pushes (incl. the 0x0402
# inventory snapshot, if any) have arrived before we read the holder.
_LOGIN_SETTLE_SEC = 2.0


def _combo(fs) -> str:
    return "".join(sorted(fs)) or "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--batches", type=int, default=1)
    ap.add_argument("--num", type=int, default=20, help="boxes per batch")
    ap.add_argument("--sell", action="store_true",
                    help="actually equip winners + sell rest (irreversible)")
    ap.add_argument("--map-only", action="store_true",
                    help="only derive+print the 套裝 map (no open/equip/sell)")
    ap.add_argument("--lamp-percent", type=float, default=0.0,
                    help="open this %% of the current 神燈 stock (0 = feature off)")
    ap.add_argument("--lamp-min-keep", type=int, default=0,
                    help="keep at least this many 神燈 unopened (0 = feature off)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[lamp] device={args.device} uid={creds.uid} role_id={creds.role_id}", flush=True)

    # Tee login-time 0x0402 inventory pushes through extract_lamp_count so we can
    # answer: does the login snapshot carry item 1001 (神燈 現量)? open_lamp later
    # installs its own push handler, so this one only sees the pre-open pushes.
    holder = {"count": None, "frames": 0}

    def _push(cmd: int, body: bytes) -> None:
        if cmd == lamp.CMD_INVENTORY_PUSH:
            qty = lamp.extract_lamp_count(body)
            if qty is not None:
                holder["count"] = qty
                holder["frames"] += 1

    client = WSGameClient(creds, push_handler=_push)
    print("[lamp] login:", client.connect(), flush=True)
    try:
        time.sleep(_LOGIN_SETTLE_SEC)  # let login pushes settle before reading holder
        print(f"[lamp] login-snapshot 1001 present={holder['count'] is not None} "
              f"count={holder['count']} frames={holder['frames']}", flush=True)
        active = lamp.parse_tab_info(client.call(lamp.CMD_TAB_INFO, b""))
        set_map, lian, worn = lamp.derive_set_map(client.call(lamp.CMD_EQUIP_INFO, b""))
        print(f"[lamp] active_tab={active}  lian_shan_tabs={sorted(lian)}", flush=True)
        for fs, tab in sorted(set_map.items(), key=lambda kv: kv[1]):
            print(f"  套裝 {_combo(fs):4s} -> tab {tab}"
                  f"{'  (連閃 build)' if tab in lian else ''}", flush=True)
        if args.map_only:
            return 0

        res = lamp.open_lamp(client, dry_run=not args.sell,
                             batch_num=args.num, max_batches=args.batches,
                             lamp_percent=args.lamp_percent,
                             lamp_min_keep=args.lamp_min_keep,
                             initial_count=holder["count"],
                             on_progress=lambda o, t: print(f"  progress {o}/{t}", flush=True))
        print(f"[lamp] opened={res['opened']} equipped={len(res['equipped'])} "
              f"sold={len(res['sold'])} left={len(res['left'])} dry_run={res['dry_run']} "
              f"target={res['target']} initial_count={res['initial_count']} "
              f"remaining={res['remaining']}",
              flush=True)
        for tab, uid, reason in res["equipped"]:
            print(f"  EQUIP uid={uid} -> tab {tab}: {reason}", flush=True)
        for uid, reason in res["sold"]:
            print(f"  SELL  uid={uid}: {reason}", flush=True)
        for uid, combo in res["left"]:
            print(f"  LEAVE uid={uid}: {combo}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

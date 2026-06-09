"""Live runner: open lamp + combo->套裝 auto-equip over pure WS (dry-run default).

  python -m ws_token.lamp_smoke --device 7fe98fc6 --map-only         # print derived 套裝 map only
  python -m ws_token.lamp_smoke --device 7fe98fc6 --batches 1        # open, DRY-RUN (no equip/sell)
  python -m ws_token.lamp_smoke --device 7fe98fc6 --batches 1 --sell # actually equip/sell (IRREVERSIBLE)

WARNING: WS login kicks the account's session. Opening consumes lamp items;
--sell equips winners + sells the rest (irreversible). Default is dry-run.
"""
from __future__ import annotations

import argparse

from ws_token import lamp
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


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
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[lamp] device={args.device} uid={creds.uid} role_id={creds.role_id}", flush=True)
    client = WSGameClient(creds)
    print("[lamp] login:", client.connect(), flush=True)
    try:
        active = lamp.parse_tab_info(client.call(lamp.CMD_TAB_INFO, b""))
        set_map, lian, worn = lamp.derive_set_map(client.call(lamp.CMD_EQUIP_INFO, b""))
        print(f"[lamp] active_tab={active}  lian_shan_tabs={sorted(lian)}", flush=True)
        for fs, tab in sorted(set_map.items(), key=lambda kv: kv[1]):
            print(f"  套裝 {_combo(fs):4s} -> tab {tab}"
                  f"{'  (連閃 build)' if tab in lian else ''}", flush=True)
        if args.map_only:
            return 0

        res = lamp.open_lamp(client, dry_run=not args.sell,
                             batch_num=args.num, max_batches=args.batches)
        print(f"[lamp] opened={res['opened']} equipped={len(res['equipped'])} "
              f"sold={len(res['sold'])} left={len(res['left'])} dry_run={res['dry_run']}",
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

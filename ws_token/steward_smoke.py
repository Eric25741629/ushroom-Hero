"""Live runner: 家園管家採購掃蕩 over pure WS (worker_common 0x49).

  python -m ws_token.steward_smoke --device 7fe98fc6                 # read state only (dry)
  python -m ws_token.steward_smoke --device 7fe98fc6 --shopping      # run 購物管家 sweep
  python -m ws_token.steward_smoke --device 7fe98fc6 --sweep 1:5:10  # run 副本管家 sweep (id:level:times[:use_ad])
  python -m ws_token.steward_smoke --device 7fe98fc6 --shopping --renew  # renew expired服務 first (SPENDS 家園幣)

WARNING: logging in over WS kicks that account's active session (App / web / bot).
DRY-RUN by default: it only reads info/settings. Action flags are explicit gates:
  --shopping / --sweep actually send sweeps; --renew SPENDS 家園幣 and only fires
  for a service that is BOTH selected by --shopping/--sweep AND currently expired.
"""
from __future__ import annotations

import argparse

from ws_token import steward
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_sweep_arg(items: list[str]) -> list[tuple[int, ...]]:
    """Parse --sweep id:level:times[:use_ad] tokens into sweep_list tuples."""
    out: list[tuple[int, ...]] = []
    for tok in items:
        parts = [int(p) for p in tok.split(":")]
        if len(parts) < 3:
            raise SystemExit(f"--sweep entry {tok!r} needs id:level:times[:use_ad]")
        out.append(tuple(parts[:4]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--shopping", action="store_true",
                    help="actually run the 購物管家 sweep (shopping 18696)")
    ap.add_argument("--sweep", action="append", default=[], metavar="id:level:times[:use_ad]",
                    help="run 副本管家 sweep for this chapter (repeatable)")
    ap.add_argument("--renew", action="store_true",
                    help="auto-renew an expired selected service first (SPENDS 家園幣)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[steward] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    serv_time = int(info.get("serv_time") or 0)
    print(f"[steward] login code={info['code']} role_id={info['role_id']} "
          f"serv_time={serv_time}", flush=True)
    try:
        hk = steward.read_info(client)
        print(f"[steward] housekeeper info (0x{steward.CMD_INFO:04x}): "
              f"{len(hk.expiry)} service(s)", flush=True)
        for sid, expiry in sorted(hk.expiry.items()):
            active = expiry > serv_time
            label = {steward.SERVICE_SHOPPING: "購物管家",
                     steward.SERVICE_DUNGEON: "副本管家"}.get(sid, f"service{sid}")
            print(f"  id={sid} ({label}) expiry={expiry} "
                  f"{'ACTIVE' if active else 'EXPIRED'}", flush=True)

        setting = steward.read_dungeon_setting(client)
        print(f"[steward] dungeon setting (0x{steward.CMD_DUNGEON_SETTING_INFO:04x}): "
              f"{setting}", flush=True)

        sweep_list = _parse_sweep_arg(args.sweep)
        if not args.shopping and not sweep_list:
            print("[steward] (dry run) pass --shopping and/or --sweep to act.")
            return 0

        summary = steward.run(
            client, serv_time=serv_time,
            do_shopping=args.shopping, do_dungeon=bool(sweep_list),
            sweep_list=sweep_list or None, renew=args.renew)

        if args.shopping:
            sr = summary["shopping"]
            print(f"[steward] shopping active={summary['shopping_active']} "
                  f"result={'<skipped>' if sr is None else len(sr.shops)} shop(s)",
                  flush=True)
            if sr is not None:
                for shop in sr.shops:
                    print(f"  shop_id={shop.shop_id} code={shop.code} items={shop.items}",
                          flush=True)
        if sweep_list:
            sw = summary["sweep"]
            print(f"[steward] dungeon active={summary['dungeon_active']} "
                  f"result={'<skipped>' if sw is None else len(sw.results)} chapter(s)",
                  flush=True)
            if sw is not None:
                for r in sw.results:
                    print(f"  chapter_id={r.chapter_id} code={r.code} rewards={r.rewards}",
                          flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Live runner for 比格先生 / 伴侶 (spouse-favor) over pure WS — dry by default.

  python -m ws_token.couple_smoke --device 7fe98fc6                    # read (dry)
  python -m ws_token.couple_smoke --device 7fe98fc6 --give-flowers 30  # send 30 奶茶 + 30 鮮花
  python -m ws_token.couple_smoke --device 7fe98fc6 --fetch-reward 5   # 默契考驗 favor_lv=5
  python -m ws_token.couple_smoke --device 7fe98fc6 --forge-ring       # 錘鍊 to 真愛之石 zero

Dry run reads marry_status (partner), favor_friend_info (partner list), and
marry_ring_info (戒指 level/exp). Mutations target the FIRST partner.

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Mutations are IRREVERSIBLE: --give-flowers sends NUM 奶茶 then NUM 鮮花 to the
partner (親密度↑), --fetch-reward claims the weekly 默契考驗, --forge-ring consumes
ALL 真愛之石. Inventory counts (奶茶/鮮花/真愛之石 在手幾個) are NOT readable here —
they come from the login-time 0x0402 push — so NUM is EXPLICIT and live-confirm;
--forge-ring loops until the server says 物品不足 (stones used up).
"""
from __future__ import annotations

import argparse

from ws_token import couple
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _print_state(client: WSGameClient) -> int:
    lover_id = couple.read_partner(client)
    print(f"[couple] marry_status lover_id={lover_id} "
          f"({'no partner' if lover_id == 0 else 'has partner'})", flush=True)
    partners = couple.read_favor_info(client)
    print(f"[couple] favor_friend_info: {len(partners)} partner(s)", flush=True)
    # only the first partner (the 伴侶) matters; printing all 50 names can also
    # hit a non-UTF-8 console's encoder, so show just the first.
    if partners:
        p = partners[0]
        name = p.name.encode("utf-8", "replace").decode("utf-8")
        print(f"  role_id={p.role_id} name={name!r} favor_lv={p.favor_lv} "
              f"favor={p.favor} <- 伴侶", flush=True)
    ring = couple.read_ring(client)
    print(f"[couple] marry_ring_info level={ring['level']} use={ring['use']} "
          f"exp={ring['exp']}", flush=True)
    return partners[0].role_id if partners else lover_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--give-flowers", type=int, metavar="NUM",
                    help="send NUM 奶茶 then NUM 鮮花 to the first partner (SPENDS items)")
    ap.add_argument("--fetch-reward", type=int, metavar="FAVOR_LV",
                    help="claim the weekly 默契考驗 at this favor_lv (live-confirm)")
    ap.add_argument("--forge-ring", action="store_true",
                    help="錘鍊 the ring until 真愛之石 runs out (consumes ALL stones)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[couple] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[couple] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        friend_id = _print_state(client)

        if args.give_flowers is not None:
            if not friend_id:
                print("[couple] no partner — skipping --give-flowers", flush=True)
            else:
                num = args.give_flowers
                for goods, label in ((couple.MILK_TEA, "奶茶"), (couple.FLOWER, "鮮花")):
                    out = couple.give_all(client, friend_id=friend_id,
                                          flower_id=goods, num=num)
                    print(f"[couple] give_all {label}({goods}) num={num}: "
                          f"ok={out['ok']} error_code={out['error_code']}", flush=True)

        if args.fetch_reward is not None:
            if not friend_id:
                print("[couple] no partner — skipping --fetch-reward", flush=True)
            else:
                out = couple.fetch_favor_reward(client, friend_id=friend_id,
                                                favor_lv=args.fetch_reward)
                print(f"[couple] fetch_favor_reward favor_lv={args.fetch_reward}: "
                      f"ok={out['ok']} error_code={out['error_code']}", flush=True)

        if args.forge_ring:
            out = couple.forge_ring_until_empty(client)
            print(f"[couple] forge_ring_until_empty: forges={out['forges']} "
                  f"last_new_lev={out['last_new_lev']} "
                  f"stopped_reason={out['stopped_reason']}", flush=True)

        if (args.give_flowers is None and args.fetch_reward is None
                and not args.forge_ring):
            print("[couple] (dry run) pass --give-flowers/--fetch-reward/--forge-ring "
                  "to act.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

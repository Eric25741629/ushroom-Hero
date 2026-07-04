"""Live runner: read / claim idle (掛機) and offline (離線) rewards over pure WS.

  python -m ws_token.idle_reward_smoke --device 7fe98fc6                  # dry: print accrued amounts
  python -m ws_token.idle_reward_smoke --device 7fe98fc6 --claim-online   # claim ONLINE accrual
  python -m ws_token.idle_reward_smoke --device 7fe98fc6 --claim-offline  # claim OFFLINE accrual

Default is dry: it prints the ONLINE accrual (reward_info{1}) and any OFFLINE
reward the server pushed at login, without claiming anything. Pass a --claim-*
flag to actually redeem — claiming a reward is free money you have already
earned, but it is irreversible.

NOTE: there is no "看廣告加倍" over WS, so this only ever takes the base amount.

WARNING: logging in over WS kicks that account's active session (App / web / bot).

# live-confirm: 離線 reward 是否登入即 push reward_info_s2c{type:2}、claim(2) 能否直接領。
"""
from __future__ import annotations

import argparse

from ws_token import idle_reward
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--claim-online", action="store_true",
                    help="actually claim the ONLINE accrual (claim_reward{1})")
    ap.add_argument("--claim-offline", action="store_true",
                    help="actually claim the OFFLINE reward (claim_reward{2})")
    ap.add_argument("--claim-quick", action="store_true",
                    help="claim the 2h quick income (ad_reward 0x1602, "
                         "30min冷卻/一天3次)")
    args = ap.parse_args()

    # Capture the offline reward the server pushes right after login.
    offline_pushes: list[bytes] = []

    def _push(cmd: int, body: bytes) -> None:
        if cmd == idle_reward.CMD_REWARD_INFO:
            info = idle_reward.parse_reward_info(body)
            if info.type == idle_reward.TYPE_OFFLINE:
                offline_pushes.append(body)
                print(f"[idle] OFFLINE push: time={info.time}s "
                      f"res={info.res_list} item={info.item_list}", flush=True)

    creds = load_creds(args.device)
    print(f"[idle] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds, push_handler=_push)  # push handler BEFORE connect
    info = client.connect()
    print(f"[idle] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        online = idle_reward.read_online(client)
        print(f"[idle] ONLINE accrual (reward_info{{1}}): time={online.time}s "
              f"res={online.res_list} item={online.item_list} "
              f"claimable={online.is_claimable}", flush=True)

        if args.claim_online:
            res = idle_reward.claim_online(client)
            if res is None:
                print("[idle] ONLINE: nothing to claim.", flush=True)
            else:
                print(f"[idle] ONLINE claim{{1}} -> "
                      f"success={res.success} cmd=0x{res.response_cmd:04x}", flush=True)

        if args.claim_offline:
            if offline_pushes:
                res = idle_reward.claim_offline_from_push(client, offline_pushes[0])
                if res is None:
                    print("[idle] OFFLINE push was empty; nothing to claim.", flush=True)
                else:
                    print(f"[idle] OFFLINE claim{{2}} (from push) -> "
                          f"success={res.success}", flush=True)
            else:
                print("[idle] no OFFLINE push captured; trying direct claim{2}...",
                      flush=True)
                res = idle_reward.claim_offline(client)
                print(f"[idle] OFFLINE claim{{2}} (direct) -> "
                      f"success={res.success} cmd=0x{res.response_cmd:04x}", flush=True)

        if args.claim_quick:
            res = idle_reward.claim_quick_2h(client)
            print(f"[idle] QUICK-2H ad_reward{{config_id:4}} -> "
                  f"success={res.success} error_code={res.error_code} "
                  f"ad={res.ad}", flush=True)

        if not (args.claim_online or args.claim_offline or args.claim_quick):
            print("[idle] (dry run) pass --claim-online / --claim-offline / "
                  "--claim-quick to redeem.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

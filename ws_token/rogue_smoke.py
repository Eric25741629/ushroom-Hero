"""Live runner: 萬神試煉 本周積分獎勵一鍵領取 over pure WS.

  python -m ws_token.rogue_smoke --device 7fe98fc6            # claim 本周積分獎勵
  python -m ws_token.rogue_smoke --device 7fe98fc6 --info     # read status only (no claim)

The claim is idempotent: if this week is already claimed the server replies with
an empty get_list (nothing granted). ⚠ Logging in over WS KICKS that account's
active session (App / web browser / bot) via 異地登入.
"""
from __future__ import annotations

import argparse

from ws_token import codec, rogue
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

CMD_ROGUE_INFO = 19457     # rogue.rogue_info_c2s/_s2c {id, point, end_time, score,
                           #   get_list#5:uint32[] (already-claimed milestones), ...}


def _print_info(client: WSGameClient) -> None:
    body = client.call(CMD_ROGUE_INFO, b"")
    f = codec.walk_dict(body)
    got = tuple(v for fnum, v in codec.walk(body) if fnum == 5 and isinstance(v, int))
    print(f"[rogue] info: point={f.get(2)} score={f.get(4)} rank={f.get(6)} "
          f"already_claimed={got}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--info", action="store_true",
                    help="只讀 rogue_info（本周積分 / 已領里程碑），不領取")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[rogue] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[rogue] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        _print_info(client)
        if args.info:
            print("[rogue] (info only) pass without --info to claim 本周積分獎勵.",
                  flush=True)
            return 0

        r = rogue.claim_week_reward(client)
        if r.success:
            tag = (f"claimed={r.claimed} rewards={r.rewards}"
                   if r.claimed else "nothing to claim (本周已領完)")
            print(f"[rogue] week_reward SUCCESS {tag}", flush=True)
        else:
            print(f"[rogue] week_reward ERR cmd=0x{r.response_cmd:04x} "
                  f"code={r.error_code}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

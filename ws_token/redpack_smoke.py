"""Live Step-2 runner: list + grab red packets over pure WS.

  python -m ws_token.redpack_smoke --device 7fe98fc6          # list only (dry)
  python -m ws_token.redpack_smoke --device 7fe98fc6 --grab   # actually claim

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Claiming a red packet is a free reward (desired), but it is irreversible.
"""
from __future__ import annotations

import argparse

from ws_token import redpack
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--grab", action="store_true",
                    help="actually send grab (0x2603) for each listed bag")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[redpack] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[redpack] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        bags = redpack.list_red_bags(client)
        print(f"[redpack] brief list (0x2605): {len(bags)} bag(s)", flush=True)
        for b in bags:
            print(f"  bag_id={b.bag_id} cfg_id={b.cfg_id} state={b.state} "
                  f"sender={b.sender_name!r} expiry={b.expiry_time} "
                  f"amt={b.min_amount}-{b.max_amount}", flush=True)
        if not bags:
            print("[redpack] no bags listed.")
            return 0
        if not args.grab:
            print("[redpack] (dry run) pass --grab to actually claim.")
            return 0
        for b in bags:
            r = redpack.grab(client, b)
            tag = f"SUCCESS amount={r.amount}" if r.success else f"ERR code={r.error_code}"
            print(f"[redpack] grab bag_id={b.bag_id} cfg_id={b.cfg_id} -> "
                  f"cmd=0x{r.response_cmd:04x} {tag}", flush=True)
            print(f"          fields={r.fields} raw={r.response_body.hex()}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

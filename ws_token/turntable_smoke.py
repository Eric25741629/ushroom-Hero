"""Live runner: read turntable info, optionally spin free turns over pure WS.

  python -m ws_token.turntable_smoke --device 7fe98fc6          # read num/cd (dry)
  python -m ws_token.turntable_smoke --device 7fe98fc6 --spin   # actually spin free turns

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Spinning is irreversible and consumes free/accumulated turns (a free reward).
Only free turns are spun; ad-funded top-ups are not attempted (not possible over WS).
The prize for a winning slot lives in the client config (configTurntable, id-1);
this runner only reports the slot id.
"""
from __future__ import annotations

import argparse

from ws_token import turntable
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--spin", action="store_true",
                    help="actually spin (0x1604) every free/accumulated turn")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[turntable] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[turntable] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        wheel = turntable.read_info(client)
        print(f"[turntable] info (0x1603): num={wheel.num} cd={wheel.cd}", flush=True)
        if wheel.num <= 0:
            print("[turntable] no free spins available.")
            return 0
        if not args.spin:
            print(f"[turntable] (dry run) {wheel.num} free spin(s); pass --spin to spin.")
            return 0
        out = turntable.spin_all_free(client)
        print(f"[turntable] spun={out['spun']} winning slots={out['results']}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

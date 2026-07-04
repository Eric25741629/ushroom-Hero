"""Live runner: read turntable info, optionally run the daily 轉盤 flow (pure WS).

  python -m ws_token.turntable_smoke --device 7fe98fc6          # read num/cd (dry)
  python -m ws_token.turntable_smoke --device 7fe98fc6 --spin   # claim ad top-ups + spin

WARNING: logging in over WS kicks that account's active session (App / web / bot).
``--spin`` runs turntable.run_daily: it banks today's ad-funded spins (config 13,
2/day, NO_ADS = free instant) into the wheel pool, then spins every available
turn. Spinning is irreversible and consumes free/accumulated turns (a free
reward). The prize for a winning slot lives in the client config
(configTurntable, id-1); this runner only reports the slot id.
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
        if not args.spin:
            print(f"[turntable] (dry run) {wheel.num} free spin(s); pass --spin to "
                  "claim ad top-ups + spin.")
            return 0
        out = turntable.run_daily(client)
        print(f"[turntable] ad_topup={out['ad_topup']} spun={out['spun']} "
              f"winning slots={out['results']}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

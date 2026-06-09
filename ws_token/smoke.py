"""Live Step-1 acceptance runner for the ws_token WSGameClient.

Connect -> role_login -> send a read RPC (fly_pet_info_c2s 16898) -> hold with
heartbeat for N seconds -> read again to prove the connection survived. This is
the Step-1 acceptance: login + heartbeat >2min + a read RPC over pure WS.

WARNING: logging in over WS kicks that account's active session (App / web /
bot). Use an idle account.

Usage (run from repo root):
  python -m ws_token.smoke --device 7fe98fc6 --hold 130
  python -m ws_token.smoke --device emulator-5554 --refresh --hold 10
"""
from __future__ import annotations

import argparse
import time

from ws_token.client import WSGameClient
from ws_token.codec import walk
from ws_token.creds import load_creds, refresh_creds

CMD_FLY_PET_INFO = 16898  # fly.fly_pet_info_c2s (empty body) -> repeated p_fly_pet


def count_pets(body: bytes) -> tuple[int, list]:
    """fly_pet_info_s2c: field 1 repeated p_fly_pet. Return (count, first names)."""
    pets = [v for fn, v in walk(body)
            if fn == 1 and isinstance(v, (bytes, bytearray))]
    names = []
    for v in pets[:5]:
        name = dict(walk(v)).get(7)
        if isinstance(name, (bytes, bytearray)):
            try:
                name = name.decode("utf-8")
            except UnicodeDecodeError:
                name = None
        names.append(name)
    return len(pets), names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--user", type=int, default=None,
                    help="Android user id of a dual-app clone (e.g. 999)")
    ap.add_argument("--hold", type=float, default=130.0,
                    help="seconds to hold with heartbeat (>120 proves keepalive)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-scrape a fresh ticket via adb_token_login first "
                         "(cold-restarts the App, ~30s)")
    args = ap.parse_args()

    creds = (refresh_creds(args.device, user=args.user)
             if args.refresh else load_creds(args.device))
    print(f"[smoke] device={args.device} uid={creds.uid} role_id={creds.role_id}")
    print(f"[smoke] ws={creds.ws_url[:48]}...", flush=True)

    client = WSGameClient(creds)
    info = client.connect()
    print(f"[smoke] role_login_s2c code={info['code']} role_id={info['role_id']} "
          f"serv_time={info['serv_time']}", flush=True)
    try:
        n, names = count_pets(client.call(CMD_FLY_PET_INFO, b""))
        print(f"[smoke] fly_pet_info_s2c: {n} pets; first={names}", flush=True)

        if args.hold > 0:
            print(f"[smoke] holding {args.hold:.0f}s with heartbeat...", flush=True)
            end = time.time() + args.hold
            while time.time() < end:
                time.sleep(2)
            n2, _ = count_pets(client.call(CMD_FLY_PET_INFO, b""))
            print(f"[smoke] still alive after {args.hold:.0f}s: re-read {n2} pets",
                  flush=True)

        print("[smoke] PASS")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

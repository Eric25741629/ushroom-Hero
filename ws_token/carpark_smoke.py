"""Live cross car-park smoke: read a lot's slots (dry) or park into a free slot.

  python -m ws_token.carpark_smoke --device 7fe98fc6 --target 5001        # dry: print slots
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001          # auto-park first free slot
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001:3        # park into a specific pos
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001 --old    # use the legacy 12832 body

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Parking a mount into a cross lot is irreversible (you'd have to stop it manually
in-game). Default is DRY — pass --park to actually park.

Scope is STRICTLY parking into a cross (type==3) lot. No collecting, no battle.
"""
from __future__ import annotations

import argparse

from ws_token import carpark
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_park_arg(value: str) -> tuple[int, int | None]:
    """``"5001"`` -> (5001, None); ``"5001:3"`` -> (5001, 3)."""
    if ":" in value:
        tid, pos = value.split(":", 1)
        return int(tid), int(pos)
    return int(value), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--target", type=int,
                    help="master_id of the cross lot to read (dry run)")
    ap.add_argument("--park", metavar="TARGET[:POS]",
                    help="actually park into this cross lot (auto-pick free slot, "
                         "or :POS to force a position)")
    ap.add_argument("--old", action="store_true",
                    help="use the legacy 12832 start body instead of 12847")
    args = ap.parse_args()

    if not args.target and not args.park:
        ap.error("pass --target <id> (dry) or --park <id[:pos]>")

    creds = load_creds(args.device)
    print(f"[carpark] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[carpark] login code={info['code']} role_id={info['role_id']}", flush=True)
    new = not args.old
    try:
        if args.park:
            return _do_park(client, args.park, new=new)
        return _do_dry(client, args.target)
    finally:
        client.close()


def _print_lot(lot: carpark.CarParkLot) -> None:
    tag = "CROSS" if lot.is_cross else f"type={lot.type}"
    print(f"[carpark] lot master_id={lot.master_id} {tag} ceng={lot.ceng} "
          f"slots={len(lot.spaces)} free={lot.free_positions()}", flush=True)
    for s in lot.spaces:
        state = f"role_id={s.role_id}" if s.occupied else "EMPTY"
        print(f"    pos={s.pos} {state}", flush=True)


def _do_dry(client: WSGameClient, target_id: int) -> int:
    lot = carpark.read_lot(client, type=carpark.CROSS_TYPE, master_id=target_id)
    _print_lot(lot)
    if not lot.is_cross:
        print(f"[carpark] WARNING: lot type={lot.type} is not cross (expected 3)",
              flush=True)
    mounts = carpark.read_my_mounts(client)
    print(f"[carpark] available mounts: {[m.mount_id for m in mounts]}", flush=True)
    print("[carpark] (dry run) pass --park to actually park.", flush=True)
    return 0


def _do_park(client: WSGameClient, park_arg: str, *, new: bool) -> int:
    target_id, pos = _parse_park_arg(park_arg)
    lot = carpark.read_lot(client, type=carpark.CROSS_TYPE, master_id=target_id)
    _print_lot(lot)
    if not lot.is_cross:
        print(f"[carpark] ABORT: lot type={lot.type} is not cross (expected 3)",
              flush=True)
        return 1

    if pos is None:
        result = carpark.auto_park_cross(client, target_id=target_id, new=new)
        print(f"[carpark] auto_park parked={result['parked']} reason={result['reason']} "
              f"pos={result['pos']} mount_id={result['mount_id']}", flush=True)
        r = result["result"]
    else:
        mounts = carpark.read_my_mounts(client)
        if not mounts:
            print("[carpark] ABORT: no available mount", flush=True)
            return 1
        mount_id = mounts[0].mount_id
        r = carpark.park_into_cross(client, target_id=target_id, pos=pos,
                                    mount_id=mount_id, new=new)
        print(f"[carpark] park pos={pos} mount_id={mount_id} success={r.success}",
              flush=True)

    if r is not None:
        tag = "SUCCESS" if r.success else f"ERR code={r.error_code}"
        print(f"[carpark] -> cmd=0x{r.response_cmd:04x} {tag} fields={r.fields} "
              f"raw={r.response_body.hex()}", flush=True)
        return 0 if r.success else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

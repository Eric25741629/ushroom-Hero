"""Live runner for 農場/打工 over pure WS — dry by default.

  python -m ws_token.farm_smoke --device 7fe98fc6                 # read state (dry)
  python -m ws_token.farm_smoke --device 7fe98fc6 --plant 5001    # plant on empties
  python -m ws_token.farm_smoke --device 7fe98fc6 --harvest       # harvest ready
  python -m ws_token.farm_smoke --device 7fe98fc6 --work 101      # start 打工 (管家)

role_id defaults to the device's own creds.role_id (own farm).

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Planting / harvesting / starting 打工 mutate the real account and are irreversible.
seed_id (--plant) and team_cfg_id (--work) are live-confirm values: read them from
a live session once and pass them explicitly.
"""
from __future__ import annotations

import argparse

from ws_token import farm
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _print_state(info: farm.FarmInfo) -> None:
    print(f"[farm] role_id={info.role_id} name={info.name!r} level={info.level} "
          f"exp={info.exp} lands={len(info.lands)}", flush=True)
    for land in info.lands:
        tag = "EMPTY" if land.is_empty else ("READY" if land.is_ready else "GROWING")
        print(f"  land_id={land.id} state={land.state} end_time={land.end_time} "
              f"seed_id={land.seed_id} cfg_id={land.cfg_id} -> {tag}", flush=True)
    print(f"[farm] empty={len(info.empty_lands)} ready={len(info.ready_lands)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--role-id", type=int, default=None,
                    help="target farm role_id (default: own creds.role_id)")
    ap.add_argument("--plant", type=int, metavar="SEED_ID",
                    help="plant this seed_id on every empty land")
    ap.add_argument("--harvest", action="store_true",
                    help="harvest every ready (MATURE) land")
    ap.add_argument("--work", type=int, metavar="TEAM_CFG_ID",
                    help="start 打工 (管家自動種+收) with this team_cfg_id")
    args = ap.parse_args()

    creds = load_creds(args.device)
    role_id = args.role_id if args.role_id is not None else creds.role_id
    print(f"[farm] device={args.device} uid={creds.uid} role_id={role_id}", flush=True)
    client = WSGameClient(creds)
    info_login = client.connect()
    print(f"[farm] login code={info_login['code']} role_id={info_login['role_id']}", flush=True)
    try:
        info = farm.read_farm(client, role_id)
        _print_state(info)

        if args.plant is not None:
            summary = farm.plant_empty(client, role_id, args.plant, info=info)
            print(f"[farm] plant: attempted={summary['attempted']} "
                  f"planted={summary['planted']}", flush=True)
            for r in summary["results"]:
                print(f"  land_id={r['land_id']} code={r['code']} ok={r['ok']}", flush=True)

        if args.harvest:
            summary = farm.harvest_ready(client, role_id, info=info)
            print(f"[farm] harvest: attempted={summary['attempted']} "
                  f"harvested={summary['harvested']} rewards={summary['rewards']}", flush=True)
            for r in summary["results"]:
                print(f"  land_id={r['land_id']} code={r['code']} ok={r['ok']}", flush=True)

        if args.work is not None:
            result = farm.start_work(client, args.work)
            print(f"[farm] work: running={result['running']} "
                  f"worker_status={result['worker_status']}", flush=True)

        if args.plant is None and not args.harvest and args.work is None:
            print("[farm] (dry run) pass --plant/--harvest/--work to act.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Live runner: 加工坊 (processing workshop) over pure WS — dry by default.

  python -m ws_token.workshop_smoke --device 7fe98fc6                 # read state (dry)
  python -m ws_token.workshop_smoke --device 7fe98fc6 --choose 501:3:2  # start (food_k:food_v:workshop_id)
  python -m ws_token.workshop_smoke --device 7fe98fc6 --cancel 2         # cancel workshop 2
  python -m ws_token.workshop_smoke --device 7fe98fc6 --collect 6001:50  # transfer out (material_id:num)

WARNING: logging in over WS kicks that account's active session (App / web / bot).
DRY-RUN by default: it only reads info + dining_hall and prints the workshops and
foods. Mutations are irreversible and gated behind explicit flags:
  --choose assigns食物 and starts processing; --cancel stops a running workshop;
  --collect transfers finished material out. The food ids, workshop ids, and the
  collect material_id/num are LIVE-CONFIRM values — read them off the dry-run
  first and pass them explicitly.
"""
from __future__ import annotations

import argparse

from ws_token import workshop
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_pair(tok: str, want: int, label: str) -> tuple[int, ...]:
    parts = [int(p) for p in tok.split(":")]
    if len(parts) != want:
        raise SystemExit(f"{label} needs {want} ':'-separated ints, got {tok!r}")
    return tuple(parts)


def _print_state(info: workshop.WorkshopInfo, foods) -> None:
    print(f"[workshop] auto_use_food_list={list(info.auto_use_food_list)} "
          f"workshops={len(info.workshops)}", flush=True)
    for w in info.workshops:
        tag = "RUNNING" if w.is_running else "IDLE"
        print(f"  team_cfg_id={w.team_cfg_id} worker_status={w.worker_status} "
              f"auto_feed={w.auto_feed} unlock_slot_num={w.unlock_slot_num} "
              f"pw_info_len={len(w.pw_worker_info)} -> {tag}", flush=True)
    print(f"[workshop] dining_hall foods={len(foods)}", flush=True)
    for food_id, count in foods:
        print(f"  food_id={food_id} count={count}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--choose", metavar="FOOD_K:FOOD_V:WORKSHOP_ID",
                    help="assign food and start a workshop (SPENDS food)")
    ap.add_argument("--cancel", type=int, metavar="WORKSHOP_ID",
                    help="cancel a running workshop")
    ap.add_argument("--collect", metavar="MATERIAL_ID:NUM",
                    help="crops_transfer finished material out (live-confirm ids)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[workshop] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info_login = client.connect()
    print(f"[workshop] login code={info_login['code']} role_id={info_login['role_id']}",
          flush=True)
    try:
        info = workshop.read_info(client)
        foods = workshop.read_dining_hall(client)
        _print_state(info, foods)

        if args.choose:
            food_k, food_v, workshop_id = _parse_pair(args.choose, 3, "--choose")
            result = workshop.choose_food(
                client, food_k=food_k, food_v=food_v, workshop_id=workshop_id)
            print(f"[workshop] choose_food ok={result['ok']} "
                  f"reason={result.get('reason')} "
                  f"(confirmed by 18434 re-read)", flush=True)

        if args.cancel is not None:
            result = workshop.cancel_work(client, args.cancel)
            print(f"[workshop] cancel_work ok={result['ok']} "
                  f"error_code={result['error_code']}", flush=True)

        if args.collect:
            material_id, num = _parse_pair(args.collect, 2, "--collect")
            result = workshop.collect(client, material_id=material_id, num=num)
            print(f"[workshop] collect ok={result['ok']} "
                  f"error_code={result['error_code']}", flush=True)

        if not args.choose and args.cancel is None and not args.collect:
            print("[workshop] (dry run) pass --choose/--cancel/--collect to act.",
                  flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

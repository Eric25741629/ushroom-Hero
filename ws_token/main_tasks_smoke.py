"""Live runner: read + (optionally) claim main-screen tasks over pure WS.

Read is PUSH-based, so a TaskCollector is mounted as the client's push_handler
BEFORE connect() to capture the login-time task_all / daily_point / weekly_box
frames. Default is a dry run (collect + print state, no claims).

  python -m ws_token.main_tasks_smoke --device 7fe98fc6                  # dry: just show state
  python -m ws_token.main_tasks_smoke --device 7fe98fc6 --claim-tasks    # commit claimable dailies
  python -m ws_token.main_tasks_smoke --device 7fe98fc6 --daily-box      # claim activity boxes
  python -m ws_token.main_tasks_smoke --device 7fe98fc6 --weekly-box     # claim weekly box
  python -m ws_token.main_tasks_smoke --device 7fe98fc6 --achievement    # claim achievement milestones
  python -m ws_token.main_tasks_smoke --device 7fe98fc6 --all            # all of the above

WARNING: logging in over WS kicks that account's active session (App / web /
bot). Every claim is a free, already-earned reward (no cost), but irreversible.
"""
from __future__ import annotations

import argparse

from ws_token import main_tasks
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

_TYPE_NAMES = {
    main_tasks.TYPE_MAIN: "Main",
    main_tasks.TYPE_DAILY: "Daily",
    main_tasks.TYPE_ACHIEVEMENT: "Achievement",
    main_tasks.TYPE_FLYPET: "FlyPet",
}
_STATE_NAMES = {
    main_tasks.STATE_PENDING: "未達成",
    main_tasks.STATE_CLAIMABLE: "可領",
    main_tasks.STATE_CLAIMED: "已領",
}


def _print_state(state: main_tasks.TaskState) -> None:
    print(f"[tasks] daily_point={state.daily_point} "
          f"weekly_status={state.weekly_status} "
          f"({len(state.tasks)} task(s), {len(state.daily_boxes)} daily box(es))",
          flush=True)
    for t in state.tasks:
        tn = _TYPE_NAMES.get(t.type, str(t.type))
        sn = _STATE_NAMES.get(t.state, str(t.state))
        print(f"  task_id={t.task_id} type={tn} state={sn} count={t.count}", flush=True)
    for box_id, st in state.daily_boxes:
        tag = "可領" if st == main_tasks.BOX_CLAIMABLE else "已領"
        print(f"  daily_box id={box_id} state={tag}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--settle", type=float, default=1.5,
                    help="seconds to wait for login pushes before snapshotting")
    ap.add_argument("--claim-tasks", action="store_true",
                    help="commit every claimable daily task (task_commit)")
    ap.add_argument("--daily-box", action="store_true",
                    help="claim the daily activity boxes (task_req_daily_box)")
    ap.add_argument("--weekly-box", action="store_true",
                    help="claim the weekly box (task_req_weekly_box)")
    ap.add_argument("--achievement", action="store_true",
                    help="claim reached achievement milestones")
    ap.add_argument("--all", action="store_true",
                    help="enable every claim flag above")
    args = ap.parse_args()

    do_tasks = args.claim_tasks or args.all
    do_daily = args.daily_box or args.all
    do_weekly = args.weekly_box or args.all
    do_ach = args.achievement or args.all

    creds = load_creds(args.device)
    print(f"[tasks] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)

    # PUSH-based read: mount the collector BEFORE connect so login pushes land.
    collector = main_tasks.TaskCollector()
    client = WSGameClient(creds, push_handler=collector)
    info = client.connect()
    print(f"[tasks] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        state = main_tasks.collect_state(client, collector, settle=args.settle)
        _print_state(state)

        if not (do_tasks or do_daily or do_weekly or do_ach):
            print("[tasks] (dry run) pass --claim-tasks/--daily-box/--weekly-box/"
                  "--achievement/--all to actually claim.", flush=True)
            return 0

        if do_tasks:
            summary = main_tasks.claim_daily_tasks(client, state)
            print(f"[tasks] daily commit: attempted={summary['attempted']} "
                  f"claimed={summary['claimed']}", flush=True)
        if do_daily:
            done = main_tasks.claim_daily_box(client, state)
            print(f"[tasks] daily box claimed={done}", flush=True)
        if do_weekly:
            done = main_tasks.claim_weekly_box(client, state)
            print(f"[tasks] weekly box claimed={done}", flush=True)
        if do_ach:
            summary = main_tasks.claim_achievement(client)
            print(f"[tasks] achievement: claimed={summary['claimed']} "
                  f"get_id={summary['get_id']} now_id={summary['now_id']} "
                  f"error={summary['error']}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

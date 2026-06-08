"""Live runner: family-hall (家族大廳) daily over pure WS.

  python -m ws_token.guild_smoke --device 7fe98fc6              # read-only (dry)
  python -m ws_token.guild_smoke --device 7fe98fc6 --donate     # actually donate
  python -m ws_token.guild_smoke --device 7fe98fc6 --treasure   # actually open boxes
  python -m ws_token.guild_smoke --device 7fe98fc6 --help       # actually answer helps
  python -m ws_token.guild_smoke --device 7fe98fc6 --help-type 0

DRY by default: only the info/list reads run unless an action flag is given.
WARNING: logging in over WS kicks that account's active session (App / web /
bot). Donating / opening boxes / helping are irreversible daily actions.
"""
from __future__ import annotations

import argparse

from ws_token import guild
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _read_only(client: WSGameClient, help_type: int) -> None:
    """The dry-run read path: dump donate state, treasure, help list + status."""
    treasure = guild.list_treasure(client)
    print(f"[guild] treasure round={treasure.round} my_open={treasure.my_open} "
          f"boxes={len(treasure.box_list)} countdown={treasure.countdown}", flush=True)
    for box in treasure.box_list:
        print(f"  box n={box.n} pos={box.pos} open={box.open_num}/{box.open_limit} "
              f"room={box.has_room}", flush=True)

    statuses = guild.list_help_status(client)
    print(f"[guild] help_status: {len(statuses)} channel(s)", flush=True)
    for st in statuses:
        print(f"  type={st.type} sub_type={st.sub_type} status={st.status}", flush=True)

    helps = guild.list_help(client, help_type)
    print(f"[guild] help_info(type={help_type}): {len(helps)} request(s)", flush=True)
    for h in helps:
        print(f"  help_id={h.help_id} role={h.role_id} name={h.name!r} "
              f"type={h.type} sub_type={h.sub_type} num={h.help_num}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--help-type", type=int, default=guild.DEFAULT_HELP_TYPE,
                    dest="help_type", help="guild_help_info query type (live-confirm)")
    ap.add_argument("--donate", action="store_true",
                    help="actually donate (guild_donate) until capped")
    ap.add_argument("--treasure", action="store_true",
                    help="actually open treasure boxes (guild_treasure_open)")
    ap.add_argument("--help", dest="do_help", action="store_true",
                    help="actually answer guildmate help requests (guild_help)")
    ap.add_argument("--daily-limit", type=int, default=None, dest="daily_limit",
                    help="cap for --help (remaining = daily_limit - daily_count)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[guild] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[guild] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        _read_only(client, args.help_type)

        if not (args.donate or args.treasure or args.do_help):
            print("[guild] (dry run) pass --donate / --treasure / --help to act.",
                  flush=True)
            return 0

        if args.donate:
            summary = guild.donate_until_capped(client)
            print(f"[guild] donate -> {summary}", flush=True)
        if args.treasure:
            summary = guild.open_all_treasure(client)
            print(f"[guild] treasure -> {summary}", flush=True)
        if args.do_help:
            summary = guild.help_all(client, help_type=args.help_type,
                                     daily_limit=args.daily_limit)
            print(f"[guild] help -> {summary}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

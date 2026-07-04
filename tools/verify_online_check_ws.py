"""Live verification for the pure-WS online-check path (online_check_service).

Calls runtime_services.ws_online_checker.check_via_ws against a target player id
using one or more checker devices' captured creds and prints:
  True  -> ONLINE        (do NOT run; protect the human)
  False -> OFFLINE       (safe to run)
  None  -> UNDETERMINED  (target not visible to that checker: not a friend and
                          no/!match guild_id)

Usage:
    python tools/verify_online_check_ws.py [target_pid] [checker ...]

Defaults: target = emulator-5558's online_check_target_pid;
          checkers = emulator-5554/5556/5560.

WARNING: check_via_ws logs in with the checker's own account, which kicks any
*active* session for that account (异地登入). Only run with the bot stopped, or
against idle/sleeping checkers.
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_manager
from runtime_services.ws_online_checker import check_via_ws

logging.basicConfig(level=logging.INFO, format="%(message)s")

_LABEL = {True: "ONLINE", False: "OFFLINE", None: "UNDETERMINED (not friend/guild)"}


def main(argv):
    if argv and argv[0].isdigit():
        target = int(argv[0])
        checkers = argv[1:]
    else:
        target = int(config_manager.get_device_config("emulator-5558").get(
            "online_check_target_pid"))
        checkers = argv
    if not checkers:
        checkers = ["emulator-5554", "emulator-5556", "emulator-5560"]

    print(f"target_pid={target}")
    for c in checkers:
        guild = config_manager.get_device_config(c).get("online_check_guild_id") or None
        try:
            r = check_via_ws(c, target, guild_id=guild)
        except Exception as e:  # noqa: BLE001
            print(f"  {c:20s} -> ERROR: {e}")
            continue
        print(f"  {c:20s} -> {r!r:7s} [{_LABEL.get(r, r)}]"
              + (f"  guild_id={guild}" if guild else ""))


if __name__ == "__main__":
    main(sys.argv[1:])

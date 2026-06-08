"""Live runner: read + claim 烈焰山洞 / 魔法劇場 boxes over pure WS.

  python -m ws_token.league_solo_smoke --device 7fe98fc6                       # dry: show box state
  python -m ws_token.league_solo_smoke --device 7fe98fc6 --claim               # claim all claimable (types 1-4)
  python -m ws_token.league_solo_smoke --device 7fe98fc6 --claim --types 1,2   # only lava (daily)
  python -m ws_token.league_solo_smoke --device 7fe98fc6 --claim --types 3,4   # only theatre (weekly)

Claiming a league-solo box is a free reward (boxes already earned, no cost), but
logging in over WS kicks that account's active session (App / web / bot).
"""
from __future__ import annotations

import argparse

from ws_token import league_solo
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_types(raw: str | None) -> tuple[int, ...] | None:
    if not raw:
        return None
    return tuple(int(part) for part in raw.split(",") if part.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--claim", action="store_true",
                    help="actually send get_reward (0x0E0F) for each claimable box")
    ap.add_argument("--types", default=None,
                    help="comma list of box types to claim (default 1,2,3,4); "
                         "1,2=lava daily, 3,4=theatre weekly")
    args = ap.parse_args()

    types = _parse_types(args.types)
    creds = load_creds(args.device)
    print(f"[league_solo] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[league_solo] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        boxes = league_solo.read_boxes(client)
        print(f"[league_solo] solo_info (0x0E0E): {len(boxes)} box type(s)", flush=True)
        for b in boxes:
            tag = "CLAIMABLE" if b.claimable else "maxed"
            print(f"  type={b.type} count={b.count} got_count={b.got_count} "
                  f"-> {tag} rare={b.rare_offer_name!r}", flush=True)
        if not boxes:
            print("[league_solo] no boxes listed.")
            return 0
        if not args.claim:
            print("[league_solo] (dry run) pass --claim to actually claim.")
            return 0

        summary = league_solo.claim_available(client, types=types)
        print(f"[league_solo] attempted={summary['attempted']} claimed={summary['claimed']} "
              f"already={summary['already']} skipped_maxed={summary['skipped_maxed']}",
              flush=True)
        for r in summary["results"]:
            if r.success:
                tag = f"SUCCESS cmd=0x{r.response_cmd:04x}"
            elif r.already_claimed:
                tag = "ALREADY (error 159)"
            else:
                tag = f"ERR code={r.error_code}"
            print(f"  type={r.type} -> {tag} fields={r.fields}", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

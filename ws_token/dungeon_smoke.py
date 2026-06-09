"""Live runner: list dungeons + sweep/battle 深淵之門 / 萬神試煉 over pure WS.

  python -m ws_token.dungeon_smoke --device 7fe98fc6                       # dry: list dungeons
  python -m ws_token.dungeon_smoke --device 7fe98fc6 --sweep 23:4001:2     # 掃蕩 type:dungeon_id:num
  python -m ws_token.dungeon_smoke --device 7fe98fc6 --battle 2:5          # 戰鬥 type:level (anti-cheat 風險)

Prefer --sweep: it takes the reward directly and bypasses the client-side battle
+ anti-cheat. --battle sends a client-reported result=0 which is NOT live-verified
and may be rejected by checkCheat. Logging in over WS kicks that account's active
session (App / web / bot).
"""
from __future__ import annotations

import argparse

from ws_token import dungeon
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_triple(raw: str, n: int, label: str) -> tuple[int, ...]:
    parts = [p for p in raw.split(":") if p.strip()]
    if len(parts) != n:
        raise SystemExit(f"--{label} expects {n} colon-separated ints, got {raw!r}")
    return tuple(int(p) for p in parts)


def _type_name(type_: int) -> str:
    if type_ == dungeon.TYPE_ABYSS:
        return "深淵之門"
    if type_ == dungeon.TYPE_SEVEN_TRIAL:
        return "萬神試煉"
    return f"type{type_}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--sweep", default=None, metavar="type:dungeon_id:num",
                    help="掃蕩 (preferred): send dungeon_sweep and take the reward")
    ap.add_argument("--battle", default=None, metavar="type:level",
                    help="戰鬥 (anti-cheat 風險, result=0 未驗證): start -> report win")
    args = ap.parse_args()

    creds = load_creds(args.device)
    print(f"[dungeon] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[dungeon] login code={info['code']} role_id={info['role_id']}", flush=True)
    try:
        dungeons = dungeon.list_dungeons(client)
        print(f"[dungeon] dungeon_list (0x0E01): {len(dungeons)} dungeon(s)", flush=True)
        for d in dungeons:
            print(f"  {_type_name(d.type)} type={d.type} max_level={d.max_level} "
                  f"cur_level={d.cur_level} day_times={d.day_times} "
                  f"({'有次數' if d.has_attempts else '今日已滿'})", flush=True)

        if args.sweep:
            type_, dungeon_id, num = _parse_triple(args.sweep, 3, "sweep")
            print(f"[dungeon] SWEEP {_type_name(type_)} type={type_} "
                  f"dungeon_id={dungeon_id} num={num}", flush=True)
            r = dungeon.run_sweep(client, type=type_, dungeon_id=dungeon_id,
                                  sweep_num=num)
            tag = f"SUCCESS rewards={r.rewards}" if r.success else f"ERR code={r.error_code}"
            print(f"[dungeon] sweep -> cmd=0x{r.response_cmd:04x} {tag}", flush=True)
            print(f"          fields={r.fields}", flush=True)
            return 0

        if args.battle:
            type_, level = _parse_triple(args.battle, 2, "battle")
            print(f"[dungeon] BATTLE {_type_name(type_)} type={type_} level={level} "
                  f"(result=0 未驗證, anti-cheat 可能拒)", flush=True)
            r = dungeon.run_battle(client, type=type_, level=level)
            tag = f"SUCCESS rewards={r.rewards}" if r.success else f"ERR code={r.error_code}"
            print(f"[dungeon] battle -> cmd=0x{r.response_cmd:04x} {tag} "
                  f"dungeon_id={r.dungeon_id} seed={r.random_seed}", flush=True)
            print(f"          fields={r.fields}", flush=True)
            return 0

        print("[dungeon] (dry run) pass --sweep type:dungeon_id:num "
              "or --battle type:level to act.", flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

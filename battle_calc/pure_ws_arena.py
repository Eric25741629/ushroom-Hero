# -*- coding: utf-8 -*-
"""CLI：pure WS 競技場 + 免洗 B（預設全新瀏覽器、無 profile）。

用法::

    python -m battle_calc.pure_ws_arena --device 7fe98fc6 --fights 3
    python -m battle_calc.pure_ws_arena --device 7fe98fc6 --b-mode cdp --cdp-port 9226
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from typing import Optional

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from battle_calc.config import DEFAULT_ARENA_GAP_SEC, MIN_ARENA_GAP_SEC, coerce_arena_gap_sec


def run_fights(
    device_id: str,
    *,
    fights: int = 3,
    gap_sec: float = DEFAULT_ARENA_GAP_SEC,
    b_mode: str = "ephemeral",
    cdp_port: Optional[int] = None,
    headless: bool = True,
    game_url: str = "https://mushroomh5.acenetgame.com/",
) -> int:
    from ws_token import arena_fight as af
    from ws_token.client import WSGameClient
    from ws_token.creds import load_creds

    gap_sec = coerce_arena_gap_sec(gap_sec)
    prefer_ephemeral = str(b_mode or "ephemeral").lower() != "cdp"
    creds = load_creds(device_id)
    print(f"pure WS login role={creds.role_id}")
    print(f"B mode={'ephemeral(no profile)' if prefer_ephemeral else f'cdp:{cdp_port}'}")
    client = WSGameClient(creds)
    client.connect()
    try:
        report = af.run_with_b(
            client,
            fights=fights,
            gap_sec=gap_sec,
            prefer_ephemeral=prefer_ephemeral,
            cdp_port=cdp_port,
            game_url=game_url,
            headless=headless,
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.success else 5
    finally:
        try:
            client.close()
        except Exception:
            pass


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Arena pure WS + ephemeral B browser")
    p.add_argument("--device", default="7fe98fc6")
    p.add_argument("--fights", type=int, default=3)
    p.add_argument("--gap", type=float, default=DEFAULT_ARENA_GAP_SEC)
    p.add_argument("--b-mode", choices=("ephemeral", "cdp"), default="ephemeral")
    p.add_argument("--cdp-port", type=int, default=0, help="only for --b-mode cdp")
    p.add_argument("--headed", action="store_true", help="show B browser window")
    p.add_argument("--game-url", default="https://mushroomh5.acenetgame.com/")
    args = p.parse_args(argv)
    if args.gap < MIN_ARENA_GAP_SEC:
        args.gap = MIN_ARENA_GAP_SEC
    return run_fights(
        args.device,
        fights=args.fights,
        gap_sec=args.gap,
        b_mode=args.b_mode,
        cdp_port=args.cdp_port or None,
        headless=not args.headed,
        game_url=args.game_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())

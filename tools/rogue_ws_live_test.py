# -*- coding: utf-8 -*-
"""萬神試煉 pure_ws 完整 live 測試 — 小寶 7fe98fc6，跑 1 局（enter/combat/result/over）。

用 ephemeral B（全新無 profile 瀏覽器）避免碰裝置自己的 Playwright 頁。
連線後印出：enter/status/over 是否成功、每關 sim_ms/result/precent/elapsed，
以及與 local_sim baseline(13.6s/6 stages) 的比較。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ws_token.creds import load_creds
from ws_token.client import WSGameClient
from ws_token import rogue as rogue_mod
from ws_token import rogue_fight as rf


def _print_info(client: WSGameClient, tag: str) -> None:
    try:
        info = rogue_mod.fetch_info(client)
        status = rogue_mod.fetch_status(client)
        print(f"[live] {tag} rogue_info point(試煉之心)={info.point} score={info.score} "
              f"fields={ {k: v for k, v in info.fields.items() if not isinstance(v, (bytes, bytearray))} } "
              f"status.raw={status.raw_status}")
    except Exception as e:
        print(f"[live] {tag} rogue_info/status failed: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="7fe98fc6")
    ap.add_argument("--auth-dir", default=None)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--stages", type=int, default=80)
    args = ap.parse_args()

    auth_kw = {}
    if args.auth_dir:
        auth_kw["auth_dir"] = Path(args.auth_dir)

    print(f"[live] load creds for {args.device}")
    creds = load_creds(args.device, **auth_kw)
    client = WSGameClient(creds)
    t0 = time.monotonic()
    try:
        print("[live] connect + login...")
        login = client.connect()
        print(f"[live] login ok: {login}")
        _print_info(client, "BEFORE")

        print(f"[live] run_with_b rounds={args.rounds} stages={args.stages} (ephemeral B)")
        report = rf.run_with_b(
            client,
            rounds=args.rounds,
            stages=args.stages,
            prefer_ephemeral=True,
        )
        elapsed = time.monotonic() - t0
        print(f"\n[live] === RESULT ===")
        print(f"[live] success={report.success} rounds_completed={report.rounds_completed}/{args.rounds}")
        print(f"[live] stages_fought={report.stages_fought} stages_won={report.stages_won}")
        print(f"[live] error={report.error}")
        for o in report.outcomes:
            print(f"[live] stage={o.stage} ok={o.ok} result={o.result} precent={o.precent} "
                  f"sim_ms={o.sim_ms} error={o.error}")
        print(f"[live] total elapsed={elapsed:.1f}s (local_sim baseline: 13.6s/6 stages)")
        _print_info(client, "AFTER")
        return 0 if report.success else 1
    finally:
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

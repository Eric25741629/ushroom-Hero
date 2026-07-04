"""Probe: pin down how the turntable's ad-funded top-up (configAds AD_TURNTABLE,
config_id 13, times=2/day, cd=300s) composes with the wheel's own spin cmd.

The wheel exposes its own cmds (0x1603 ad_wheel_info / 0x1604 ad_wheel_spin).
The open question this probe answers: does ``ad_reward(13, is_free=1)`` GRANT a
spin (so ``num`` bumps and we then 0x1604-spin it), or does the claim itself
deliver the reward (``num`` unchanged, no separate spin)?

Reads num/count BEFORE, claims ONE config-13 ad reward, reads num/count AFTER,
then tries one spin and reads num again — the deltas reveal the compose.

  python -m tools.probe_turntable_ad --device 7fe98fc6           # dry: read only
  python -m tools.probe_turntable_ad --device 7fe98fc6 --claim   # claim 1 + spin 1

WARNING: logging in over WS kicks that account's live session (App/web/bot).
``--claim`` mutates (consumes a daily ad-spin + a wheel spin — both free,
renewable rewards). The wheel is an EVENT wheel: when the event is closed every
call returns 0x0201 code 173 (活動已結束) and the compose can't be observed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ws_token import ad_reward, turntable  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.creds import load_creds  # noqa: E402

AD_TURNTABLE_CONFIG_ID = 13


def _snapshot(client) -> tuple[turntable.TurntableInfo, dict]:
    info = turntable.read_info(client)
    counts = ad_reward.read_ad_counts(client)
    ad13 = counts.get(AD_TURNTABLE_CONFIG_ID) or {}
    return info, ad13


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--claim", action="store_true",
                    help="actually claim 1 ad-reward(13) then spin (mutates)")
    args = ap.parse_args()

    creds = load_creds(args.device)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[probe] device={args.device} login code={info['code']} "
          f"role_id={info['role_id']}", flush=True)
    try:
        wheel0, ad0 = _snapshot(client)
        now = time.time()
        print(f"[probe] BEFORE  wheel num={wheel0.num} cd={wheel0.cd} "
              f"(cd in {wheel0.cd - now:.0f}s)" if wheel0.cd else
              f"[probe] BEFORE  wheel num={wheel0.num} cd=0", flush=True)
        print(f"[probe]         ad[13] count={ad0.get('count', 0)} "
              f"next_ts={ad0.get('next_ts', 0)} "
              f"(cap={ad_reward.TIMES.get(AD_TURNTABLE_CONFIG_ID)})", flush=True)

        if not args.claim:
            print("[probe] (dry run) pass --claim to claim 1 ad reward + spin.")
            return 0

        r = ad_reward.claim_once(client, AD_TURNTABLE_CONFIG_ID)
        print(f"[probe] CLAIM   success={r.success} cmd=0x{r.response_cmd:04x} "
              f"error_code={r.error_code} new_count={r.new_count} "
              f"next_ts={r.next_ts}", flush=True)

        wheel1, ad1 = _snapshot(client)
        print(f"[probe] AFTER-CLAIM  wheel num={wheel1.num} (was {wheel0.num}; "
              f"delta {wheel1.num - wheel0.num})  ad[13] count={ad1.get('count', 0)}",
              flush=True)

        slot = turntable.spin_once(client)
        wheel2, _ = _snapshot(client)
        print(f"[probe] SPIN    slot={slot}  wheel num={wheel2.num} "
              f"(was {wheel1.num}; delta {wheel2.num - wheel1.num})", flush=True)

        print("[probe] VERDICT: "
              + ("claim GRANTS a spin (num bumped) -> claim then 0x1604-spin"
                 if wheel1.num > wheel0.num else
                 "claim did NOT bump num -> reward delivered by claim itself"),
              flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

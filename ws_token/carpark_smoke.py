"""Live cross car-park smoke: read a lot's slots (dry) or park into a free slot.

  python -m ws_token.carpark_smoke --device 7fe98fc6 --search             # dry: list cross lots (12808 type=4)
  python -m ws_token.carpark_smoke --device 7fe98fc6 --preview            # dry: legacy preview (12830)
  python -m ws_token.carpark_smoke --device 7fe98fc6 --info 5001          # dry: one lot detail (12831)
  python -m ws_token.carpark_smoke --device 7fe98fc6 --auto-park          # search -> pick lot -> park
  python -m ws_token.carpark_smoke --device 7fe98fc6 --target 5001        # dry: print slots
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001          # auto-park first free slot
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001:3        # park into a specific pos
  python -m ws_token.carpark_smoke --device 7fe98fc6 --park 5001 --old    # use the legacy 12832 body

WARNING: logging in over WS kicks that account's active session (App / web / bot).
Parking a mount into a cross lot is irreversible (you'd have to stop it manually
in-game). Default is DRY — pass --park to actually park.

Scope is STRICTLY parking into a cross (type==3) lot. No collecting, no battle.
"""
from __future__ import annotations

import argparse

from ws_token import carpark
from ws_token.client import WSGameClient
from ws_token.creds import load_creds


def _parse_park_arg(value: str) -> tuple[int, int | None]:
    """``"5001"`` -> (5001, None); ``"5001:3"`` -> (5001, 3)."""
    if ":" in value:
        tid, pos = value.split(":", 1)
        return int(tid), int(pos)
    return int(value), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", required=True)
    ap.add_argument("--target", type=int,
                    help="master_id of the cross lot to read (dry run)")
    ap.add_argument("--park", metavar="TARGET[:POS]",
                    help="actually park into this cross lot (auto-pick free slot, "
                         "or :POS to force a position)")
    ap.add_argument("--old", action="store_true",
                    help="use the legacy 12832 start body instead of 12847")
    ap.add_argument("--search", action="store_true",
                    help="dry: list parkable cross lots via car_park_search "
                         "(12808 type=4 -> null_space)")
    ap.add_argument("--preview", action="store_true",
                    help="dry: legacy lot list via cross_car_park_preview (12830)")
    ap.add_argument("--info", type=int, metavar="ID",
                    help="dry: one cross lot detail via cross_car_park_info (12831)")
    ap.add_argument("--auto-park", action="store_true",
                    help="ACTUALLY park: preview -> auto-pick a lot with a free "
                         "slot -> park one mount")
    ap.add_argument("--plan", action="store_true",
                    help="dry: print the device's carpark_plan, which window is "
                         "active now and the next scheduled wake (no WS connection)")
    ap.add_argument("--parked", action="store_true",
                    help="read MY cross-parked cars (12802 parking_data): "
                         "start_time + elapsed + time-to-8h. Use to calibrate the "
                         "start_time epoch base against the local clock.")
    args = ap.parse_args()

    if args.plan:
        return _do_plan(args.device)

    if not any((args.target, args.park, args.search, args.preview, args.info,
                args.auto_park, args.parked)):
        ap.error("pass --search / --preview / --info <id> / --auto-park / "
                 "--target <id> / --park <id[:pos]> / --plan / --parked")

    creds = load_creds(args.device)
    print(f"[carpark] device={args.device} uid={creds.uid} role_id={creds.role_id}",
          flush=True)
    client = WSGameClient(creds)
    info = client.connect()
    print(f"[carpark] login code={info['code']} role_id={info['role_id']}", flush=True)
    new = not args.old
    try:
        if args.search:
            return _do_search(client)
        if args.preview:
            return _do_preview(client)
        if args.info:
            return _do_info(client, args.info)
        if args.auto_park:
            return _do_auto_park(client, new=new)
        if args.parked:
            return _do_parked(client, args.device)
        if args.park:
            return _do_park(client, args.park, new=new)
        return _do_dry(client, args.target)
    finally:
        client.close()


def _do_parked(client: WSGameClient, device: str) -> int:
    """Dump my cross-parked cars with start_time vs the local clock (calibration).

    Confirms whether ``start_time`` is plain unix epoch (elapsed ≈ now - start_time
    sane, 0..park_max) before trusting the 8h re-park wake. Tune
    ``carpark_plan.start_time_offset`` / ``park_max_seconds`` if it is not.
    """
    import time as _time
    from datetime import datetime

    import config_manager
    from ws_token import carpark_plan as cp

    plan_cfg = (config_manager.get_device_config(device).get("ws_token") or {}
                ).get("carpark_plan") or {}
    max_sec = cp.park_max_seconds(plan_cfg)
    offset = cp.start_time_offset(plan_cfg)
    now = _time.time()
    parked = carpark.read_parked_cross(client)
    print(f"[carpark] now={datetime.fromtimestamp(now):%Y-%m-%d %H:%M:%S} "
          f"(epoch {int(now)}) park_max={max_sec}s offset={offset}", flush=True)
    print(f"[carpark] {len(parked)} cross car(s) parked", flush=True)
    for m in parked:
        pi = m.parking_info
        st = pi.start_time + offset
        elapsed = now - st
        remain = max_sec - elapsed
        st_s = datetime.fromtimestamp(st).strftime("%Y-%m-%d %H:%M:%S") \
            if 0 < st < 4_000_000_000 else "??? (not unix epoch?)"
        print(f"    mount={m.mount_id} lot={pi.master_id} pos={pi.pos} "
              f"start_time={pi.start_time} (-> {st_s}) "
              f"elapsed={elapsed/3600:.2f}h remain_to_max={remain/3600:.2f}h",
              flush=True)
    return 0


def _do_plan(device: str) -> int:
    """Dry-run the day/night plan gate for ``device`` against the local clock."""
    from datetime import datetime

    import config_manager
    from ws_token import carpark_plan as cp
    from ws_token import state as ws_state

    cfg = (config_manager.get_device_config(device).get("ws_token") or {})
    plan_cfg = cfg.get("carpark_plan") or {}
    print(f"[carpark] plan cfg: {plan_cfg}", flush=True)
    if not plan_cfg.get("enabled"):
        print("[carpark] plan disabled (ws_token.carpark_plan.enabled=false)",
              flush=True)
        return 0
    now = datetime.now()
    win = cp.active_window(cp.parse_plan(plan_cfg), now)
    if win is None:
        print(f"[carpark] now={now:%H:%M} -> no active window", flush=True)
        return 0
    st = ws_state.load_state(device)
    plans = cp.parse_plan(plan_cfg)
    # current-parked model: need = win.cross - (live parked count). This dry
    # preview has no client, so it shows the target + the scheduler's next wake.
    nxt = cp.carpark_wake_ts([], plans, now,
                             max_sec=cp.park_max_seconds(plan_cfg),
                             open_lead=cp.open_lead_seconds(plan_cfg),
                             margin=cp.repark_margin_seconds(plan_cfg),
                             offset=cp.start_time_offset(plan_cfg))
    nxt_s = datetime.fromtimestamp(nxt).strftime("%m-%d %H:%M:%S") if nxt else None
    stored = (st.get("carpark_repark") or {}).get("next_ts")
    print(f"[carpark] now={now:%H:%M} window={win.name} target_cross={win.cross} "
          f"(current-parked model: need = target - live parked count)", flush=True)
    print(f"[carpark] park_max={cp.park_max_seconds(plan_cfg)}s "
          f"open_lead={cp.open_lead_seconds(plan_cfg)}s "
          f"next_wake(computed,no-car)={nxt_s} stored_next_ts={stored}", flush=True)
    levels = plan_cfg.get("silver_levels") or list(carpark.SILVER_PREFERRED_LEVELS)
    print(f"[carpark] silver-only, prefer 鉑銀{levels} "
          f"(ceng {[carpark.silver_level_to_ceng(v) for v in levels]})",
          flush=True)
    return 0


def _do_search(client: WSGameClient) -> int:
    lots = carpark.read_cross_null_spaces(client)
    parkable = [lot for lot in lots if lot.null_num > 0]
    print(f"[carpark] search type=4: {len(lots)} cross lot(s), "
          f"{len(parkable)} parkable", flush=True)
    for lot in lots:
        tag = "PARKABLE" if lot.null_num > 0 else "full"
        print(f"    master_id={lot.master_id} ceng={lot.ceng} "
              f"null_num={lot.null_num} {tag}", flush=True)
    return 0


def _do_preview(client: WSGameClient) -> int:
    lots = carpark.read_cross_preview(client)
    print(f"[carpark] preview: {len(lots)} cross lot(s)", flush=True)
    for lot in lots:
        print(f"    id={lot.id} server_id={lot.server_id} "
              f"protect_end={lot.protect_end} def={lot.def_num} atk={lot.atk_num}",
              flush=True)
    return 0


def _do_info(client: WSGameClient, id_: int) -> int:
    d = carpark.read_cross_info(client, id_=id_)
    print(f"[carpark] cross lot id={d.id} server_id={d.server_id} "
          f"slots={len(d.spaces)} free={d.free_positions()} raw={d.raw}", flush=True)
    for s in d.spaces:
        state = f"role_id={s.role_id}" if s.occupied else "EMPTY"
        print(f"    pos={s.pos} {state}", flush=True)
    return 0


def _do_auto_park(client: WSGameClient, *, new: bool) -> int:
    result = carpark.auto_select_and_park(client, new=new)
    print(f"[carpark] auto_select parked={result['parked']} "
          f"reason={result['reason']} target_id={result['target_id']} "
          f"pos={result['pos']} mount_id={result['mount_id']} "
          f"attempts={result['attempts']} lots={result['lots']}", flush=True)
    r = result["result"]
    if r is not None:
        tag = "SUCCESS" if r.success else f"ERR code={r.error_code}"
        print(f"[carpark] -> cmd=0x{r.response_cmd:04x} {tag} fields={r.fields}",
              flush=True)
    return 0 if result["parked"] or result["reason"] in ("no_free_slot",) else 1


def _print_lot(lot: carpark.CarParkLot) -> None:
    tag = "CROSS" if lot.is_cross else f"type={lot.type}"
    print(f"[carpark] lot master_id={lot.master_id} {tag} ceng={lot.ceng} "
          f"slots={len(lot.spaces)} free={lot.free_positions()}", flush=True)
    for s in lot.spaces:
        state = f"role_id={s.role_id}" if s.occupied else "EMPTY"
        print(f"    pos={s.pos} {state}", flush=True)


def _do_dry(client: WSGameClient, target_id: int) -> int:
    lot = carpark.read_lot(client, type=carpark.CROSS_TYPE, master_id=target_id)
    _print_lot(lot)
    if not lot.is_cross:
        print(f"[carpark] WARNING: lot type={lot.type} is not cross (expected 3)",
              flush=True)
    mounts = carpark.read_my_mounts(client)
    print(f"[carpark] available mounts: {[m.mount_id for m in mounts]}", flush=True)
    print("[carpark] (dry run) pass --park to actually park.", flush=True)
    return 0


def _do_park(client: WSGameClient, park_arg: str, *, new: bool) -> int:
    target_id, pos = _parse_park_arg(park_arg)
    lot = carpark.read_lot(client, type=carpark.CROSS_TYPE, master_id=target_id)
    _print_lot(lot)
    if not lot.is_cross:
        print(f"[carpark] ABORT: lot type={lot.type} is not cross (expected 3)",
              flush=True)
        return 1

    if pos is None:
        result = carpark.auto_park_cross(client, target_id=target_id, new=new)
        print(f"[carpark] auto_park parked={result['parked']} reason={result['reason']} "
              f"pos={result['pos']} mount_id={result['mount_id']}", flush=True)
        r = result["result"]
    else:
        mounts = carpark.read_my_mounts(client)
        if not mounts:
            print("[carpark] ABORT: no available mount", flush=True)
            return 1
        mount_id = mounts[0].mount_id
        r = carpark.park_into_cross(client, target_id=target_id, pos=pos,
                                    mount_id=mount_id, new=new)
        print(f"[carpark] park pos={pos} mount_id={mount_id} success={r.success}",
              flush=True)

    if r is not None:
        tag = "SUCCESS" if r.success else f"ERR code={r.error_code}"
        print(f"[carpark] -> cmd=0x{r.response_cmd:04x} {tag} fields={r.fields} "
              f"raw={r.response_body.hex()}", flush=True)
        return 0 if r.success else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

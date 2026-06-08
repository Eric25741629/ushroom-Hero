"""Single-device daily-task orchestrator for the ws_token backend.

``run_device(device, *, spend=False)`` builds ONE :class:`WSGameClient` from the
device's captured creds, connects once (so a single background heartbeat keeps
the session alive), runs every pure-WS daily task in a fixed order, and closes
the client in a ``finally``. Each task is wrapped in its own try/except (incl.
:class:`WSTimeoutError`) so a dormant / event-gated / failing task never aborts
the others — every per-task result or error is collected into the frozen
:class:`RunReport`.

Task order (matches the in-game daily flow's free-then-paid grouping):

  1. main_tasks  — free: collect login-push state, then claim daily tasks +
                   daily activity box + weekly box + achievement milestones.
  2. league_solo — free: claim every claimable 烈焰山洞 / 魔法劇場 box (types 1-4).
  3. guild       — help_all (free); donate_until_capped (spend); treasure open
                   only when a round is active (event-gated; spend).
  4. steward     — read_info (free); shopping + dungeon sweep (spend); service
                   renewal only when spend AND the service is expired.

mining is deliberately NOT in the daily runner: it is human-supervised and runs
via ws_token.mining_smoke instead.

Default ``spend=False`` runs only the free reads + claims and sends NO cost
action. ``spend=True`` additionally donates, shops, sweeps, and (if expired)
renews — see the per-task spend gates below.

CLI:  python -m ws_token.runner --device <dev> [--spend]
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from ws_token import guild, league_solo, main_tasks, steward
from ws_token.client import WSGameClient, WSError, WSLoginError
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)

# Seconds to wait after connect for the login-time PUSH frames (task_all /
# daily_point / weekly_box) to drain before snapshotting the main-task state.
_PUSH_SETTLE_S: float = 1.5

LOGIN_TASK = "login"
TASK_ORDER: tuple[str, ...] = ("main_tasks", "league_solo", "guild", "steward")


@dataclass(frozen=True)
class RunReport:
    """Outcome of one ``run_device`` pass.

    ``tasks`` maps each run task name to its result summary (whatever the task's
    orchestrator returned, or a small dict assembled here). ``errors`` maps a
    task name (or ``"login"``) to a short error string for whatever failed; a
    successful run has an empty ``errors``.
    """

    device: str
    login_ok: bool
    spend: bool
    tasks: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def _make_client(creds, **kwargs) -> WSGameClient:
    """Construct the WSGameClient. Indirected so tests can inject a fake."""
    return WSGameClient(creds, **kwargs)


# --- per-task runners (each returns a summary; raising is caught by run_device)

def _run_main_tasks(client, collector: main_tasks.TaskCollector) -> dict:
    """Free: snapshot login-push state, then claim every free reward."""
    state = main_tasks.collect_state(client, collector, settle=_PUSH_SETTLE_S)
    daily = main_tasks.claim_daily_tasks(client, state)
    daily_box = main_tasks.claim_daily_box(client, state)
    weekly_box = main_tasks.claim_weekly_box(client, state)
    achievement = main_tasks.claim_achievement(client)
    return {
        "daily_tasks": daily,
        "daily_box": daily_box,
        "weekly_box": weekly_box,
        "achievement": achievement,
    }


def _run_league_solo(client) -> dict:
    """Free: claim every claimable 烈焰山洞 / 魔法劇場 box (types 1-4)."""
    return league_solo.claim_available(client)


def _run_guild(client, *, spend: bool) -> dict:
    """help_all (free); donate (spend); treasure open only with an active round."""
    summary: dict = {"help": None, "donate": None, "treasure": None}
    summary["help"] = guild.help_all(client)
    if spend:
        summary["donate"] = guild.donate_until_capped(client)
    # Treasure is event-gated: only open when a round is active (round != 0 and
    # there are boxes). A dormant event simply reports round=0 -> skip silently.
    info = guild.list_treasure(client)
    if spend and getattr(info, "round", 0) and getattr(info, "box_list", None):
        summary["treasure"] = guild.open_all_treasure(client)
    return summary


def _run_steward(client, *, spend: bool, serv_time: int,
                 sweep_list: Sequence[Sequence[int]]) -> dict:
    """read_info (free); shopping + dungeon sweep + renew only when spend.

    The dungeon sweep needs a caller-supplied chapter list (``sweep_list`` —
    steward does NOT auto-derive level/times); with no chapters configured the
    sweep is skipped even on spend, matching the live wiring.
    """
    summary: dict = {
        "info": None, "shopping": None, "sweep": None,
        "shopping_active": False, "dungeon_active": False,
    }
    summary["info"] = steward.read_info(client)
    if not spend:
        return summary

    # renew=True spends 家園幣 only when the selected service is expired.
    shopping_active = steward.ensure_active(
        client, steward.SERVICE_SHOPPING, serv_time=serv_time, renew=True)
    summary["shopping_active"] = shopping_active
    if shopping_active:
        summary["shopping"] = steward.run_shopping(client)

    if sweep_list:
        dungeon_active = steward.ensure_active(
            client, steward.SERVICE_DUNGEON, serv_time=serv_time, renew=True)
        summary["dungeon_active"] = dungeon_active
        if dungeon_active:
            summary["sweep"] = steward.run_dungeon_sweep(client, sweep_list)
    return summary


def run_device(device: str, *, spend: bool = False,
               sweep_list: Optional[Iterable[Sequence[int]]] = None) -> RunReport:
    """Run every ws_token daily task for ``device`` over one logged-in client.

    Builds a single WSGameClient (with a TaskCollector mounted as push_handler
    so the login-time main-task frames are captured), connects once, runs each
    task with its own error boundary, then closes the client. Returns a
    :class:`RunReport` summarising per-task results and errors. ``spend=False``
    (default) sends no cost action.

    ``sweep_list`` (only used when ``spend``) is the 副本管家 chapter list
    ``[(id, level, times[, use_ad]), ...]``; with none configured the sweep is
    skipped (steward does not auto-derive level/times).
    """
    tasks: dict[str, Any] = {}
    errors: dict[str, str] = {}
    sweep: tuple[Sequence[int], ...] = tuple(sweep_list or ())

    creds = load_creds(device)
    # The collector must be mounted BEFORE connect so the login-time task PUSH
    # frames (task_all / daily_point / weekly_box) land in it.
    collector = main_tasks.TaskCollector()
    client = _make_client(creds, push_handler=collector)

    try:
        login = client.connect()
    except (WSLoginError, WSError, OSError) as exc:
        logger.error("ws_token runner: %s login failed: %s", device, exc)
        try:
            client.close()
        except Exception:  # noqa: BLE001 — close must never mask the login error
            logger.debug("ws_token runner: %s close after login failure raised", device,
                         exc_info=True)
        return RunReport(device=device, login_ok=False, spend=spend,
                         tasks=tasks, errors={LOGIN_TASK: str(exc)})

    serv_time = int(login.get("serv_time") or creds.login_time or 0)
    logger.info("ws_token runner: %s login ok role_id=%s spend=%s",
                device, login.get("role_id"), spend)

    try:
        _safe(tasks, errors, "main_tasks", lambda: _run_main_tasks(client, collector))
        _safe(tasks, errors, "league_solo", lambda: _run_league_solo(client))
        _safe(tasks, errors, "guild", lambda: _run_guild(client, spend=spend))
        _safe(tasks, errors, "steward",
              lambda: _run_steward(client, spend=spend, serv_time=serv_time,
                                   sweep_list=sweep))
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("ws_token runner: %s close raised", device, exc_info=True)

    logger.info("ws_token runner: %s done — %d task(s) ok, %d error(s)",
                device, len(tasks), len(errors))
    return RunReport(device=device, login_ok=True, spend=spend,
                     tasks=tasks, errors=errors)


def _safe(tasks: dict, errors: dict, name: str, fn) -> None:
    """Run one task with its own error boundary; record result OR error.

    Any exception (WSTimeoutError, parse errors, etc.) is caught so the next
    task still runs. The error is summarised onto ``errors[name]``.
    """
    try:
        tasks[name] = fn()
    except Exception as exc:  # noqa: BLE001 — per-task isolation is the whole point
        errors[name] = f"{type(exc).__name__}: {exc}"
        logger.warning("ws_token runner: task %s failed: %s", name, exc, exc_info=True)


# --- CLI --------------------------------------------------------------------

def _format_report(rep: RunReport) -> str:
    lines = [
        f"[runner] device={rep.device} login_ok={rep.login_ok} spend={rep.spend}",
        f"[runner] tasks_ok={list(rep.tasks)} errors={list(rep.errors)}",
    ]
    for name, summary in rep.tasks.items():
        lines.append(f"  {name}: {summary}")
    for name, err in rep.errors.items():
        lines.append(f"  ERROR {name}: {err}")
    return "\n".join(lines)


def _parse_sweep_arg(items: list[str]) -> list[tuple[int, ...]]:
    """Parse --sweep id:level:times[:use_ad] tokens (same as steward_smoke)."""
    out: list[tuple[int, ...]] = []
    for tok in items:
        parts = [int(p) for p in tok.split(":")]
        if len(parts) < 3:
            raise SystemExit(f"--sweep entry {tok!r} needs id:level:times[:use_ad]")
        out.append(tuple(parts[:4]))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--spend", action="store_true",
                    help="also send cost actions (guild donate / steward shopping "
                         "+ sweep / service renew). Default: free reads/claims only.")
    ap.add_argument("--sweep", action="append", default=[],
                    metavar="id:level:times[:use_ad]",
                    help="副本管家 sweep chapter (repeatable; only used with --spend)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    sweep_list = _parse_sweep_arg(args.sweep) or None
    print(f"[runner] starting device={args.device} spend={args.spend}", flush=True)
    rep = run_device(args.device, spend=args.spend, sweep_list=sweep_list)
    print(_format_report(rep), flush=True)
    return 0 if rep.login_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

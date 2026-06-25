"""Persistent online-status monitor — one live WS connection, periodic refresh.

Keeps the preferred *bot* device (default: emulator-5554) connected via WS with
heartbeat. Every ``poll_sec`` seconds reads the full friend list in ONE call and
snapshots everyone's online state. On disconnect, auto-failovers to an offline
device from the last snapshot (zero interruption for online players).

Never connects as a human-played account (config `human_played`, resolved to its
roleId): logging in there would kick the human (異地登入). A 5-min cooldown
(``switch_cooldown_sec``) rate-limits detector switches / reconnects so a kicked
session never triggers an immediate re-login loop that fights the live player.

Usage:
  python -m ws_token.online_monitor                        # default emulator-5554
  python -m ws_token.online_monitor --device emulator-5556 # override detector
  python -m ws_token.online_monitor --interval 15          # faster poll
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ws_token import online_guard
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)
AUTH_DIR = Path(__file__).resolve().parents[1] / "auth_state"

# bot_state task strings that mean "this device's bot is sleeping" → its game
# session is closed, so logging in as its account won't kick a live session.
_IDLE_TASKS = ("休眠中", "啟動後休眠")

# Hand the detector off this many seconds BEFORE its own bot is scheduled to
# wake, so the bot's start never races the borrowed monitor session.
_HANDOFF_LEAD_SEC = 120.0


@dataclass(frozen=True)
class StatusEntry:
    role_id: int
    name: str
    online: bool
    last_login_ts: Optional[int]


@dataclass(frozen=True)
class Snapshot:
    detector: str
    timestamp: float
    entries: tuple[StatusEntry, ...] = ()


def discover_role_map(auth_dir: Path = AUTH_DIR,
                      protected_role_ids: frozenset = frozenset()) -> Dict[int, str]:
    """Read all creds files + bot_config online_check_target_pid -> {role_id: device}.

    ``protected_role_ids`` (human-played accounts) are excluded so the monitor
    never selects them as a detector to connect as.
    """
    mapping: Dict[int, str] = {}
    for f in sorted(auth_dir.glob("_auth_capture_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            rid = int(data.get("creds", {}).get("roleId", 0))
            dev = f.stem.replace("_auth_capture_", "")
            if rid and rid not in mapping:
                mapping[rid] = dev
        except Exception:
            continue
    # devices without creds but with online_check_target_pid in bot_config
    try:
        import config_manager
        for dev, cfg in (config_manager.load_config() or {}).get("devices", {}).items():
            pid = cfg.get("online_check_target_pid")
            if pid and int(pid) not in mapping:
                mapping[int(pid)] = dev
    except Exception:
        pass
    return {rid: dev for rid, dev in mapping.items() if rid not in protected_role_ids}


def poll_friends(client: WSGameClient, threshold_sec: int = 120) -> List[StatusEntry]:
    """One friend-list WS call -> all friends' online status."""
    body = client.call(online_guard.CMD_FRIEND_LIST,
                       online_guard.build_friend_list_body(), timeout=10)
    now = int(time.time())
    out: List[StatusEntry] = []
    for f in online_guard.parse_friend_list(body):
        ts = f.last_login_ts
        if ts is None:
            online = False
        elif ts == 0:
            online = True
        else:
            online = (now - ts) < threshold_sec
        out.append(StatusEntry(f.player_id, f.name, online, ts))
    return out


class OnlineMonitor:
    """Background thread that maintains a live Snapshot."""

    def __init__(self, preferred: str = "emulator-5554",
                 poll_sec: float = 30.0, threshold_sec: int = 120,
                 protected_role_ids: frozenset = frozenset(),
                 switch_cooldown_sec: float = 300.0,
                 force_refresh_sec: float = 300.0,
                 force_exclude=("emulator-5558",),
                 now: "Callable[[], float]" = time.time):
        self._preferred = preferred
        self._poll_sec = poll_sec
        self._threshold_sec = threshold_sec
        self._protected_rids = frozenset(protected_role_ids or ())
        self._switch_cooldown = float(switch_cooldown_sec)
        self._now = now
        # stale-escape: snapshot 過期超過此秒數，且驗不出任何離線候選時，強制盲選
        # 一台 idle bot 直接登入刷新（可能踢到剛好在玩該 bot 帳號的真人）。
        # ``force_exclude`` 的裝置永不被強制選中（5558 = 最受保護的真人主帳）。
        self._force_refresh_sec = float(force_refresh_sec)
        self._force_exclude = frozenset(force_exclude or ())
        self._last_pick_forced = False  # 本次選擇是否為強制盲選（_loop 用來繞過節流）
        self._last_switch_ts = 0.0  # epoch of last detector connect/switch
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._snapshot: Optional[Snapshot] = None
        self._role_map: Dict[int, str] = {}
        self._wake = threading.Event()
        self._active_detector: Optional[str] = None  # currently-connected detector
        self._last_switch_event: Optional[dict] = None  # {frm, to, ts} for display

    def _set_active(self, detector: Optional[str]) -> None:
        """Record the active detector; log + remember the transition if it changed.

        ``detector`` is the device now connected (or ``None`` when disconnected).
        Drives the dashboard's switch trace ("上次切換: X→Y"). No-op when unchanged.
        """
        with self._lock:
            prev = self._active_detector
            if prev == detector:
                return
            self._active_detector = detector
            self._last_switch_event = {"frm": prev, "to": detector,
                                       "ts": self._now()}
        logger.info("online-monitor: detector switch %s -> %s", prev, detector)

    @property
    def last_switch(self) -> Optional[dict]:
        """Latest detector transition {frm, to, ts}, or None if never switched."""
        with self._lock:
            return dict(self._last_switch_event) if self._last_switch_event else None

    def _switch_allowed(self) -> bool:
        """True iff the cooldown since the last detector switch/connect elapsed.

        Rate-limits how often the monitor changes/reopens its WS detector so a
        disconnect (e.g. the human took the account) never triggers an immediate
        reconnect loop that fights the live session.
        """
        return (self._now() - self._last_switch_ts) >= self._switch_cooldown

    @property
    def snapshot(self) -> Optional[Snapshot]:
        with self._lock:
            return self._snapshot

    @property
    def ready(self) -> bool:
        """True once at least one snapshot has been collected."""
        with self._lock:
            return self._snapshot is not None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._role_map = discover_role_map(protected_role_ids=self._protected_rids)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="online-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    def poll_now(self) -> None:
        """Signal the monitor to poll immediately instead of waiting for the next cycle."""
        self._wake.set()

    def _is_safe_detector(self, dev: str, states: Dict[str, dict]) -> bool:
        """True iff logging in as ``dev`` won't kick a live session.

        Safe = the device's bot is sleeping (game session closed) or OFFLINE. A
        device with no running thread is unsafe — we can't confirm its account is
        free, so we never risk it as the detector. Human accounts are already
        absent from role_map.
        """
        st = states.get(dev)
        if not st:
            return False
        if str(st.get("status") or "").upper() == "OFFLINE":
            return True
        return str(st.get("task") or "") in _IDLE_TASKS

    def _has_creds(self, dev: str) -> bool:
        """True iff we can load WS creds for ``dev`` (so the monitor can log in
        AS it). Excludes creds-less devices like emulator-5558 — its account is
        the most-protected human main and must never be a detector.
        """
        try:
            load_creds(dev)
            return True
        except Exception:
            return False

    def _about_to_wake(self, dev: str, states: Dict[str, dict]) -> bool:
        """True iff ``dev``'s bot is scheduled to start within
        ``_HANDOFF_LEAD_SEC``. We hand the detector off before that so the bot's
        own login doesn't race the borrowed monitor session.
        """
        nwa = (states.get(dev) or {}).get("next_wake_at")
        if not nwa:
            return False
        try:
            return (float(nwa) - self._now()) <= _HANDOFF_LEAD_SEC
        except (TypeError, ValueError):
            return False

    def _snapshot_offline(self, dev: str,
                          snapshot: Optional[Snapshot]) -> Optional[bool]:
        """Is ``dev``'s account OFFLINE per ``snapshot``? ``True``/``False``, or
        ``None`` when unknown (no snapshot, unresolved roleId, or not in the
        detector's friend list). Used to verify a candidate is human-free BEFORE
        logging in as it, so the monitor's own login never kicks a live player.
        """
        if snapshot is None:
            return None
        try:
            import config_manager
            rid = config_manager.get_device_role_id(dev)
        except Exception:
            rid = None
        if rid is None:
            return None
        for e in snapshot.entries:
            if int(e.role_id) == int(rid):
                return not e.online
        return None

    def _eligible_pool(self, states: Dict[str, dict]) -> List[str]:
        """Detector candidates: have creds, bot-idle/offline, not about to wake.

        Deduped, role_map order preserved. Snapshot-offline verification is
        applied later (only when picking a NEW detector), not here, so a sticky
        current detector — which is online via the monitor itself — still
        qualifies.
        """
        pool: List[str] = []
        for dev in dict.fromkeys(self._role_map.values()):
            if (self._has_creds(dev)
                    and self._is_safe_detector(dev, states)
                    and not self._about_to_wake(dev, states)):
                pool.append(dev)
        return pool

    def _stale_for(self, snapshot: Optional[Snapshot]) -> float:
        """Seconds since the last successful refresh (0 when no snapshot yet)."""
        if snapshot is None:
            return 0.0
        return self._now() - float(snapshot.timestamp)

    def _force_pick(self, pool: List[str]) -> Optional[str]:
        """Random idle-bot candidate for a stale-escape blind connect, with the
        excluded devices (5558) removed. ``None`` when nothing is left to try."""
        candidates = [d for d in pool if d not in self._force_exclude]
        if not candidates:
            return None
        return random.choice(candidates)  # ponytail: stdlib random pick per user spec

    def _select_detector(self, current: Optional[str] = None,
                         snapshot: Optional[Snapshot] = None) -> Optional[str]:
        """Pick the device to read presence from, applying the sticky policy.

        - **Sticky**: keep ``current`` while it stays eligible (idle + has creds
          + not about to wake); never bounce back to ``preferred`` just because
          it freed up.
        - **Reselect** (current gone / busy / about-to-wake) chooses only among
          accounts the latest ``snapshot`` confirms OFFLINE — so logging in as
          the new detector never kicks a human. Prefers ``preferred`` (5554)
          when it is eligible-and-offline, else any.
        - **Cold start** (``snapshot is None``): nothing to verify against, so
          connect blind to a bot-idle candidate — the one unavoidable risk
          window the user accepted (best-effort).
        - **Stale escape** (snapshot older than ``force_refresh_sec`` and nothing
          verified-offline): the verification is exactly what's keeping us stuck,
          so blind-connect to a RANDOM idle bot (``force_exclude`` removed),
          accepting it may kick a human on that bot's account.
        - Returns ``None`` when nothing is safe to connect → stay disconnected,
          the one-shot WS fallback covers checks meanwhile.
        """
        try:
            import bot_state as _bs
            states = _bs.get_all_states()
        except Exception:
            states = {}
        pool = self._eligible_pool(states)
        if current is not None and current in pool:
            self._last_pick_forced = False
            return current  # sticky
        if snapshot is not None:
            safe = [d for d in pool if self._snapshot_offline(d, snapshot) is True]
        else:
            safe = pool  # cold-start blind
        if safe:
            self._last_pick_forced = False
            return self._preferred if self._preferred in safe else safe[0]
        # Nothing the snapshot confirms human-free. Once it's been stale past the
        # force threshold, verification can't recover (it's why we're stuck) →
        # blind-pick a random idle bot (5558 excluded). May kick a human.
        if self._stale_for(snapshot) > self._force_refresh_sec:
            forced = self._force_pick(pool)
            if forced is not None:
                self._last_pick_forced = True
                return forced
        self._last_pick_forced = False
        return None

    def _connect(self, device: str) -> Optional[WSGameClient]:
        try:
            creds = load_creds(device)
            if int(getattr(creds, "role_id", 0)) in self._protected_rids:
                logger.warning("online-monitor: refusing to connect as protected "
                               "(human-played) account via %s", device)
                return None
            client = WSGameClient(creds, heartbeat_enabled=True)
            client.connect()
            logger.info("online-monitor: connected as %s", device)
            return client
        except Exception as exc:
            logger.warning("online-monitor: connect %s failed: %s", device, exc)
            return None

    @staticmethod
    def _close(client: WSGameClient) -> None:
        try:
            client.close()
        except Exception:
            pass

    def _sleep(self) -> None:
        self._wake.wait(timeout=self._poll_sec)
        self._wake.clear()

    def _loop(self) -> None:
        client: Optional[WSGameClient] = None
        current: Optional[str] = None
        gate_reconnect = False  # throttle only reopen-after-failure (anti-storm)

        while self._running:
            desired = self._select_detector(current, self.snapshot)

            # No idle bot to safely log in as → drop any connection rather than
            # squat on a busy account and kick its running bot. Go stale; the
            # one-shot WS fallback covers online-checks meanwhile.
            if desired is None:
                if client is not None:
                    logger.info("online-monitor: no idle detector; disconnecting %s",
                                current)
                    self._close(client)
                    client, current = None, None
                self._set_active(None)
                self._sleep()
                continue

            # Current detector went busy (or a better one is preferred) → hand
            # off immediately. Switching AWAY from a busy account is never
            # throttled (it stops a conflict); only reopening after a failure is.
            if client is not None and current != desired:
                logger.info("online-monitor: handing off detector %s -> %s",
                            current, desired)
                self._close(client)
                client, current = None, None
                gate_reconnect = False

            if client is None:
                # A forced (stale-escape) pick bypasses the anti-storm throttle:
                # the user wants it to retry a fresh random bot every cycle until
                # the snapshot refreshes.
                if (gate_reconnect and not self._switch_allowed()
                        and not self._last_pick_forced):
                    self._sleep()
                    continue
                if self._last_pick_forced:
                    logger.warning(
                        "online-monitor: snapshot stale >%.0fs — FORCE blind-connect "
                        "as %s (may kick a human; excluded=%s)",
                        self._force_refresh_sec, desired, sorted(self._force_exclude))
                client = self._connect(desired)
                self._last_switch_ts = self._now()
                if client is None:
                    current = None
                    gate_reconnect = True  # connect failed → throttle the retry
                    self._set_active(None)
                    self._sleep()
                    continue
                current = desired
                gate_reconnect = False
                self._set_active(desired)  # records the clean prev->desired switch

            try:
                entries = poll_friends(client, self._threshold_sec)
                with self._lock:
                    self._snapshot = Snapshot(current, self._now(), tuple(entries))
            except Exception as exc:
                logger.warning("online-monitor: poll failed (%s): %s", current, exc)
                self._close(client)
                client, current = None, None
                gate_reconnect = True  # dropped/kicked → throttle reconnect
                self._set_active(None)
                continue

            self._sleep()

        if client is not None:
            self._close(client)


# --- module-level singleton for dashboard integration -------------------------
_monitor: Optional[OnlineMonitor] = None
_log_handler_attached = False


def _setup_monitor_log() -> None:
    """Route this detector's logger to a dedicated, isolated file
    (``logs/system/online_monitor.log``) so the presence machinery is debuggable
    on its own. ``ws_token.online_monitor`` otherwise reaches no per-device
    main.log, which is why detector switches / connect / poll-fail were invisible.
    Idempotent; never blocks startup.
    """
    global _log_handler_attached
    if _log_handler_attached:
        return
    try:
        from logging.handlers import RotatingFileHandler
        from utils.log_paths import LogPaths
        path = LogPaths.system_log("online_monitor")
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(str(path), maxBytes=2_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        _log_handler_attached = True
        logger.info("online-monitor: dedicated log -> %s", path)
    except Exception:  # noqa: BLE001 — logging setup must never block startup
        logger.debug("online-monitor: dedicated log setup failed", exc_info=True)


def get_snapshot() -> Optional[Snapshot]:
    """Read the latest snapshot (None if monitor not running)."""
    return _monitor.snapshot if _monitor else None


def current_detector() -> Optional[str]:
    """Device serial the monitor is currently connected AS, or None.

    A start-gate uses this for a fast pass: if a device is itself the active
    detector, the monitor holds that account's live WS session, so no human can
    be on it (a human login would have kicked the monitor) → the device is safe
    to start immediately. The device's own login then kicks the monitor, which
    fails over to another idle account.
    """
    return _monitor._active_detector if _monitor else None


def account_online(role_id: int, *, max_age_sec: float = 60.0,
                   now: Optional[float] = None) -> Optional[bool]:
    """Presence of ``role_id`` from the live snapshot: ``True``/``False``, or
    ``None`` when unknown — monitor not running, no snapshot yet, snapshot older
    than ``max_age_sec``, or the account is not in the current detector's friend
    list. Callers that must never act on a stale/blind read treat ``None`` as
    "maybe online" (see ws_phase human-played gate). ``now`` injectable for tests.
    """
    snap = get_snapshot()
    if snap is None:
        return None
    t = time.time() if now is None else now
    if (t - float(snap.timestamp)) > max_age_sec:
        return None
    for e in snap.entries:
        if int(e.role_id) == int(role_id):
            return bool(e.online)
    return None


def get_last_switch() -> Optional[dict]:
    """Latest detector transition {frm, to, ts} (None if monitor not running)."""
    return _monitor.last_switch if _monitor else None


def get_poll_sec() -> Optional[float]:
    """The monitor's refresh interval (seconds), or None if not running."""
    return _monitor._poll_sec if _monitor else None


def resolve_protected_role_ids() -> frozenset:
    """roleIds of human-played devices (config `human_played`) → never connect as.

    Resolved via each flagged device's creds so the protection survives the
    device-name aliasing (e.g. fc65396d_u999 / adb-fc65396d-…_tcp share one
    roleId). Best-effort: a flagged device without creds is skipped.
    """
    rids = set()
    try:
        import config_manager
        for dev in config_manager.get_human_played_devices():
            try:
                rids.add(int(load_creds(dev).role_id))
            except Exception:
                continue
    except Exception:
        pass
    return frozenset(rids)


def ensure_started(preferred: str = "emulator-5554", poll_sec: float = 30.0,
                   wait_ready: float = 15.0) -> OnlineMonitor:
    """Start the singleton monitor and block until the first snapshot is ready.

    ``wait_ready`` caps how long to wait (seconds). If the monitor can't connect
    in time (no creds, server down), returns anyway — callers fall through to the
    one-shot WS path. 0 = don't wait.
    """
    global _monitor
    _setup_monitor_log()
    if _monitor is None:
        _monitor = OnlineMonitor(preferred=preferred, poll_sec=poll_sec,
                                 protected_role_ids=resolve_protected_role_ids())
    _monitor.start()
    if wait_ready > 0 and not _monitor.ready:
        deadline = time.time() + wait_ready
        while time.time() < deadline and not _monitor.ready:
            time.sleep(0.3)
        if _monitor.ready:
            logger.info("online-monitor: first snapshot ready")
        else:
            logger.warning("online-monitor: timed out waiting for first snapshot "
                           "(%.1fs); one-shot fallback will be used", wait_ready)
    return _monitor


# --- CLI ----------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description="Persistent online-status monitor")
    ap.add_argument("--device", default="emulator-5554", help="preferred detector")
    ap.add_argument("--interval", type=float, default=30.0, help="poll seconds")
    ap.add_argument("--threshold", type=int, default=120, help="online threshold sec")
    args = ap.parse_args()

    protected = resolve_protected_role_ids()
    role_map = discover_role_map(protected_role_ids=protected)
    mon = OnlineMonitor(args.device, args.interval, args.threshold,
                        protected_role_ids=protected)
    print(f"Monitor: detector={args.device}, interval={args.interval}s")
    print(f"Known: { {v: k for k, v in role_map.items()} }")
    print("Ctrl+C to stop.\n")
    mon.start()

    try:
        while True:
            time.sleep(3)
            snap = mon.snapshot
            if snap is None:
                continue
            ts = time.strftime("%H:%M:%S", time.localtime(snap.timestamp))
            # only print our devices + filter by role_map
            print(f"\n[{ts}] detector={snap.detector}")
            for e in snap.entries:
                dev = role_map.get(e.role_id)
                tag = "ONLINE" if e.online else "offline"
                label = f"({dev})" if dev else ""
                print(f"  {e.name:<20s} {label:<32s} {tag}")
    except KeyboardInterrupt:
        print("\nStopping...")
        mon.stop()

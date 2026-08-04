"""Day/night carpark window + re-park/grab wake timing (pure; ``now`` injected).

Config shape (device ``ws_token.carpark_plan``):

    {"enabled": true,
     "silver_levels": [9, 10],
     "day":   {"window": ["10:00", "22:00"], "cross": 1},
     "night": {"window": ["22:00", "10:00"], "cross": 0},
     "park_max_seconds": 28800,      # game auto-collects a cross car after this
     "open_lead_seconds": 60,        # wake this early to grab the 10:00 open
     "repark_margin_seconds": 30,    # wake this late after expiry (car freed)
     "start_time_offset": 0}         # epoch base correction for server start_time

Model (2026-06-13 rewrite — **current-parked**, not a per-day quota):
  - Only 跨界 (cross) parking is planned; the 5 本服/好友 cars are the game's
    built-in automation. The cross car is silver-tier-only (see carpark.py).
  - Each wake the runner reads how many cross cars are CURRENTLY parked (12802
    parking_data) and parks up to ``win.cross`` minus that. So when the game
    auto-collects a car at ``park_max_seconds`` (≈8h), the live count drops and
    the next wake re-parks — no per-day state needed, self-correcting.
  - To re-park the instant a car expires (and to be logged-in and ready for the
    10:00 cross-open rush), the runner stores a single ``next wake`` epoch that
    the sleep scheduler clamps the wake EARLIER to. ``carpark_wake_ts`` computes
    it as the min of:
      (a) earliest parked car's expiry (start_time + offset + max + margin),
          but only while a cross window is open AND the expiry lands inside it;
      (b) the next cross-window open minus ``open_lead`` (the 09:59 grab).
  - A window may wrap midnight (night 22:00->10:00).

The wake epoch lives under ``ws_state/<device>.json`` key ``carpark_repark``
(written by the runner): ``{"next_ts": 1718000000.0}``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)

WINDOW_NAMES = ("day", "night")

DEFAULT_PARK_MAX_SECONDS = 28800        # 8h — the game's cross auto-collect
DEFAULT_OPEN_LEAD_SECONDS = 60          # wake 60s before open to win the rush
DEFAULT_REPARK_MARGIN_SECONDS = 30      # wake 30s after expiry (car is freed)
DEFAULT_GRAB_ATTEMPTS = 8               # safety upper bound on grab retry rounds
DEFAULT_GRAB_POLL_SECONDS = 1.0         # spacing between grab retries (every ~1s)
DEFAULT_GRAB_WINDOW_SECONDS = 60        # keep retrying until open + this (10:01)
DEFAULT_CLUSTER_MIN = 5                 # 停入前已有同服 >= 5 才算抱團（不含自己）
DEFAULT_ALLOW_LOW_NONCLUSTER = False    # 嚴格抱團：找不到合格車位就不下車


@dataclass(frozen=True)
class WindowPlan:
    name: str       # "day" | "night"
    start: time
    end: time       # end <= start means the window wraps midnight
    cross: int

    @property
    def wraps(self) -> bool:
        return self.end <= self.start


def _parse_hhmm(s: object) -> time | None:
    if not isinstance(s, str) or ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def parse_plan(cfg: dict | None) -> list[WindowPlan]:
    """Build the window list from config; malformed windows are dropped."""
    out: list[WindowPlan] = []
    if not isinstance(cfg, dict):
        return out
    for name in WINDOW_NAMES:
        w = cfg.get(name)
        if not isinstance(w, dict):
            continue
        window = w.get("window")
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            logger.warning("carpark_plan: window %s malformed (%r), dropped",
                           name, window)
            continue
        start, end = _parse_hhmm(window[0]), _parse_hhmm(window[1])
        if start is None or end is None:
            logger.warning("carpark_plan: window %s bad HH:MM (%r), dropped",
                           name, window)
            continue
        out.append(WindowPlan(
            name=name, start=start, end=end,
            cross=max(0, _to_int(w.get("cross"))),
        ))
    return out


def _in_window(win: WindowPlan, now: datetime) -> bool:
    t = now.time()
    if win.wraps:
        return t >= win.start or t < win.end
    return win.start <= t < win.end


def active_window(plans: list[WindowPlan], now: datetime) -> WindowPlan | None:
    for win in plans:
        if _in_window(win, now):
            return win
    return None


# --- config getters ----------------------------------------------------------

def park_max_seconds(cfg: dict | None) -> int:
    return _pos_int((cfg or {}).get("park_max_seconds"), DEFAULT_PARK_MAX_SECONDS)


def open_lead_seconds(cfg: dict | None) -> int:
    return _nonneg_int((cfg or {}).get("open_lead_seconds"),
                       DEFAULT_OPEN_LEAD_SECONDS)


def repark_margin_seconds(cfg: dict | None) -> int:
    return _nonneg_int((cfg or {}).get("repark_margin_seconds"),
                       DEFAULT_REPARK_MARGIN_SECONDS)


def start_time_offset(cfg: dict | None) -> int:
    """Epoch base correction added to server ``start_time`` (may be negative).

    0 when ``start_time`` is plain unix epoch (the expected case; verify live).
    """
    return _to_int((cfg or {}).get("start_time_offset"))


def grab_attempts(cfg: dict | None) -> int:
    return _pos_int((cfg or {}).get("grab_attempts"), DEFAULT_GRAB_ATTEMPTS)


def grab_poll_seconds(cfg: dict | None) -> float:
    v = (cfg or {}).get("grab_poll_seconds")
    if v is None:
        return DEFAULT_GRAB_POLL_SECONDS
    try:
        f = float(v)
    except (TypeError, ValueError):
        return DEFAULT_GRAB_POLL_SECONDS
    return f if f > 0 else DEFAULT_GRAB_POLL_SECONDS


def grab_window_seconds(cfg: dict | None) -> int:
    """How long after the cross open to keep retrying the grab (open->10:01)."""
    return _pos_int((cfg or {}).get("grab_window_seconds"),
                    DEFAULT_GRAB_WINDOW_SECONDS)


def cluster_min(cfg: dict | None) -> int:
    """同服占用達此數才算抱團 (高獎勵低編號區 鉑銀1-8 的停車門檻)。"""
    return max(DEFAULT_CLUSTER_MIN,
               _pos_int((cfg or {}).get("cluster_min"), DEFAULT_CLUSTER_MIN))


def allow_low_noncluster(cfg: dict | None) -> bool:
    """絕對最後手段：preferred/抱團/中/高區皆無位時，仍停進鉑銀1-8 非抱團空位。"""
    v = (cfg or {}).get("allow_low_noncluster")
    if v is None:
        return DEFAULT_ALLOW_LOW_NONCLUSTER
    return bool(v)


# --- cluster scan (抱團掃描) ---------------------------------------------------

DEFAULT_CLUSTER_SCAN_EXCLUDED_LEVELS = (1, 2, 3)
DEFAULT_CLUSTER_SCAN_LEVELS = tuple(range(4, 11))  # 鉑銀4-10；1/2/3 永久排除
DEFAULT_CLUSTER_SCAN_DURATION = 300     # 5 minutes
# 官方 H5 跨界車位刷新按鈕的 isInCD timer 是 1 秒。
DEFAULT_CLUSTER_SCAN_INTERVAL = 1       # 每輪完成後最少等官方冷卻 1 秒
DEFAULT_CLUSTER_SCAN_MIN_ALLIES = 5     # 停入前已有 >= 5 位同服（不含自己）
DEFAULT_CLUSTER_SCAN_FALLBACK = 9       # park 鉑銀9 if no cluster found


@dataclass(frozen=True)
class ClusterScanConfig:
    """Per-device 抱團掃描: when parking in an open cross window, poll lots for
    same-server allies before committing.  Disabled by default; enable per device
    in carpark_plan.cluster_scan.

    ``priority_levels`` is a subset of ``levels`` scanned with preference — among
    lots meeting ``min_allies`` the priority-range ones are parked first (e.g.
    手機fc/小寶 prefer 鉑銀1-15, 模擬器 prefer 15-30).  Empty = no preference.
    """
    enabled: bool = False
    levels: tuple[int, ...] = DEFAULT_CLUSTER_SCAN_LEVELS
    priority_levels: tuple[int, ...] = ()
    excluded_levels: tuple[int, ...] = DEFAULT_CLUSTER_SCAN_EXCLUDED_LEVELS
    duration: int = DEFAULT_CLUSTER_SCAN_DURATION
    interval: int = DEFAULT_CLUSTER_SCAN_INTERVAL
    min_allies: int = DEFAULT_CLUSTER_SCAN_MIN_ALLIES
    fallback_level: int = DEFAULT_CLUSTER_SCAN_FALLBACK


def parse_cluster_scan(cfg: dict | None) -> ClusterScanConfig:
    cs = (cfg or {}).get("cluster_scan")
    if not isinstance(cs, dict) or not cs.get("enabled"):
        return ClusterScanConfig()
    raw_excluded = cs.get("excluded_levels")
    excluded = set(DEFAULT_CLUSTER_SCAN_EXCLUDED_LEVELS)
    if isinstance(raw_excluded, (list, tuple)):
        for value in raw_excluded:
            try:
                level = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= level <= 30:
                excluded.add(level)

    def _levels(value, default=()):
        source = value if isinstance(value, (list, tuple)) else default
        out = []
        for item in source:
            try:
                level = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= level <= 30 and level not in excluded and level not in out:
                out.append(level)
        return tuple(out)

    levels = cs.get("levels")
    if isinstance(levels, (list, tuple)):
        levels = _levels(levels)
    else:
        levels = _levels(DEFAULT_CLUSTER_SCAN_LEVELS)
    prio = _levels(cs.get("priority_levels"))
    return ClusterScanConfig(
        enabled=True,
        levels=levels,
        priority_levels=prio,
        excluded_levels=tuple(sorted(excluded)),
        duration=_pos_int(cs.get("duration"), DEFAULT_CLUSTER_SCAN_DURATION),
        interval=_pos_int(cs.get("interval"), DEFAULT_CLUSTER_SCAN_INTERVAL),
        min_allies=max(
            DEFAULT_CLUSTER_SCAN_MIN_ALLIES,
            _pos_int(cs.get("min_allies"), DEFAULT_CLUSTER_SCAN_MIN_ALLIES)),
        fallback_level=_pos_int(cs.get("fallback_level"), DEFAULT_CLUSTER_SCAN_FALLBACK),
    )


# --- open-window timing ------------------------------------------------------

def next_cross_open_dt(plans: list[WindowPlan], now: datetime) -> datetime | None:
    """The next datetime a cross (cross>0) window opens, strictly after ``now``
    when ``now`` is already at/after today's open (so a currently-open window
    resolves to tomorrow). None when no window allows cross parking."""
    best: datetime | None = None
    for win in plans:
        if win.cross <= 0:
            continue
        cand = now.replace(hour=win.start.hour, minute=win.start.minute,
                           second=0, microsecond=0)
        if cand <= now:
            cand = cand + timedelta(days=1)
        if best is None or cand < best:
            best = cand
    return best


def _cross_window_starting_at(plans: list[WindowPlan],
                              open_dt: datetime) -> WindowPlan | None:
    for win in plans:
        if win.cross > 0 and win.start == open_dt.time():
            return win
    return None


def cross_open_wait(plans: list[WindowPlan], now: datetime,
                    open_lead: int) -> tuple[float, datetime, WindowPlan] | None:
    """If ``now`` is within ``open_lead`` seconds before a cross window opens
    (and not already inside an open cross window), return
    ``(seconds_until_open, open_dt, win)`` so the runner can wait then grab.
    None otherwise (already open / too early / no cross window / lead disabled).
    """
    if open_lead <= 0:
        return None
    active = active_window(plans, now)
    if active is not None and active.cross > 0:
        return None
    open_dt = next_cross_open_dt(plans, now)
    if open_dt is None:
        return None
    secs = (open_dt - now).total_seconds()
    if 0 <= secs <= open_lead:
        win = _cross_window_starting_at(plans, open_dt)
        if win is not None:
            return (secs, open_dt, win)
    return None


def _window_end_dt(win: WindowPlan, now: datetime) -> datetime:
    """The end datetime of ``win`` for the open instance covering ``now``."""
    end = now.replace(hour=win.end.hour, minute=win.end.minute,
                      second=0, microsecond=0)
    if win.wraps and now.time() >= win.start:
        end = end + timedelta(days=1)
    return end


def carpark_wake_ts(parked_cross, plans: list[WindowPlan], now: datetime, *,
                    max_sec: int, open_lead: int, margin: int = 0,
                    offset: int = 0) -> float | None:
    """The epoch the scheduler should wake to for carpark, or None.

    min of: earliest parked-car expiry (only while a cross window is open and the
    expiry lands inside it) and the next cross open minus ``open_lead``.
    """
    now_ts = now.timestamp()
    cands: list[float] = []

    active = active_window(plans, now)
    if active is not None and active.cross > 0 and parked_cross:
        starts = [m.parking_info.start_time for m in parked_cross
                  if m.parking_info is not None]
        if starts:
            expiry = min(starts) + offset + max_sec + margin
            if expiry <= _window_end_dt(active, now).timestamp():
                cands.append(float(expiry))

    open_dt = next_cross_open_dt(plans, now)
    if open_dt is not None and open_lead > 0:
        cands.append(open_dt.timestamp() - open_lead)

    future = [c for c in cands if c > now_ts]
    return min(future) if future else None


def _to_int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _pos_int(v: object, default: int) -> int:
    n = _to_int(v) if v is not None else default
    return n if n > 0 else default


def _nonneg_int(v: object, default: int) -> int:
    if v is None:
        return default
    n = _to_int(v)
    return n if n >= 0 else default

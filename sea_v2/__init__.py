"""sea_v2: 航海每日任務重構 (H5-first deterministic navigation).

Not wired into the runtime yet — ``daily_pipeline`` still calls the legacy ``Sea.sea``.
Opt in later (after the 階段 B daytime action mapping is verified) via the
``sea_v2_enabled`` flag, gated by :func:`use_sea_v2`.

Design: ``docs/protocol/SEA_DAILY.md``.
"""
from __future__ import annotations

import logging

from sea_v2.tasks import SeaReport, run_daily

logger = logging.getLogger(__name__)


def use_sea_v2(ip: str, config: dict) -> bool:
    """Feature flag: per-device ``sea_v2_enabled`` overrides ``global.sea_v2_enabled``.
    Defaults off so the runtime keeps the legacy sea flow until explicitly enabled."""
    dev = (config.get("devices") or {}).get(ip) or {}
    if "sea_v2_enabled" in dev:
        return bool(dev["sea_v2_enabled"])
    return bool((config.get("global") or {}).get("sea_v2_enabled", False))


def _build_session(d):
    """Build an H5 session from the device wrapper, or None for non-H5 (adb) backends."""
    page = getattr(d, "_page", None)
    if page is None:
        return None
    from sea_v2.session import H5SeaSession
    return H5SeaSession(page)


def sea(ip: str, d, cache_path=None) -> SeaReport:
    """Entry point matching the legacy ``sea(ip, d)`` call signature.

    階段 A: H5 backends run navigation + reward-claim; garrison/attack are no-ops until
    the daytime 階段 B mapping lands. adb backends abort cleanly (階段 C)."""
    session = _build_session(d)
    if session is None:
        report = SeaReport(aborted_reason="sea_v2 supports H5 backend only (no _page); adb is 階段 C")
        logger.info("[sea] %s — ip=%s", report.aborted_reason, ip)
        return report
    if cache_path is None:
        from sea_v2.map_cache import default_cache_path
        cache_path = default_cache_path()
    from utils import pause_guard
    pause_guard.bind(ip=ip, page=session.page)
    try:
        return run_daily(session, account_id=ip, cache_path=cache_path)
    except pause_guard.TaskAborted as exc:
        logger.info("[sea] aborted ip=%s: %s", ip, exc)
        return SeaReport(aborted_reason=f"pause-state-diverged: {exc}")
    finally:
        pause_guard.unbind()

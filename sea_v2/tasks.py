"""Daily 航海 flow orchestration.

The flow is driven through a ``SeaSession`` (see ``session.py``) so the decision logic
here stays pure and unit-testable. Verified-now steps: enter, 定位, read tiles, pick
targets, bring-on-screen, claim rewards, write shared cache. The garrison/attack/repair
*actions* are session methods completed in the daytime window (階段 B) — this layer just
calls them on the chosen tiles and records what was attempted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sea_v2 import map_cache as mc
from sea_v2 import tiles as T

logger = logging.getLogger(__name__)


@dataclass
class SeaReport:
    home: Optional[T.Tile] = None
    garrisoned: List[Tuple[float, float]] = field(default_factory=list)
    attacked: List[Tuple[float, float]] = field(default_factory=list)
    repaired: bool = False
    rewards_claimed: int = 0
    aborted_reason: Optional[str] = None


def run_daily(session, account_id: Optional[str] = None, cache_path=None) -> SeaReport:
    """Run one daily sea pass. Always leaves the view tidy via ``exit_season``."""
    report = SeaReport()
    session.enter_season()
    try:
        cam_wp = session.locate()
        tiles = T.parse_tiles(session.read_objects())
        home = T.home_base(tiles, cam_wp)
        if home is None:
            report.aborted_reason = "home base not found"
            logger.warning("[sea] %s", report.aborted_reason)
            return report
        report.home = home

        plan = T.pick_daily_targets(tiles, home)
        _record_cache(account_id, cache_path, home, plan)

        # Map actions first (we stay on the map for these and for reward-claim)...
        for res in plan.resources:
            if session.bring_on_screen(res):
                if session.garrison(res):
                    report.garrisoned.append((res.wx, res.wy))
            else:
                logger.warning("[sea] resource off-screen, skipped: %s", res.wp)

        if plan.relic is not None:
            if session.bring_on_screen(plan.relic):
                if session.attack(plan.relic):
                    report.attacked.append((plan.relic.wx, plan.relic.wy))
            else:
                logger.warning("[sea] relic off-screen, skipped: %s", plan.relic.wp)

        report.rewards_claimed = int(session.claim_rewards() or 0)

        # ...repair LAST: it dives into 港口, and closing 港口 exits the season to the
        # main page, so doing it last means the exit doubles as leaving the season.
        report.repaired = bool(session.use_repair_kit())
        return report
    finally:
        session.exit_season()


def _record_cache(account_id, cache_path, home: T.Tile, plan: T.DailyTargets) -> None:
    if not cache_path:
        return
    cache = mc.load(cache_path)
    if account_id:
        mc.record_account_base(cache, account_id, home.wp)
    targets = list(plan.resources) + ([plan.relic] if plan.relic else [])
    mc.record_targets(cache, targets)
    mc.save(cache_path, cache)

"""Unit tests for utils.carpark_auto — pure logic only (no Cocos navigation)."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import pytest

import utils.carpark_auto as carpark_auto
from utils.carpark_auto import (
    POOL_TYPE_TO_ID, SILVER_LOT_COUNT, CarparkSnapshot, CarparkTarget,
    claim_warehouse, is_daytime_window, target_state,
)
from utils.carpark_state import DeployedCar


TAIWAN = timezone(timedelta(hours=8))


# ──────────────────────────────────────────────────────────────────────
# is_daytime_window — Taiwan 10:00–21:59 inclusive
# ──────────────────────────────────────────────────────────────────────


def _tw(h: int, m: int = 0) -> datetime:
    return datetime(2026, 5, 20, h, m, tzinfo=TAIWAN)


@pytest.mark.parametrize("hour,expected", [
    (0, False), (9, False), (9, False),
    (10, True), (11, True), (15, True), (21, True),
    (22, False), (23, False),
])
def test_daytime_window_boundaries(hour, expected):
    assert is_daytime_window(_tw(hour)) is expected


def test_daytime_window_minute_boundary_inclusive():
    """09:59 = night; 10:00 = day; 21:59 = day; 22:00 = night."""
    assert is_daytime_window(_tw(9, 59)) is False
    assert is_daytime_window(_tw(10, 0)) is True
    assert is_daytime_window(_tw(21, 59)) is True
    assert is_daytime_window(_tw(22, 0)) is False


# ──────────────────────────────────────────────────────────────────────
# target_state — applies daytime vs nighttime cfg
# ──────────────────────────────────────────────────────────────────────


_DEFAULT_CFG = {
    "daytime_total": 6, "daytime_cross": 1,
    "nighttime_total": 5, "nighttime_cross": 0,
}


def test_target_state_daytime():
    tgt = target_state(_DEFAULT_CFG, _tw(15))
    assert tgt == CarparkTarget(total_deployed=6, cross_count=1)
    assert tgt.normal_count == 5


def test_target_state_nighttime():
    tgt = target_state(_DEFAULT_CFG, _tw(23))
    assert tgt == CarparkTarget(total_deployed=5, cross_count=0)
    assert tgt.normal_count == 5


def test_target_state_uses_defaults_when_cfg_missing():
    """Module-level defaults should match the project's stated goal."""
    tgt_day = target_state({}, _tw(15))
    assert tgt_day.total_deployed == 6 and tgt_day.cross_count == 1
    tgt_night = target_state({}, _tw(2))
    assert tgt_night.total_deployed == 5 and tgt_night.cross_count == 0


# ──────────────────────────────────────────────────────────────────────
# CarparkSnapshot
# ──────────────────────────────────────────────────────────────────────


def _car(idx: int, park_type: str, cross: bool = False) -> DeployedCar:
    return DeployedCar(slot_idx=idx, hp_pct=100.0, timer_str="07:00:00",
                       park_type=park_type, at_limit=True,
                       cross_node_active=cross)


def test_carpark_snapshot_counts_cross_correctly():
    """cross_count counts cars whose nodeCorss.active is True — independent
    of the stale txtTime-001 string (which may say 跨界车位 even for friend
    lots due to UIList virtualization)."""
    snap = CarparkSnapshot(deployed=[
        _car(0, "跨界車位", cross=True),
        _car(1, "跨界車位", cross=True),
        _car(2, "跨界車位", cross=False),  # stale label, actually friend lot
        _car(3, "好友車位", cross=False),
    ])
    assert snap.cross_count == 2
    # Without home park, total = scrollHorse only
    assert snap.total == 4
    assert snap.normal_count == 0


def test_carpark_snapshot_includes_home_park():
    """total = scrollHorse + buildingRoot occupied (一般 cars at home)."""
    snap = CarparkSnapshot(
        deployed=[_car(0, "跨界車位", cross=True)],
        home_occupied=4, home_total=4,
    )
    assert snap.cross_count == 1
    assert snap.normal_count == 4
    assert snap.total == 5   # 1 cross + 4 home


def test_carpark_snapshot_empty():
    snap = CarparkSnapshot(deployed=[])
    assert snap.total == 0
    assert snap.cross_count == 0
    assert snap.normal_count == 0


# ──────────────────────────────────────────────────────────────────────
# Module constants — guard against typo regressions
# ──────────────────────────────────────────────────────────────────────


def test_pool_type_silver_maps_to_3():
    """5554 user policy says park silver — id 3 must remain the silver pool."""
    assert POOL_TYPE_TO_ID["silver"] == 3


def test_pool_type_all_5_tiers_present():
    assert set(POOL_TYPE_TO_ID.keys()) == {"diamond", "gold", "silver", "bronze", "server"}


def test_silver_lot_count_is_30():
    """鉑銀1 … 鉑銀30. If the game ever expands the pool, update SILVER_LOT_COUNT."""
    assert SILVER_LOT_COUNT == 30


# ──────────────────────────────────────────────────────────────────────
# Cleanup after live carpark UI actions
# ──────────────────────────────────────────────────────────────────────


class _FakeMouse:
    def __init__(self, events):
        self.events = events

    def click(self, x, y):
        self.events.append(("click", x, y))


class _WarehousePage:
    def __init__(self):
        self.events = []
        self.mouse = _FakeMouse(self.events)

    def evaluate(self, script, *args):
        text = str(script)
        if args and isinstance(args[0], list):
            self.events.append(("close_views", tuple(args[0])))
            return True
        if "btnWareHourse" in text:
            self.events.append(("probe_warehouse_button",))
            return {"has_red": True, "x": 10, "y": 20}
        if "ParkingWareHouseView" in text and "return !!" in text:
            self.events.append(("probe_warehouse_open",))
            return True
        if "rewardBtn" in text:
            self.events.append(("probe_reward_button",))
            return {"x": 30, "y": 40}
        return True


def test_claim_warehouse_closes_popups_after_reward_click(monkeypatch):
    page = _WarehousePage()
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _page: True)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _sec: None)

    assert claim_warehouse(page) is True

    assert ("click", 10, 20) in page.events
    reward_click_idx = page.events.index(("click", 30, 40))
    close_after_reward = [
        e for e in page.events[reward_click_idx + 1:]
        if e[0] == "close_views" and "ParkingWareHouseView" in e[1] and "GoodsGetView" in e[1]
    ]
    assert close_after_reward


def test_reconcile_returns_to_main_even_when_snapshot_fails(monkeypatch):
    called = []
    monkeypatch.setattr(carpark_auto, "parking_view_is_open", lambda _page: False)
    monkeypatch.setattr(carpark_auto, "take_snapshot", lambda _page: None)
    monkeypatch.setattr(carpark_auto, "_return_parking_to_main", lambda _page: called.append("returned") or True)

    assert carpark_auto.reconcile(object(), {}) == {"err": "no snapshot", "actions": []}
    assert called == ["returned"]


def test_reconcile_can_skip_h5_warehouse_when_ws_owns_claim(monkeypatch):
    claimed = []
    monkeypatch.setattr(carpark_auto, "parking_view_is_open", lambda _page: False)
    monkeypatch.setattr(
        carpark_auto,
        "take_snapshot",
        lambda _page: CarparkSnapshot(deployed=[]),
    )
    monkeypatch.setattr(
        carpark_auto,
        "claim_warehouse",
        lambda _page: claimed.append("claimed") or True,
    )
    monkeypatch.setattr(carpark_auto, "_return_parking_to_main", lambda _page: True)

    out = carpark_auto.reconcile(
        object(),
        _DEFAULT_CFG,
        now=_tw(23),
        claim_warehouse_rewards=False,
    )

    assert claimed == []
    assert "claimed warehouse rewards" not in out["actions"]


# ──────────────────────────────────────────────────────────────────────
# park_one_silver — avoid_lots filter
# ──────────────────────────────────────────────────────────────────────


def test_park_one_silver_skips_avoided_lots(monkeypatch):
    """avoid_lots=[1,2,3,4] (1-indexed) must never call _click_silver_lot_by_idx
    with lot_idx in {0,1,2,3} (0-indexed)."""
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_click_pool_tier", lambda _p, _id: True)

    attempted: list[int] = []

    def fake_click_lot(_p, lot_idx):
        attempted.append(lot_idx)
        return False  # always "no spot" → keep iterating

    monkeypatch.setattr(carpark_auto, "_click_silver_lot_by_idx", fake_click_lot)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)

    name = carpark_auto.park_one_silver(
        object(), prefer_back=True, cluster=False, avoid_lots=[1, 2, 3, 4],
    )
    assert name is None  # nothing parked (all attempts returned False)
    assert attempted, "park_one_silver tried zero lots — order filter killed everything"
    assert not (set(attempted) & {0, 1, 2, 3}), (
        f"avoided lots leaked into attempts: {sorted(set(attempted) & {0,1,2,3})}"
    )


def test_park_one_silver_avoid_lots_empty_means_no_filter(monkeypatch):
    """avoid_lots=None / [] = same as no filter."""
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_click_pool_tier", lambda _p, _id: True)
    attempted: list[int] = []
    monkeypatch.setattr(
        carpark_auto, "_click_silver_lot_by_idx",
        lambda _p, idx: attempted.append(idx) or False,
    )
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)

    carpark_auto.park_one_silver(object(), prefer_back=True, cluster=False, avoid_lots=None)
    # prefer_back + cluster=False + MAX_TRY=8 → should attempt 8 lots starting from 29 down
    assert attempted == [29, 28, 27, 26, 25, 24, 23, 22]


# ──────────────────────────────────────────────────────────────────────
# _silver_tier_has_empty — rendered tier aggregate, stale-value rejection
# ──────────────────────────────────────────────────────────────────────


class _TierPage:
    """Fake page whose evaluate returns a queued sequence of _SILVER_TIER_OCC_JS
    results (last one repeats once exhausted)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def evaluate(self, script, *args):
        self.calls += 1
        r = self._results[min(self.calls - 1, len(self._results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


def test_silver_tier_has_empty_true_when_not_full(monkeypatch):
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)
    page = _TierPage([{"ok": True, "occupied": 296, "total": 300}])
    assert carpark_auto._silver_tier_has_empty(page) is True
    assert page.calls == 1  # valid reading → no extra polling


def test_silver_tier_has_empty_false_when_full(monkeypatch):
    """300/300 → whole tier full → False → caller skips."""
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)
    page = _TierPage([{"ok": True, "occupied": 300, "total": 300}])
    assert carpark_auto._silver_tier_has_empty(page) is False


def test_silver_tier_has_empty_rejects_non_multiple_of_10(monkeypatch):
    """A malformed reading whose total isn't a multiple of 10 is rejected by the
    sanity gate; polling continues until a valid 300/300 reading arrives."""
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)
    page = _TierPage([
        {"ok": True, "occupied": 4, "total": 9},      # malformed → rejected
        {"ok": True, "occupied": 300, "total": 300},  # valid → full
    ])
    assert carpark_auto._silver_tier_has_empty(page) is False
    assert page.calls == 2


def test_silver_tier_has_empty_none_when_unreadable(monkeypatch):
    """Cross view not active / cell missing across all polls → None (fallback)."""
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)
    page = _TierPage([{"err": "cross_view_not_active"}])
    assert carpark_auto._silver_tier_has_empty(page, attempts=2) is None
    assert page.calls == 2


# ──────────────────────────────────────────────────────────────────────
# park_one_silver — fast-path skip when whole tier is full
# ──────────────────────────────────────────────────────────────────────


def test_park_one_silver_skips_when_tier_full_no_churn(monkeypatch):
    """The core ask: whole 泊銀車座 full → skip instantly, never drill into lots."""
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_silver_tier_has_empty", lambda _p, **k: False)
    drilled: list = []
    monkeypatch.setattr(carpark_auto, "_click_pool_tier",
                        lambda _p, _id: drilled.append("tier") or True)
    monkeypatch.setattr(carpark_auto, "_click_silver_lot_by_idx",
                        lambda _p, idx: drilled.append(idx) or True)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)

    assert carpark_auto.park_one_silver(object()) is None
    assert drilled == [], "skip path drilled into lots — should not churn"


def test_park_one_silver_proceeds_to_iteration_when_tier_has_empty(monkeypatch):
    """has_empty True → fall through to legacy iteration (drills lots)."""
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_silver_tier_has_empty", lambda _p, **k: True)
    monkeypatch.setattr(carpark_auto, "_click_pool_tier", lambda _p, _id: True)
    attempted: list = []
    monkeypatch.setattr(carpark_auto, "_click_silver_lot_by_idx",
                        lambda _p, idx: attempted.append(idx) or False)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)

    carpark_auto.park_one_silver(object(), prefer_back=True, cluster=False)
    assert attempted == [29, 28, 27, 26, 25, 24, 23, 22]


def test_park_one_silver_iterates_when_reading_unavailable(monkeypatch):
    """None reading (e.g. cross view not active) → legacy iteration, unchanged."""
    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", lambda _p: True)
    monkeypatch.setattr(carpark_auto, "_silver_tier_has_empty", lambda _p, **k: None)
    monkeypatch.setattr(carpark_auto, "_click_pool_tier", lambda _p, _id: True)
    attempted: list = []
    monkeypatch.setattr(carpark_auto, "_click_silver_lot_by_idx",
                        lambda _p, idx: attempted.append(idx) or False)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)

    carpark_auto.park_one_silver(object(), prefer_back=True, cluster=False)
    assert attempted == [29, 28, 27, 26, 25, 24, 23, 22]


# ──────────────────────────────────────────────────────────────────────
# parse_occupied_total / silver_tier_full — backend-agnostic OCR parsing
# (used by the ADB/OCR path; H5 reads the same occ/total from the scene tree)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("texts,expected", [
    (["299/300"], (299, 300)),
    (["299 / 300"], (299, 300)),
    (["299", "300"], (299, 300)),
    (["300/300"], (300, 300)),
    (["占用 299/300 滿"], (299, 300)),     # slash anchors past stray glyphs
])
def test_parse_occupied_total_valid(texts, expected):
    assert carpark_auto.parse_occupied_total(texts) == expected


@pytest.mark.parametrize("texts", [
    [],                       # nothing
    ["299305"],               # slash dropped, single 6-digit blob → can't split
    ["299/305"],              # total not a multiple of 10 → reject
    ["301/300"],              # occupied > total → reject
    ["abc"],                  # no digits
    ["10", "20", "30"],       # three numbers → ambiguous
    ["0/0"],                  # total 0 → reject
])
def test_parse_occupied_total_rejects_untrustworthy(texts):
    assert carpark_auto.parse_occupied_total(texts) is None


def test_silver_tier_full_verdicts():
    assert carpark_auto.silver_tier_full((300, 300)) is True
    assert carpark_auto.silver_tier_full((299, 300)) is False
    assert carpark_auto.silver_tier_full(None) is None


# ──────────────────────────────────────────────────────────────────────
# park_one_silver — re-enter-detail-list recovery branches (cx-4)
# These pin the two previously-untested recovery paths (full-lot and
# no-cluster) so extracting _reenter_silver_detail_list stays behaviour-identical.
# ──────────────────────────────────────────────────────────────────────


class _LotSnap:
    def __init__(self, empty_count, has_cluster_bonus, occupied_count=8, total=8):
        self.empty_count = empty_count
        self.has_cluster_bonus = has_cluster_bonus
        self.occupied_count = occupied_count
        self.total = total


def _setup_silver(monkeypatch, *, snapshot, pool_tier=None):
    """Wire park_one_silver's helpers; return a dict of call counters.

    pool_tier: optional fn(call_no:int)->bool to vary _click_pool_tier per call
    (call_no is 1-based); None means always True.
    """
    calls = {"main": 0, "space": 0, "tier": 0, "lots": []}

    def _main(_p):
        calls["main"] += 1
        return True

    def _space(_p):
        calls["space"] += 1
        return True

    def _tier(_p, _id):
        calls["tier"] += 1
        return True if pool_tier is None else pool_tier(calls["tier"])

    def _lot(_p, idx):
        calls["lots"].append(idx)
        return True

    monkeypatch.setattr(carpark_auto, "_ensure_parking_main_open", _main)
    monkeypatch.setattr(carpark_auto, "_open_space_view_and_cross_tab", _space)
    monkeypatch.setattr(carpark_auto, "_silver_tier_has_empty", lambda _p, **k: True)
    monkeypatch.setattr(carpark_auto, "_click_pool_tier", _tier)
    monkeypatch.setattr(carpark_auto, "_click_silver_lot_by_idx", _lot)
    monkeypatch.setattr(carpark_auto, "get_current_lot_snapshot", lambda _p: snapshot)
    monkeypatch.setattr(carpark_auto.time, "sleep", lambda _s: None)
    return calls


def test_park_one_silver_full_lots_reenter_each_time_then_none(monkeypatch):
    """Every lot full (empty_count==0) → re-enter detail list per lot, give up None."""
    calls = _setup_silver(monkeypatch, snapshot=_LotSnap(0, False))
    result = carpark_auto.park_one_silver(object(), prefer_back=True, cluster=False)
    assert result is None
    assert calls["lots"] == [29, 28, 27, 26, 25, 24, 23, 22]   # MAX_TRY=8
    assert calls["tier"] == 1 + 8   # initial click + one re-enter per full lot
    assert calls["main"] == 1 + 8


def test_park_one_silver_reenter_failure_returns_none_immediately(monkeypatch):
    """Re-enter failure (pool-tier re-click False) aborts with None, no further lots."""
    calls = _setup_silver(
        monkeypatch, snapshot=_LotSnap(0, False),
        pool_tier=lambda n: n == 1,   # True on initial click, False on first re-enter
    )
    result = carpark_auto.park_one_silver(object(), prefer_back=True, cluster=False)
    assert result is None
    assert calls["lots"] == [29]   # bailed during recovery after the first full lot
    assert calls["tier"] == 2      # initial + one failed re-enter


def test_park_one_silver_no_cluster_reenters_pass0_then_parks_pass1(monkeypatch):
    """empties-but-no-cluster → re-enter through pass 0; pass 1 (no filter) parks."""
    calls = _setup_silver(monkeypatch, snapshot=_LotSnap(empty_count=2, has_cluster_bonus=False))
    monkeypatch.setattr(carpark_auto, "_click_empty_spot_in_current_lot", lambda _p: 0)
    monkeypatch.setattr(carpark_auto, "_pick_zero_minute_car_and_park", lambda _p: "車A")
    result = carpark_auto.park_one_silver(object(), prefer_back=True, cluster=True)
    assert result == "車A"
    assert calls["tier"] == 1 + 8   # initial + 8 pass-0 re-enters; pass-1 parks first lot

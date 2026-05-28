"""Unit tests for utils.carpark_state — pure data + parsing logic only.

Live behavior (Cocos scene tree traversal) needs integration tests against
a running emulator-5554 — see tests/integration/test_carpark_live.py.
"""
from __future__ import annotations
import pytest

from utils.carpark_state import (
    AvailableCar, DeployedCar, LotSnapshot, LotSpot, _parse_today_min,
)


# ──────────────────────────────────────────────────────────────────────
# DeployedCar
# ──────────────────────────────────────────────────────────────────────


def test_deployed_car_is_cross_when_node_active():
    """Cross-server parks are flagged by nodeCorss.active in the live cell,
    NOT by the stale "跨界车位" string (UIList virtualization keeps the label
    after the cell becomes a friend-lot park). Verified live 2026-05-24."""
    c = DeployedCar(slot_idx=0, hp_pct=100.0, timer_str="07:30:00",
                    park_type="跨界車位", at_limit=True,
                    cross_node_active=True)
    assert c.is_cross is True


def test_deployed_car_not_cross_even_if_label_says_cross():
    """The 跨界车位 label is stale forensics — friend-lot parks reuse cells
    that previously held cross cars, leaving the string stuck."""
    c = DeployedCar(slot_idx=0, hp_pct=100.0, timer_str="07:30:00",
                    park_type="跨界车位", at_limit=True,
                    cross_node_active=False)
    assert c.is_cross is False


def test_deployed_car_is_cross_false_for_unknown_or_empty():
    c = DeployedCar(slot_idx=0, hp_pct=100.0, timer_str="07:30:00",
                    park_type="", at_limit=False)
    assert c.is_cross is False


def test_deployed_car_timer_seconds_hms():
    c = DeployedCar(slot_idx=0, hp_pct=100.0, timer_str="07:30:43",
                    park_type="跨界車位", at_limit=True)
    assert c.timer_seconds == 7 * 3600 + 30 * 60 + 43


def test_deployed_car_timer_seconds_ms_only():
    """Newly-deployed cars show MM:SS (e.g. "02:33")."""
    c = DeployedCar(slot_idx=0, hp_pct=0.0, timer_str="02:33",
                    park_type="跨界車位", at_limit=True)
    assert c.timer_seconds == 2 * 60 + 33


def test_deployed_car_timer_seconds_none_on_garbage():
    c = DeployedCar(slot_idx=0, hp_pct=None, timer_str="??",
                    park_type="", at_limit=False)
    assert c.timer_seconds is None
    c2 = DeployedCar(slot_idx=0, hp_pct=None, timer_str="",
                     park_type="", at_limit=False)
    assert c2.timer_seconds is None


# ──────────────────────────────────────────────────────────────────────
# LotSnapshot
# ──────────────────────────────────────────────────────────────────────


def _spots_pattern(occupancy: list[bool]) -> LotSnapshot:
    spots = [LotSpot(slot_idx=i + 1, occupied=occ) for i, occ in enumerate(occupancy)]
    return LotSnapshot(spots=spots)


def test_lot_snapshot_counts_correctly():
    snap = _spots_pattern([True, True, False, True, False, False, True, False, False, False])
    assert snap.total == 10
    assert snap.occupied_count == 4
    assert snap.empty_count == 6


def test_lot_snapshot_empty_slots_returns_only_empty():
    snap = _spots_pattern([False, True, False, True, True])
    empty = snap.empty_slots
    assert len(empty) == 2
    assert all(not s.occupied for s in empty)
    assert [s.slot_idx for s in empty] == [1, 3]


def test_cluster_bonus_threshold_at_5():
    """≥5/9 spots occupied unlocks 抱團 bonus (per user spec)."""
    assert _spots_pattern([True] * 4 + [False] * 6).has_cluster_bonus is False
    assert _spots_pattern([True] * 5 + [False] * 5).has_cluster_bonus is True
    assert _spots_pattern([True] * 10).has_cluster_bonus is True


# ──────────────────────────────────────────────────────────────────────
# _parse_today_min — strips rich-text + extracts minute count
# ──────────────────────────────────────────────────────────────────────


def test_parse_today_min_zero():
    s = "<b><color=695542>今日累計停車時間</color><color=d16802>0分鐘</color></b>"
    assert _parse_today_min(s) == 0


def test_parse_today_min_480():
    s = "<b><color=695542>今日累計停車時間</color><color=d16802>480分鐘</color></b>"
    assert _parse_today_min(s) == 480


def test_parse_today_min_handles_plain_string():
    """The label may also arrive without rich-text wrapping."""
    assert _parse_today_min("今日累計停車時間 90分鐘") == 90


def test_parse_today_min_none_on_garbage():
    assert _parse_today_min(None) is None
    assert _parse_today_min("") is None
    assert _parse_today_min("nothing matches") is None

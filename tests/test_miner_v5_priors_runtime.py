"""Tests for the v5 online (runtime) priors accumulation + merge.

Covers:
- observe_scroll harvests the freshly revealed bottom rows into vertical
  transition + marginal counts using coarse labels
- persistence is atomic (tmp+rename) and reloads exactly
- merged_priors == offline priors when no runtime file exists
- the online cap (20% of offline n) bounds a single device's influence
- per-device isolation (one device's counts never bleed into another)
"""
from __future__ import annotations

import json
import math

import pytest

from miner.depth_tracker import label_terrain
from miner.v5 import priors_runtime as pr
from miner.v5.priors import get_priors
from miner.v5.priors_runtime import (
    LABELS,
    PriorsAccumulator,
    merge_counts,
    merged_priors,
    runtime_path,
)


# ---------------------------------------------------------------------------
# Board helpers — symbols match the offline coarse labels via label_terrain.
# ---------------------------------------------------------------------------
_SYM = {
    ".": "empty",       # air
    "D": "dirt",
    "R": "rock",
    "*": "reachable_pit",  # pit
}


def _board(rows):
    grid = [[_SYM[ch] for ch in row] for row in rows]
    assert len(grid) == 7 and all(len(r) == 6 for r in grid), "expect 7x6"
    return grid


# ---------------------------------------------------------------------------
# observe_scroll
# ---------------------------------------------------------------------------
def test_observe_scroll_records_only_fresh_bottom_rows(tmp_path):
    acc = PriorsAccumulator(device="dev1", root=tmp_path)
    # 7 rows; a downward scroll of 1 reveals only the last row as fresh.
    prev = _board(["DDDDDD"] * 7)
    cur = _board(
        [
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "RRRRRR",  # freshly revealed bottom row (rock)
        ]
    )
    recorded = acc.observe_scroll(prev, cur, shifted=1)

    # shifted=1: the only fresh row is the last one, whose above-cell (row 5)
    # lives in the previous viewport (possibly post-dig), so NO vertical
    # transition is recorded — but the marginal still counts the fresh cells.
    assert recorded == 0
    v = acc.vertical_counts()
    assert v["dirt"]["rock"] == 0
    # marginal counts only the fresh row's 6 rock cells
    assert acc.marginal_counts()["rock"] == 6
    assert acc.marginal_counts()["dirt"] == 0


def test_observe_scroll_multi_row_uses_coarse_labels(tmp_path):
    acc = PriorsAccumulator(device="dev1", root=tmp_path)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(
        [
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "DDDDDD",
            "*****" + "*",  # row 5: pit
            "......",       # row 6: air
        ]
    )
    recorded = acc.observe_scroll(prev, cur, shifted=2)
    # 2 fresh rows (5,6); first_new=5. Row 5's above (row 4) is NOT fresh, so it
    # is skipped; only row 6 (above=row 5, both fresh) records -> 6 transitions.
    assert recorded == 6
    v = acc.vertical_counts()
    # row5's (dirt -> pit) is excluded: above row 4 lies in the previous viewport
    assert v["dirt"]["pit"] == 0
    # row6 above is row5 (pit) -> air, both freshly revealed
    assert v["pit"]["air"] == 6
    # marginal still tallies every fresh cell regardless of its above-cell
    marg = acc.marginal_counts()
    assert marg["pit"] == 6
    assert marg["air"] == 6


def test_observe_scroll_zero_shift_is_noop(tmp_path):
    acc = PriorsAccumulator(device="dev1", root=tmp_path)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(["RRRRRR"] * 7)
    assert acc.observe_scroll(prev, cur, shifted=0) == 0
    assert acc.observations == 0
    assert sum(acc.marginal_counts().values()) == 0


def test_label_terrain_used_for_coarse_bucketing():
    # Sanity: the runtime module shares the depth_tracker coarse labels.
    assert label_terrain("unreachable_rock") == "rock"
    assert label_terrain("dug_pit") == "air"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_persist_and_reload_roundtrip(tmp_path):
    acc = PriorsAccumulator(device="dev1", root=tmp_path, flush_every=1000)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(["DDDDDD"] * 6 + ["RRRRRR"])
    acc.observe_scroll(prev, cur, shifted=1)
    acc.close()

    path = runtime_path("dev1", root=tmp_path)
    assert path.exists()

    # A new accumulator on the same path loads the persisted counts.
    acc2 = PriorsAccumulator(device="dev1", root=tmp_path)
    # shifted=1 records no vertical transition (above row is from the previous
    # viewport), but the marginal tally and observation count persist + reload.
    assert acc2.vertical_counts()["dirt"]["rock"] == 0
    assert acc2.marginal_counts()["rock"] == 6
    assert acc2.observations == 6  # observations counts freshly revealed cells


def test_flush_is_atomic_tmp_rename(tmp_path, monkeypatch):
    acc = PriorsAccumulator(device="dev1", root=tmp_path, flush_every=1000)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(["DDDDDD"] * 6 + ["RRRRRR"])
    acc.observe_scroll(prev, cur, shifted=1)

    # If os.replace fails, the destination must NOT be left half-written, and the
    # temp file must be cleaned up.
    import os as _os

    real_replace = _os.replace

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(pr.os, "replace", boom)
    with pytest.raises(OSError):
        acc.flush()
    path = runtime_path("dev1", root=tmp_path)
    assert not path.exists()  # never partially written
    # no stray .tmp files left behind
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    monkeypatch.setattr(pr.os, "replace", real_replace)


def test_flush_every_triggers_autoflush(tmp_path):
    # flush_every counts freshly revealed cells; one 6-col fresh row = 6 cells.
    acc = PriorsAccumulator(device="dev1", root=tmp_path, flush_every=6)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(["DDDDDD"] * 6 + ["RRRRRR"])
    acc.observe_scroll(prev, cur, shifted=1)
    # auto-flushed already (>=6 unflushed)
    assert runtime_path("dev1", root=tmp_path).exists()


# ---------------------------------------------------------------------------
# Device isolation
# ---------------------------------------------------------------------------
def test_device_isolation(tmp_path):
    a = PriorsAccumulator(device="dev_a", root=tmp_path, flush_every=1)
    b = PriorsAccumulator(device="dev_b", root=tmp_path, flush_every=1)
    prev = _board(["DDDDDD"] * 7)
    cur = _board(["DDDDDD"] * 6 + ["RRRRRR"])
    a.observe_scroll(prev, cur, shifted=1)

    pa = runtime_path("dev_a", root=tmp_path)
    pb = runtime_path("dev_b", root=tmp_path)
    assert pa.exists()
    assert not pb.exists()  # b never observed anything
    assert pa != pb
    # device ids with ':' / '/' are sanitised into distinct, safe filenames
    assert runtime_path("127.0.0.1:5555", root=tmp_path) != runtime_path(
        "127.0.0.1_5555_x", root=tmp_path
    )


def test_runtime_path_sanitises_separators(tmp_path):
    p = runtime_path("a:b/c d", root=tmp_path)
    assert ":" not in p.name
    assert "/" not in p.name
    assert " " not in p.name


# ---------------------------------------------------------------------------
# merge_counts — cap logic
# ---------------------------------------------------------------------------
def test_merge_counts_no_online_is_identity():
    offline = {"dirt": {"air": 10, "dirt": 80, "rock": 8, "pit": 2}}
    online = {"dirt": {"air": 0, "dirt": 0, "rock": 0, "pit": 0}}
    merged = merge_counts(offline, online, {"dirt": 100})
    for lab in ("air", "dirt", "rock", "pit"):
        assert merged["dirt"][lab] == offline["dirt"][lab]


def test_merge_counts_caps_online_at_fraction_of_offline_n():
    # offline n=100 for dirt; cap fraction 0.2 -> online total capped at 20.
    offline = {a: {b: 0 for b in LABELS} for a in LABELS}
    offline["dirt"] = {"air": 0, "dirt": 100, "rock": 0, "pit": 0}
    online = {a: {b: 0 for b in LABELS} for a in LABELS}
    online["dirt"] = {"air": 0, "dirt": 0, "rock": 1000, "pit": 0}  # huge online
    merged = merge_counts(offline, online, {"dirt": 100}, cap_fraction=0.2)
    # online rock 1000 scaled to cap 20 (since 1000 > 20)
    assert math.isclose(merged["dirt"]["rock"], 20.0, rel_tol=1e-9)
    # offline dirt preserved
    assert merged["dirt"]["dirt"] == 100


def test_merge_counts_small_online_under_cap_unscaled():
    offline = {a: {b: 0 for b in LABELS} for a in LABELS}
    offline["dirt"] = {"air": 0, "dirt": 100, "rock": 0, "pit": 0}
    online = {a: {b: 0 for b in LABELS} for a in LABELS}
    online["dirt"] = {"air": 0, "dirt": 0, "rock": 5, "pit": 0}  # under cap (20)
    merged = merge_counts(offline, online, {"dirt": 100}, cap_fraction=0.2)
    assert merged["dirt"]["rock"] == 5  # unscaled


# ---------------------------------------------------------------------------
# merged_priors — integration with real offline priors
# ---------------------------------------------------------------------------
def test_merged_priors_equals_offline_when_no_runtime_file(tmp_path):
    offline = get_priors()
    merged = merged_priors("nonexistent_device", root=tmp_path)
    assert merged.below_given_above == offline.below_given_above
    assert merged.marginal == offline.marginal
    assert merged is offline or merged.source == offline.source


def test_merged_priors_blends_capped_online(tmp_path):
    offline = get_priors()
    # Write a runtime file with an extreme online skew for above=air.
    online = {a: {b: 0 for b in LABELS} for a in LABELS}
    online["air"] = {"air": 100000, "dirt": 0, "rock": 0, "pit": 0}
    marginal = {"air": 100000, "dirt": 0, "rock": 0, "pit": 0}
    path = runtime_path("dev_blend", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"vertical": online, "marginal": marginal, "observations": 100000}),
        encoding="utf-8",
    )

    merged = merged_priors("dev_blend", root=tmp_path)
    # P(air|air) should rise vs offline (online pushes toward air) but the cap
    # (20% of offline n) keeps it from snapping to ~1.0.
    off_aa = offline.below_given_above["air"]["air"]
    new_aa = merged.below_given_above["air"]["air"]
    assert new_aa > off_aa
    assert new_aa < 0.5  # capped, nowhere near 1.0 despite 100k online air
    # Row still normalised.
    assert math.isclose(sum(merged.below_given_above["air"].values()), 1.0, rel_tol=1e-6)
    assert "runtime" in merged.source


def test_merged_priors_marginal_normalised(tmp_path):
    online = {a: {b: 0 for b in LABELS} for a in LABELS}
    marginal = {"air": 500, "dirt": 0, "rock": 0, "pit": 0}
    path = runtime_path("dev_marg", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"vertical": online, "marginal": marginal, "observations": 0}),
        encoding="utf-8",
    )
    merged = merged_priors("dev_marg", root=tmp_path)
    assert math.isclose(sum(merged.marginal.values()), 1.0, rel_tol=1e-6)

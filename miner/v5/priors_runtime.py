"""Online (runtime) accumulation of v5 mining priors.

The offline priors (``miner/v5/priors.json``, built by
``tools/build_v5_priors.py`` from ~65k tape cells across 343 sessions) are a
static snapshot. This module lets a live mining session *keep observing* the
vertical terrain transitions it actually scrolls through and fold those fresh
counts back into the priors the planner consumes — without ever letting a
single device's short-term run drown out the 65k-sample offline base.

How an observation is harvested
-------------------------------
The mining viewport is a 7-row sliding window over a longer vertical tape
(see ``miner/depth_tracker.py``). When the board scrolls down by ``shifted``
rows, the bottom ``shifted`` rows of the *current* board are freshly revealed
tape cells we had never seen. A freshly revealed cell whose *above* cell is
**also** freshly revealed contributes one vertical-transition observation
``(above_label -> below_label)`` — exactly the statistic
``vertical_conditional_P_below_given_above`` in the offline priors. The topmost
freshly revealed row is deliberately excluded from the vertical table: its
above-cell lives in the previous viewport (the row whose clearing typically
triggered the scroll), so the classifier reads it as post-dig air rather than
the original terrain the offline tape records — counting it would contaminate
the merged ``above=air`` rows. Every freshly revealed cell is still tallied into
the marginal label distribution regardless of its above-cell.

Coarse labels (air / dirt / rock / pit) come from
``miner.depth_tracker.label_terrain`` so online and offline counts use the same
bucketing.

Merge rule (cap, not decay-by-time)
-----------------------------------
``merged_priors(device)`` adds the online counts to the offline counts, but the
online contribution for each ``above`` row is **capped at 20% of the offline
``n`` for that row**. A single device that hits an unusual streak can nudge the
priors, but cannot more than slightly perturb a well-sampled offline cell. With
no runtime file present the merge is the identity (merged == offline).

Persistence
-----------
Counts persist per device at
``miner/v5/runtime/priors_runtime_<device>.json`` (atomic tmp+rename). The
accumulator auto-flushes every ``FLUSH_EVERY`` observations and on
``close()``. The JSON shape deliberately mirrors the offline priors' nested
``{above: {below: count}}`` layout so the two are trivial to diff/merge and so
``tools/build_v5_priors.py --include-runtime`` can list them side by side.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional, Sequence

from miner.depth_tracker import label_terrain
from miner.v5.priors import Priors, get_priors
from utils.log_paths import LogPaths

logger = logging.getLogger(__name__)

Board = Sequence[Sequence[str]]

LABELS = ("air", "dirt", "rock", "pit")

#: Flush to disk every N observed transitions (or on close()).
FLUSH_EVERY = 20

#: Online counts for an `above` row are capped at this fraction of the offline
#: sample size for the same row before being merged.
ONLINE_CAP_FRACTION = 0.20

_RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------
def _empty_vertical() -> Dict[str, Dict[str, int]]:
    return {above: {below: 0 for below in LABELS} for above in LABELS}


def _empty_marginal() -> Dict[str, int]:
    return {lab: 0 for lab in LABELS}


def runtime_path(device: str, *, root: Optional[Path] = None) -> Path:
    """Path of the runtime counts JSON for ``device``.

    ``root`` overrides the default ``miner/v5/runtime`` directory (test sandbox).
    """
    base = Path(root) if root is not None else _RUNTIME_DIR
    safe = LogPaths.safe_path_segment(device)
    return base / f"priors_runtime_{safe}.json"


def _load_counts(path: Path) -> Dict[str, object]:
    """Load persisted counts, returning zeroed structures when absent/bad."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"vertical": _empty_vertical(), "marginal": _empty_marginal(), "observations": 0}
    vertical = _empty_vertical()
    for above in LABELS:
        node = (raw.get("vertical") or {}).get(above, {})
        for below in LABELS:
            try:
                vertical[above][below] = int(node.get(below, 0))
            except (TypeError, ValueError):
                vertical[above][below] = 0
    marginal = _empty_marginal()
    for lab in LABELS:
        try:
            marginal[lab] = int((raw.get("marginal") or {}).get(lab, 0))
        except (TypeError, ValueError):
            marginal[lab] = 0
    try:
        observations = int(raw.get("observations", 0))
    except (TypeError, ValueError):
        observations = 0
    return {"vertical": vertical, "marginal": marginal, "observations": observations}


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------
class PriorsAccumulator:
    """Accumulate online vertical-transition counts for one device.

    Usage::

        acc = PriorsAccumulator(device="emulator-5554")
        ...
        shifted = depth_tracker.update(board)
        if shifted > 0:
            acc.observe_scroll(prev_board, board, shifted)
        ...
        acc.close()  # final flush
    """

    def __init__(
        self,
        device: str,
        *,
        root: Optional[Path] = None,
        flush_every: int = FLUSH_EVERY,
    ) -> None:
        self.device = device
        self._path = runtime_path(device, root=root)
        self._flush_every = max(1, int(flush_every))
        self._lock = threading.Lock()
        loaded = _load_counts(self._path)
        self._vertical: Dict[str, Dict[str, int]] = loaded["vertical"]  # type: ignore[assignment]
        self._marginal: Dict[str, int] = loaded["marginal"]  # type: ignore[assignment]
        self._observations: int = loaded["observations"]  # type: ignore[assignment]
        self._unflushed = 0

    # -- observation -------------------------------------------------------
    def observe_scroll(self, prev_board: Board, cur_board: Board, shifted: int) -> int:
        """Record the freshly revealed bottom rows of ``cur_board``.

        After a downward scroll of ``shifted`` rows, the bottom ``shifted`` rows
        of ``cur_board`` are tape cells never seen before. Each freshly revealed
        cell is tallied into the marginal distribution; a vertical
        ``(above -> below)`` transition is recorded only when the above cell is
        itself freshly revealed (``r-1 >= first_new``). The topmost freshly
        revealed row therefore contributes to the marginal only — its above-cell
        lives in the previous viewport and may be post-dig air, which would
        contaminate the vertical table (see module preamble).

        ``prev_board`` is currently unused (the counted above-cell always lives
        inside the freshly revealed band of ``cur_board`` itself) but is part of
        the API so future revisions can cross-check the overlap. Returns the
        number of transition observations recorded.
        """
        if shifted <= 0:
            return 0
        rows = len(cur_board)
        if rows == 0:
            return 0
        cols = len(cur_board[0]) if cur_board[0] is not None else 0
        shifted = min(int(shifted), rows)
        first_new = rows - shifted  # index of the first freshly revealed row

        recorded = 0  # vertical transitions (the return value)
        seen = 0      # freshly revealed cells (drives flush + observation count)
        with self._lock:
            for r in range(first_new, rows):
                for c in range(cols):
                    below = label_terrain(str(cur_board[r][c]))
                    self._marginal[below] += 1
                    seen += 1
                    # Only count a vertical transition when the *above* cell is
                    # itself freshly revealed (r-1 >= first_new). The topmost new
                    # row's above-cell lives in the PREVIOUS viewport and is the
                    # row whose clearing usually triggered the scroll, so the
                    # classifier now reads it as post-dig air — not the original
                    # terrain the offline tape records. Counting it would bias the
                    # merged above=air rows upward. See module preamble.
                    if r - 1 >= first_new:
                        above = label_terrain(str(cur_board[r - 1][c]))
                        self._vertical[above][below] += 1
                        recorded += 1
            # Persist by fresh-cell volume, not transition count: in the common
            # shifted==1 case no vertical transition is recorded, but the
            # marginal still gains `cols` cells worth flushing before close().
            self._observations += seen
            self._unflushed += seen
            should_flush = self._unflushed >= self._flush_every
        if should_flush:
            self.flush()
        return recorded

    # -- persistence -------------------------------------------------------
    def _snapshot(self) -> Dict[str, object]:
        return {
            "device": self.device,
            "observations": self._observations,
            "vertical": {a: dict(self._vertical[a]) for a in LABELS},
            "marginal": dict(self._marginal),
        }

    def flush(self) -> None:
        with self._lock:
            payload = self._snapshot()
            self._unflushed = 0
        _atomic_write_json(self._path, payload)

    def close(self) -> None:
        """Final flush (idempotent)."""
        self.flush()

    # -- read access (tests / diagnostics) ---------------------------------
    @property
    def observations(self) -> int:
        return self._observations

    def vertical_counts(self) -> Dict[str, Dict[str, int]]:
        return {a: dict(self._vertical[a]) for a in LABELS}

    def marginal_counts(self) -> Dict[str, int]:
        return dict(self._marginal)


# ---------------------------------------------------------------------------
# Merge: offline priors + capped online counts
# ---------------------------------------------------------------------------
def _offline_vertical_counts(offline: Priors) -> Dict[str, Dict[str, int]]:
    """Reconstruct integer offline transition counts from probs * n.

    The offline ``Priors`` dataclass only exposes probabilities, but the merge
    needs counts. We recover them from the per-row ``n`` recorded in
    ``priors.json``; if ``n`` is unavailable (hard-coded fallback) we fall back
    to a nominal weight so the cap math still behaves.
    """
    counts: Dict[str, Dict[str, int]] = {}
    n_by_above = _offline_n_by_above(offline)
    for above in LABELS:
        probs = offline.below_given_above.get(above, {})
        n = n_by_above.get(above, 0)
        counts[above] = {below: int(round(probs.get(below, 0.0) * n)) for below in LABELS}
    return counts


def _offline_n_by_above(offline: Priors) -> Dict[str, int]:
    """Per-`above` offline sample size, read from priors.json when available."""
    n_by_above: Dict[str, int] = {}
    raw_n = getattr(offline, "below_given_above_n", None)
    if isinstance(raw_n, dict):
        for above in LABELS:
            try:
                n_by_above[above] = int(raw_n.get(above, 0))
            except (TypeError, ValueError):
                n_by_above[above] = 0
        return n_by_above
    # Fallback: derive from priors.json on disk if loadable, else a nominal base.
    n_by_above = _read_offline_n_from_json()
    if n_by_above:
        return n_by_above
    return {above: 1000 for above in LABELS}


def _read_offline_n_from_json() -> Dict[str, int]:
    path = Path(__file__).resolve().parent / "priors.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        node = raw["vertical_conditional_P_below_given_above"]
        return {above: int(node[above]["n"]) for above in LABELS}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def merge_counts(
    offline_vertical: Dict[str, Dict[str, int]],
    online_vertical: Dict[str, Dict[str, int]],
    offline_n_by_above: Dict[str, int],
    *,
    cap_fraction: float = ONLINE_CAP_FRACTION,
) -> Dict[str, Dict[str, float]]:
    """Offline + capped online transition counts.

    Online counts for each ``above`` row are scaled down so their total never
    exceeds ``cap_fraction * offline_n``. This bounds a single device's
    short-term influence; with zero online counts the result equals offline.
    """
    merged: Dict[str, Dict[str, float]] = {}
    for above in LABELS:
        off_row = offline_vertical.get(above, {})
        on_row = online_vertical.get(above, {})
        on_total = sum(on_row.get(below, 0) for below in LABELS)
        off_n = offline_n_by_above.get(above, 0)
        cap = cap_fraction * off_n
        scale = 1.0
        if on_total > 0 and cap >= 0 and on_total > cap:
            scale = cap / on_total
        merged[above] = {
            below: off_row.get(below, 0) + on_row.get(below, 0) * scale
            for below in LABELS
        }
    return merged


def _normalize(counts_row: Dict[str, float]) -> Dict[str, float]:
    total = sum(counts_row.values())
    if total <= 0:
        return {lab: 0.0 for lab in LABELS}
    return {lab: counts_row.get(lab, 0.0) / total for lab in LABELS}


def merged_priors(
    device: str,
    *,
    root: Optional[Path] = None,
    cap_fraction: float = ONLINE_CAP_FRACTION,
) -> Priors:
    """Return offline priors blended with this device's capped online counts.

    With no runtime file present the result is value-equal to ``get_priors()``
    (online counts are all zero). Only the vertical transition table and the
    marginal distribution are blended; ``pit_below_by_width`` is kept from the
    offline priors (the runtime accumulator does not yet estimate it).
    """
    offline = get_priors()
    path = runtime_path(device, root=root)
    if not path.exists():
        return offline

    loaded = _load_counts(path)
    online_vertical: Dict[str, Dict[str, int]] = loaded["vertical"]  # type: ignore[assignment]
    online_marginal: Dict[str, int] = loaded["marginal"]  # type: ignore[assignment]

    offline_vertical = _offline_vertical_counts(offline)
    offline_n = _offline_n_by_above(offline)
    blended_v_counts = merge_counts(
        offline_vertical, online_vertical, offline_n, cap_fraction=cap_fraction
    )
    below_given_above = {above: _normalize(blended_v_counts[above]) for above in LABELS}

    # Marginal: offline counts (from marginal prob * total tape cells) + capped
    # online marginal. Cap total online marginal at cap_fraction of offline.
    offline_marginal_counts = _offline_marginal_counts(offline)
    off_marg_total = sum(offline_marginal_counts.values())
    on_marg_total = sum(online_marginal.get(lab, 0) for lab in LABELS)
    marg_cap = cap_fraction * off_marg_total
    marg_scale = 1.0
    if on_marg_total > 0 and on_marg_total > marg_cap:
        marg_scale = marg_cap / on_marg_total
    blended_marg = {
        lab: offline_marginal_counts.get(lab, 0) + online_marginal.get(lab, 0) * marg_scale
        for lab in LABELS
    }
    marginal = _normalize(blended_marg)

    return Priors(
        marginal=marginal,
        below_given_above=below_given_above,
        pit_below_by_width=dict(offline.pit_below_by_width),
        source=f"{offline.source}+runtime({device})",
    )


def _offline_marginal_counts(offline: Priors) -> Dict[str, int]:
    """Recover offline marginal counts from priors.json (or a nominal base)."""
    path = Path(__file__).resolve().parent / "priors.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        node = raw["overall_label_distribution"]
        return {lab: int(node[lab]["count"]) for lab in LABELS}
    except (OSError, ValueError, KeyError, TypeError):
        total = 65000
        return {lab: int(offline.marginal.get(lab, 0.0) * total) for lab in LABELS}


__all__ = [
    "PriorsAccumulator",
    "merged_priors",
    "merge_counts",
    "runtime_path",
    "FLUSH_EVERY",
    "ONLINE_CAP_FRACTION",
    "LABELS",
]

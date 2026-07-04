"""Per-session scroll / depth tracking for the mining viewport.

The mining board is a 7-row viewport sliding down a longer vertical tape.
When the bot clears the bottom of the viewport the game auto-scrolls, so two
consecutive classifications can be offset by a few rows. :class:`DepthTracker`
aligns consecutive boards, infers the scroll offset, and accumulates a relative
session depth.

Key tolerances when matching two frames:

* Cells the bot dug between frames change ``solid -> air`` (e.g. dirt -> empty,
  pit -> dug_pit). Those are *expected* and must not count as mismatches.
* Reachability can flip (``unreachable_rock -> rock``) once digging opens a
  path. We compare on coarse terrain (air / dirt / rock / pit), which is
  invariant to the ``unreachable_`` prefix.

When the overlap has too little comparable structure to decide, the tracker
returns ``0`` and sets :attr:`last_uncertain` instead of guessing.

The row-shift scorer (:func:`best_scroll`) is shared with
``tools/track_pits_replay.py`` so the offline reconstruction and the live
tracker stay in lock-step.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

Board = Sequence[Sequence[str]]

# Coarse terrain buckets. "air" carries no structural signal (already open),
# so it is ignored during alignment scoring.
_AIR = "air"


def label_terrain(label: str) -> str:
    """Reduce a DEFAULT_CLASSES label to a coarse terrain bucket.

    ``unreachable_`` is stripped first so reachability flips do not look like
    structural changes. Pits (reachable or not) and dug pits collapse onto the
    same bucket as dug/air where appropriate.
    """
    base = label.replace("unreachable_", "")
    if base in ("empty", "void", "dug_pit"):
        return _AIR
    if base in ("dirt",):
        return "dirt"
    if base in ("rock", "one_hit_rock"):
        return "rock"
    if base in ("pit", "reachable_pit"):
        return "pit"
    # Unknown -> treat as a generic solid obstacle so it still contributes
    # structural signal rather than being silently dropped.
    return "rock"


def best_scroll(
    prev: Board,
    cur: Board,
    *,
    normalize: Callable[[str], str],
    max_s: int = 6,
    min_comparable: int = 6,
) -> Tuple[int, float]:
    """Best downward scroll ``s`` (0..max_s) aligning ``cur`` under ``prev``.

    Only solid (non-air) structure on the overlap is compared. A cell that is
    open in ``prev`` carries no signal; a cell that became air in ``cur`` is
    treated as compatible (the bot dug it) rather than a mismatch.

    Returns ``(s, ratio)`` where ``ratio`` is matches / comparable on the best
    offset, or ``-1.0`` when no offset had enough comparable structure. Ties on
    ratio prefer the smaller scroll (scroll is usually 0 or 1).
    """
    rows = min(len(prev), len(cur))
    cols = min(len(prev[0]), len(cur[0])) if rows else 0
    best: Tuple[int, float] = (0, -1.0)
    for s in range(0, max_s + 1):
        comparable = 0
        matches = 0
        for i in range(0, rows - s):
            for j in range(cols):
                a = normalize(prev[s + i][j])
                b = normalize(cur[i][j])
                if a == _AIR:
                    continue  # prev already open here -> no structural signal
                if b == _AIR:
                    continue  # cur dug/opened this cell: compatible, weak
                comparable += 1
                if a == b:
                    matches += 1
        ratio = (matches / comparable) if comparable >= min_comparable else -1.0
        if ratio > best[1] + 1e-9:
            best = (s, ratio)
    return best


class DepthTracker:
    """Track relative scroll depth across a mining session.

    Usage::

        tracker = DepthTracker()
        for board in boards:
            shifted = tracker.update(board)  # rows scrolled this frame
            log(tracker.depth)               # accumulated session depth
    """

    #: Minimum alignment confidence to trust an inferred non-zero scroll.
    RATIO_FLOOR = 0.55

    def __init__(self, ratio_floor: float = RATIO_FLOOR) -> None:
        self._ratio_floor = ratio_floor
        self.depth: int = 0
        self.last_uncertain: bool = False
        self._prev: Optional[List[List[str]]] = None

    def reset(self) -> None:
        """Clear all state; the next ``update`` becomes a fresh baseline."""
        self.depth = 0
        self.last_uncertain = False
        self._prev = None

    def set_absolute_depth(self, depth: int) -> None:
        """Override the accumulated depth with a known absolute baseline.

        Used by the WS path, which can derive an absolute viewport depth from
        the board baseline. The alignment baseline is also cleared so the next
        :meth:`update` is treated as a fresh frame (no spurious scroll against a
        pre-calibration board); subsequent relative scrolls continue from here.
        """
        self.depth = int(depth)
        self.last_uncertain = False
        self._prev = None

    def update(self, board: Board) -> int:
        """Feed the latest board; return rows scrolled since the previous one.

        The first board (or the first after :meth:`reset`) is a baseline and
        returns ``0`` without moving :attr:`depth`. Ambiguous alignments return
        ``0`` and set :attr:`last_uncertain`.
        """
        snapshot = [list(row) for row in board]
        if self._prev is None:
            self._prev = snapshot
            self.last_uncertain = False
            return 0

        s, ratio = best_scroll(self._prev, snapshot, normalize=label_terrain)
        if ratio < self._ratio_floor:
            # Too little overlap to decide — don't guess, don't move depth.
            self.last_uncertain = True
            self._prev = snapshot
            return 0

        self.last_uncertain = False
        self._prev = snapshot
        if s > 0:
            self.depth += s
        return s


__all__ = ["DepthTracker", "best_scroll", "label_terrain"]

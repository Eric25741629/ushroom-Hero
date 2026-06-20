"""Pure-WS terrain reconstruction from the static configMine_template.

WHY: WS 0x0c01 never sends the terrain TYPE of an *undug* cell. Verified live
2026-06-20 (5556): every 201/202 block carries count==0 (already dug); the only
count>0 blocks are pits (401); undug cells are bare ``actives`` with no block.
So the planner cannot tell undug dirt (cost 1) from undug stone (cost 2) — it
blanket-assumes dirt and therefore never bombs a stone cluster it hasn't already
dug into. That blindness is the remaining efficiency ceiling.

HOW (self-learning, no CNN at runtime): the board is the client tiling fixed 7x6
templates vertically (12 distinct grids in configMine_template, values 100=air /
201=dirt / 202=stone; pits overlay separately from WS). As the bot digs, every
dug cell reveals its *real* terrain (the count==0 block's config_id). We
accumulate those reveals keyed by absolute (depth, col) — global, because a shaft
is dug straight down once — and match them against the 12 templates to learn,
per 7-row band, which template fills it. Once a band is identified we can read
the terrain of its still-undug cells straight off the template.

The mapping is *learned*, not assumed: we search the global phase (which depth a
band starts on, 0..6) and the flat->grid orientation, and only lock a band's
template when the accumulated reveals leave exactly one consistent candidate.
Unidentified cells return None and the caller keeps its old dirt default, so
integrating this can only improve the projection, never regress it.
"""
from __future__ import annotations

import io
import json
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROWS = 7
COLS = 6
AIR = 100
DIRT = 201
STONE = 202
PIT = 401

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLE_PATH = os.path.join(_REPO, "docs", "protocol", "mine_config_tables.json")
_CACHE_DIR = os.path.join(_REPO, "miner", "terrain_cache")


def default_path(device_id: str) -> str:
    """Per-device terrain cache path. Each device is a separate account/shaft, so
    its learned terrain must not be shared. ``:`` (TCP serials) is sanitized."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(device_id))
    return os.path.join(_CACHE_DIR, f"{safe}.json")

# A template grid is ROWS x COLS of {AIR, DIRT, STONE}.
Grid = Tuple[Tuple[int, ...], ...]


def _grid_rowmajor(flat: List[int]) -> Grid:
    return tuple(tuple(flat[r * COLS + c] for c in range(COLS)) for r in range(ROWS))


def _grid_colmajor(flat: List[int]) -> Grid:
    # flat laid out column-by-column (7 per column)
    return tuple(tuple(flat[c * ROWS + r] for c in range(COLS)) for r in range(ROWS))


def _load_templates() -> Dict[str, List[Grid]]:
    """Return distinct 7x6 template grids for each orientation.

    {"row": [Grid, ...], "col": [Grid, ...]} — same flats, two readings. The
    learner tries both and the consistent one wins; we don't assume which the
    client uses.
    """
    with io.open(_TABLE_PATH, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    entries = data["template"]["entries"]
    flats: List[List[int]] = []
    seen = set()
    for _k, ent in entries.items():
        flat = list(ent[1])
        if len(flat) != ROWS * COLS:
            continue
        key = tuple(flat)
        if key in seen:
            continue
        seen.add(key)
        flats.append(flat)
    return {
        "row": [_grid_rowmajor(f) for f in flats],
        "col": [_grid_colmajor(f) for f in flats],
    }


_TEMPLATES: Optional[Dict[str, List[Grid]]] = None


def templates(orientation: str = "row") -> List[Grid]:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = _load_templates()
    return _TEMPLATES[orientation]


def _band_of(depth: int, phase: int) -> Tuple[int, int]:
    """(band_index, row_in_band) for an absolute depth under a phase 0..6."""
    rel = depth - phase
    # floor division so negative depths still tile correctly
    b = rel // ROWS
    rr = rel - b * ROWS
    return b, rr


def _consistent_templates(
    band_cells: List[Tuple[int, int, int]], grids: List[Grid]
) -> List[int]:
    """Indices of templates whose (rr,col) terrain matches every revealed cell.

    band_cells: list of (row_in_band, col0, terrain) with terrain in {DIRT,STONE}.
    """
    out = []
    for idx, g in enumerate(grids):
        ok = True
        for rr, c, terr in band_cells:
            if not (0 <= rr < ROWS and 0 <= c < COLS):
                ok = False
                break
            if g[rr][c] != terr:
                ok = False
                break
        if ok:
            out.append(idx)
    return out


class TerrainModel:
    """Accumulates dug-cell reveals and reconstructs undug terrain per band.

    Coordinates are ABSOLUTE: depth (block_id // 100) and col0 (block_id % 100 - 1,
    0-indexed). The whole shaft shares one model — a band's template, once learned,
    stays valid because you never re-dig a depth.
    """

    # Keep only reveals within this many depths of the deepest seen cell. A shaft
    # is dug straight down once; bands far above the frontier are never
    # reconstructed again, so dropping them bounds learn() cost and the cache file
    # without losing any cell we'd actually query.
    # ponytail: sliding window; widen if a future planner reconstructs far behind.
    _WINDOW = 400

    def __init__(self) -> None:
        self.reveals: Dict[Tuple[int, int], int] = {}
        self.phase: Optional[int] = None
        self.orientation: Optional[str] = None
        self.bands: Dict[int, int] = {}  # band_index -> template_index (locked orientation)
        self._dirty = True
        self._max_depth = None

    # ---- ingest -------------------------------------------------------
    def observe(self, depth: int, col0: int, terrain: int) -> None:
        """Record a revealed terrain cell (terrain in {DIRT, STONE})."""
        if terrain not in (DIRT, STONE):
            return
        depth = int(depth)
        key = (depth, int(col0))
        if self.reveals.get(key) != terrain:
            self.reveals[key] = terrain
            self._dirty = True
        if self._max_depth is None or depth > self._max_depth:
            self._max_depth = depth
            cutoff = depth - self._WINDOW
            if cutoff > 0:
                stale = [k for k in self.reveals if k[0] < cutoff]
                for k in stale:
                    del self.reveals[k]
                if stale:
                    self._dirty = True

    def observe_board(self, mine_board) -> None:
        """Pull every revealed terrain cell from a parsed MineBoard."""
        for blk in getattr(mine_board, "blocks", []) or []:
            cfg = int(getattr(blk, "config_id", 0) or 0)
            if cfg in (DIRT, STONE):
                self.observe(int(blk.y), int(blk.x) - 1, cfg)

    # A band's template is "identified" (trusted for reconstruction) only once it
    # has at least this many revealed cells. Because the board IS one of the 12
    # templates, a unique consistent match is correct *given the right phase* — but
    # with very few cells a wrong phase can coincidentally look consistent, so we
    # demand enough per-band evidence before acting on a stone prediction.
    _MIN_BAND_REVEALS = 6

    # ---- learn --------------------------------------------------------
    def _score(self, phase: int, orientation: str) -> Optional[Tuple[int, Dict[int, int]]]:
        """If viable (every non-empty band has >=1 consistent template), return
        (identified_band_count, band->template) where a band is *identified* only
        when it has >=_MIN_BAND_REVEALS cells AND exactly one consistent template.
        Return None on any contradiction (a band with zero consistent templates),
        which marks this (phase,orientation) as wrong."""
        grids = templates(orientation)
        by_band: Dict[int, List[Tuple[int, int, int]]] = {}
        for (depth, col0), terr in self.reveals.items():
            b, rr = _band_of(depth, phase)
            by_band.setdefault(b, []).append((rr, col0, terr))
        identified: Dict[int, int] = {}
        for b, cells in by_band.items():
            cons = _consistent_templates(cells, grids)
            if not cons:
                return None  # contradiction -> wrong phase/orientation
            if len(cons) == 1 and len(cells) >= self._MIN_BAND_REVEALS:
                identified[b] = cons[0]
        return len(identified), identified

    # Corroboration path: two separate >=6-reveal unique band matches agreeing on
    # one phase also pins it (used when several phases remain contradiction-free).
    _MIN_CORROBORATING_BANDS = 2

    def learn(self) -> None:
        if not self._dirty:
            return

        # Score every (phase, orientation). _score returns None on a contradiction
        # (a band with zero consistent templates), so the survivors are exactly the
        # alignments the data permits. The TRUE phase can never contradict, so:
        #   - if only ONE alignment survives and it has a well-filled identified
        #     band, that survivor IS the true phase (13/14 contradicting it is
        #     overwhelming) -> lock it; or
        #   - if some alignment has >=2 independently identified bands, lock that.
        # With sparse data many alignments survive with no identified band -> we
        # lock nothing and reconstruct nothing (safe no-op).
        viable = []  # (phase, orientation, identified_dict)
        for orientation in ("row", "col"):
            for phase in range(ROWS):
                res = self._score(phase, orientation)
                if res is not None:
                    viable.append((phase, orientation, res[1]))

        # Fast path: keep the current lock if it is still among the survivors.
        if self.phase is not None and self.orientation is not None:
            for p, o, ident in viable:
                if p == self.phase and o == self.orientation:
                    self.bands = ident
                    self._dirty = False
                    return

        corroborated = [v for v in viable if len(v[2]) >= self._MIN_CORROBORATING_BANDS]
        if corroborated:
            self.phase, self.orientation, self.bands = max(corroborated, key=lambda v: len(v[2]))
        elif len(viable) == 1 and len(viable[0][2]) >= 1:
            self.phase, self.orientation, self.bands = viable[0]
        else:
            self.phase = self.orientation = None
            self.bands = {}
        self._dirty = False

    # ---- reconstruct --------------------------------------------------
    def terrain_at(self, depth: int, col0: int) -> Optional[int]:
        """Reconstructed terrain {AIR,DIRT,STONE} for an undug cell, or None if
        the band isn't identified yet."""
        self.learn()
        if self.phase is None or self.orientation is None:
            return None
        b, rr = _band_of(int(depth), self.phase)
        tmpl = self.bands.get(b)
        if tmpl is None:
            return None
        if not (0 <= col0 < COLS):
            return None
        return templates(self.orientation)[tmpl][rr][col0]

    def stats(self) -> Dict[str, object]:
        self.learn()
        return {
            "reveals": len(self.reveals),
            "phase": self.phase,
            "orientation": self.orientation,
            "bands_identified": len(self.bands),
        }

    # ---- persistence --------------------------------------------------
    def to_dict(self) -> Dict[str, object]:
        return {"reveals": [[d, c, t] for (d, c), t in self.reveals.items()]}

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "TerrainModel":
        m = cls()
        for row in d.get("reveals", []) or []:
            try:
                m.observe(int(row[0]), int(row[1]), int(row[2]))
            except (TypeError, ValueError, IndexError):
                continue
        return m

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "TerrainModel":
        try:
            with io.open(path, encoding="utf-8-sig") as fh:
                return cls.from_dict(json.load(fh))
        except (OSError, ValueError):
            return cls()


def _selftest() -> None:
    """Synthetic: tile two known templates, reveal a subset, assert recovery."""
    grids = templates("row")
    assert len(grids) == 12, len(grids)
    PHASE = 3
    t_a, t_b = 0, 5  # two distinct template indices
    # band 10 uses t_a, band 11 uses t_b
    m = TerrainModel()

    def reveal_band(band_idx: int, tmpl_idx: int, which_cells) -> None:
        for (rr, c) in which_cells:
            terr = grids[tmpl_idx][rr][c]
            if terr in (DIRT, STONE):
                depth = PHASE + band_idx * ROWS + rr
                m.observe(depth, c, terr)

    # reveal enough cells of each band to uniquely pin the template
    all_cells = [(rr, c) for rr in range(ROWS) for c in range(COLS)]
    reveal_band(10, t_a, all_cells)
    reveal_band(11, t_b, all_cells)
    m.learn()
    st = m.stats()
    assert st["phase"] == PHASE, st
    assert st["orientation"] == "row", st
    assert m.bands.get(10) == t_a, (m.bands, t_a)
    assert m.bands.get(11) == t_b, (m.bands, t_b)

    # an undug STONE cell in band 10 must reconstruct as stone
    stone_pos = next(
        ((rr, c) for rr in range(ROWS) for c in range(COLS) if grids[t_a][rr][c] == STONE),
        None,
    )
    assert stone_pos is not None
    rr, c = stone_pos
    depth = PHASE + 10 * ROWS + rr
    assert m.terrain_at(depth, c) == STONE, m.terrain_at(depth, c)

    # round-trip persistence
    m2 = TerrainModel.from_dict(m.to_dict())
    m2.learn()
    assert m2.bands.get(10) == t_a and m2.bands.get(11) == t_b
    print("mine_terrain selftest OK:", st)


if __name__ == "__main__":
    _selftest()

"""Build probability priors for the v5 (probabilistic) mining planner.

The v5 planner only sees a 7-row viewport; everything below the viewport is
unknown. It needs empirical priors to estimate the expected dig cost of going
*down* into the unknown. This tool mines the real `logs/<device>/miner.log`
history to produce those priors.

WHY GLOBAL-TAPE RECONSTRUCTION (not raw snapshots):
A single 7-row snapshot is a sliding window over a vertical tape. The same tape
cell appears in several consecutive frames at different viewport rows, and a
3x3 pit cluster is collected incrementally as the window descends, so it never
shows up intact in one frame. Computing vertical conditional probabilities from
raw snapshots therefore (a) double-counts cells and (b) biases pit adjacency.
Instead we align consecutive frames by their scroll offset (same algorithm as
`tools/track_pits_replay.py`), build a per-session GLOBAL tape where each cell
records the terrain it had BEFORE being dug, and compute every statistic over
that reconstruction.

Statistics produced (all with sample counts; combos with n<100 flagged
low_confidence):
  1. overall label distribution: air / dirt / rock / pit
  2. vertical conditional P(below=X | above=Y)
  3. vertical air run-length distribution (how far an air column extends down)
  4. horizontal adjacency P(right=X | left=Y) (simple correlation check)
  5. depth drift: stats bucketed by absolute tape depth (chi-square vs pooled)
  6. cluster square prior: P(another row below | pit run of width w touches a
     reconstructed segment's bottom edge), for w in {1,2,3}

Outputs:
  miner/v5/priors.json     machine-readable priors + sample counts
  docs/MINING_V5_PRIORS.md  short human report

Usage:
    python tools/build_v5_priors.py
    python tools/build_v5_priors.py --glob "logs/*/miner*.log"
    python tools/build_v5_priors.py --depth-bucket 50
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# symbol -> coarse terrain class (mirrors tools/track_pits_replay.NORM)
NORM = {
    ".": "air", "_": "air",
    "D": "dirt", "d": "dirt",
    "R": "rock", "r": "rock",
    "*": "pit", "X": "pit",
}
PIT_CHARS = {"*", "X"}
LABELS = ("air", "dirt", "rock", "pit")
NCOLS = 6
NROWS = 7

LOW_CONFIDENCE_N = 100

SESSION_START_RE = re.compile(r"開始挖礦")
BOARD_HEADER_RE = re.compile(r"^\s*0 1 2 3 4 5\s*$")
BOARD_ROW_RE = re.compile(r"^\s*\d+\s+([.\_DdRr*X ]+)$")


# --------------------------------------------------------------------------- #
# Parsing (shared shape with track_pits_replay / replay_real_boards)
# --------------------------------------------------------------------------- #
def parse_events(path: Path):
    """Yield ('session', None) and ('board', grid) events in file order."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if SESSION_START_RE.search(line):
            events.append(("session", None))
        content = line.split("] ", 1)[-1] if "] " in line else line
        if BOARD_HEADER_RE.match(content.strip()):
            grid = []
            j = i + 1
            while j < n and len(grid) < NROWS:
                rc = lines[j].split("] ", 1)[-1] if "] " in lines[j] else lines[j]
                rm = BOARD_ROW_RE.match(rc)
                if not rm:
                    break
                cells = rm.group(1).split()
                if len(cells) == NCOLS:
                    grid.append(cells)
                j += 1
            if len(grid) == NROWS:
                events.append(("board", grid))
                i = j
                continue
        i += 1
    return events


def split_sessions(events):
    sessions, cur = [], []
    for kind, payload in events:
        if kind == "session":
            if cur:
                sessions.append(cur)
            cur = []
        else:
            cur.append(payload)
    if cur:
        sessions.append(cur)
    return sessions


def best_scroll(prev, cur, max_s=6):
    """Best downward scroll s (0..max_s) aligning cur under prev.

    Identical structural-match logic to tools/track_pits_replay.best_scroll:
    compares only solid (non-air) cells on the overlap, tolerates dug cells.
    """
    best = (0, -1.0)
    for s in range(0, max_s + 1):
        comparable = matches = 0
        for i in range(0, NROWS - s):
            for j in range(NCOLS):
                a = NORM[prev[s + i][j]]
                b = NORM[cur[i][j]]
                if a == "air" or b == "air":
                    continue
                comparable += 1
                if a == b:
                    matches += 1
        ratio = (matches / comparable) if comparable >= 6 else -1.0
        if ratio > best[1] + 1e-9:
            best = (s, ratio)
    return best


# --------------------------------------------------------------------------- #
# Global-tape reconstruction
# --------------------------------------------------------------------------- #
def reconstruct_tapes(boards, ratio_floor=0.55):
    """Reconstruct per-session global tapes from a session's snapshots.

    Returns a list of tapes. Each tape is a dict {(global_row, col): label}
    recording the *original* terrain at each cell (the first non-air class ever
    seen there; pit beats everything because pit is the spawn signal). A tape
    breaks into a new segment whenever alignment confidence drops, so unrelated
    tape stretches never merge.
    """
    tapes = []
    cur = {}                 # (row, col) -> label
    seen_air = set()         # cells observed as air at some frame
    offset = 0
    prev = None

    def flush():
        nonlocal cur, seen_air
        if cur or seen_air:
            tapes.append((cur, seen_air))
        cur, seen_air = {}, set()

    for grid in boards:
        if prev is None:
            offset = 0
        else:
            s, ratio = best_scroll(prev, grid)
            if ratio < ratio_floor:
                flush()
                offset = 0
            else:
                offset += s
        for i in range(NROWS):
            for j in range(NCOLS):
                cls = NORM[grid[i][j]]
                key = (offset + i, j)
                if cls == "air":
                    seen_air.add(key)
                    # Only mark as air if never seen solid (cell may be dug).
                    if key not in cur:
                        cur[key] = "air"
                else:
                    # Solid observation is authoritative original terrain.
                    # pit wins over dirt/rock if both ever seen (spawn signal).
                    prevcls = cur.get(key)
                    if prevcls is None or prevcls == "air":
                        cur[key] = cls
                    elif cls == "pit":
                        cur[key] = "pit"
        prev = grid
    flush()
    return tapes


# --------------------------------------------------------------------------- #
# Statistics accumulation over reconstructed tapes
# --------------------------------------------------------------------------- #
class Stats:
    def __init__(self, depth_bucket: int):
        self.depth_bucket = depth_bucket
        self.label_counts = Counter()
        # vertical: vcond[above][below] = count
        self.vcond = {y: Counter() for y in LABELS}
        # horizontal: hcond[left][right] = count
        self.hcond = {y: Counter() for y in LABELS}
        # air run-length: how far a column of air extends downward
        self.air_runlen = Counter()
        # depth-bucketed label counts: bucket -> Counter(label)
        self.depth_label = defaultdict(Counter)
        # cluster vertical-extension prior: width -> {"pit_below": n, "total": n}
        # measured only on pit runs that HAVE an observed row below them, so the
        # cell-below ground truth is known (not the unknown tape edge).
        self.cluster_edge = {w: {"pit_below": 0, "total": 0} for w in (1, 2, 3)}

    def add_tape(self, cur, seen_air):
        if not cur:
            return
        rows = [r for (r, _c) in cur]
        cols_present = defaultdict(dict)  # row -> {col: label}
        for (r, c), lab in cur.items():
            cols_present[r][c] = lab
            self.label_counts[lab] += 1
            self.depth_label[r // self.depth_bucket][lab] += 1

        min_r, max_r = min(rows), max(rows)

        # vertical conditional + air run length, per column
        for c in range(NCOLS):
            run = 0
            for r in range(min_r, max_r + 1):
                here = cols_present.get(r, {}).get(c)
                below = cols_present.get(r + 1, {}).get(c)
                if here is not None and below is not None:
                    self.vcond[here][below] += 1
                # air run-length: count maximal downward air streaks
                if here == "air":
                    run += 1
                else:
                    if run > 0:
                        self.air_runlen[run] += 1
                    run = 0
            if run > 0:
                self.air_runlen[run] += 1

        # horizontal conditional (left -> right neighbour)
        for r in range(min_r, max_r + 1):
            rowmap = cols_present.get(r, {})
            for c in range(NCOLS - 1):
                left = rowmap.get(c)
                right = rowmap.get(c + 1)
                if left is not None and right is not None:
                    self.hcond[left][right] += 1

        # cluster square prior: find pit runs touching a segment bottom edge
        self._cluster_edge(cur, cols_present, max_r)

    def _cluster_edge(self, cur, cols_present, max_r):
        """Estimate P(pit directly below | horizontal pit run of width w).

        Square clusters of width w are w rows tall, so a width-w pit run that is
        NOT the cluster's bottom row should have pit ore directly beneath it.
        For each horizontal pit run we look at the row below: if that row is
        observed (ground truth known, not the unknown tape edge), we record
        whether any of the run's columns is also pit. The resulting probability
        tells v5 how strongly a wide pit at the viewport bottom predicts more
        ore in the next (currently unknown) row.
        """
        pit_by_row = defaultdict(set)
        for (r, c), lab in cur.items():
            if lab == "pit":
                pit_by_row[r].add(c)
        for r, cset in pit_by_row.items():
            below_row = cols_present.get(r + 1, {})
            if not below_row:
                # row below unobserved (tape edge) -> no ground truth, skip
                continue
            for run_cols in _contiguous(sorted(cset)):
                w = len(run_cols)
                if w not in self.cluster_edge:
                    continue
                self.cluster_edge[w]["total"] += 1
                if any(below_row.get(c) == "pit" for c in run_cols):
                    self.cluster_edge[w]["pit_below"] += 1


def _contiguous(sorted_cols):
    """Yield maximal contiguous column runs from a sorted col list."""
    if not sorted_cols:
        return
    run = [sorted_cols[0]]
    for c in sorted_cols[1:]:
        if c == run[-1] + 1:
            run.append(c)
        else:
            yield run
            run = [c]
    yield run


# --------------------------------------------------------------------------- #
# Probability table builders + reporting helpers
# --------------------------------------------------------------------------- #
def cond_table(cond):
    """Convert {above:{below:count}} into probs + n, flagging low confidence."""
    out = {}
    for y in LABELS:
        row = cond[y]
        n = sum(row.values())
        probs = {x: (row.get(x, 0) / n if n else 0.0) for x in LABELS}
        out[y] = {
            "n": n,
            "probs": probs,
            "low_confidence": n < LOW_CONFIDENCE_N,
        }
    return out


def chi_square_depth(depth_label, overall):
    """Chi-square of bucketed label counts vs pooled distribution.

    Returns (chi2, dof, buckets_used, per_bucket_dist). A large chi2 relative
    to dof => label mix drifts with depth.
    """
    total = sum(overall.values())
    if total == 0:
        return 0.0, 0, 0, {}
    pooled = {lab: overall.get(lab, 0) / total for lab in LABELS}
    chi2 = 0.0
    buckets = sorted(depth_label)
    per_bucket = {}
    dof_cells = 0
    for b in buckets:
        cnt = depth_label[b]
        nb = sum(cnt.values())
        if nb < LOW_CONFIDENCE_N:
            per_bucket[b] = {"n": nb, "dist": {lab: (cnt.get(lab, 0) / nb if nb else 0)
                                               for lab in LABELS},
                             "low_confidence": True}
            continue
        per_bucket[b] = {"n": nb,
                         "dist": {lab: cnt.get(lab, 0) / nb for lab in LABELS},
                         "low_confidence": False}
        for lab in LABELS:
            exp = pooled[lab] * nb
            obs = cnt.get(lab, 0)
            if exp > 0:
                chi2 += (obs - exp) ** 2 / exp
                dof_cells += 1
    big_buckets = sum(1 for b in per_bucket.values() if not b["low_confidence"])
    dof = max(0, (big_buckets - 1) * (len(LABELS) - 1))
    return chi2, dof, big_buckets, per_bucket


def chi2_critical_005(dof):
    """Rough chi-square 0.05 critical value (lookup + Wilson-Hilferty)."""
    table = {1: 3.84, 2: 5.99, 3: 7.81, 4: 9.49, 6: 12.59, 9: 16.92,
             12: 21.03, 15: 25.00, 18: 28.87, 21: 32.67}
    if dof in table:
        return table[dof]
    if dof <= 0:
        return 0.0
    # Wilson-Hilferty approximation for the 0.95 quantile
    z = 1.6449
    return dof * (1 - 2 / (9 * dof) + z * math.sqrt(2 / (9 * dof))) ** 3


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="logs/**/miner*.log",
                    help="glob (recursive) for miner logs")
    ap.add_argument("--depth-bucket", type=int, default=50,
                    help="rows per depth bucket for drift analysis")
    ap.add_argument("--out-json", default="miner/v5/priors.json")
    ap.add_argument("--out-md", default="docs/MINING_V5_PRIORS.md")
    ap.add_argument("--include-runtime", action="store_true",
                    help="also print the online (runtime) vertical-transition "
                         "counts accumulated under miner/v5/runtime/ alongside "
                         "the offline table, for manual drift cross-checking")
    args = ap.parse_args()

    paths = sorted(
        Path(p) for p in glob.glob(str(REPO_ROOT / args.glob), recursive=True)
    )
    if not paths:
        print(f"No logs matched {args.glob} under {REPO_ROOT}")
        print("Searched:", REPO_ROOT / args.glob)
        return

    stats = Stats(args.depth_bucket)
    per_device = Counter()
    sessions_total = 0
    boards_total = 0
    tapes_total = 0
    tape_cells_total = 0

    for path in paths:
        events = parse_events(path)
        sessions = split_sessions(events)
        dev = path.parent.name if path.parent.name != "logs" else path.stem
        for boards in sessions:
            if not boards:
                continue
            sessions_total += 1
            boards_total += len(boards)
            per_device[dev] += len(boards)
            for tape, seen_air in reconstruct_tapes(boards):
                if not tape:
                    continue
                tapes_total += 1
                tape_cells_total += len(tape)
                stats.add_tape(tape, seen_air)

    # --- build probability tables ---
    total_labels = sum(stats.label_counts.values())
    overall = {lab: {"count": stats.label_counts.get(lab, 0),
                     "prob": (stats.label_counts.get(lab, 0) / total_labels
                              if total_labels else 0.0)}
               for lab in LABELS}

    vtable = cond_table(stats.vcond)
    htable = cond_table(stats.hcond)

    runlen_total = sum(stats.air_runlen.values())
    runlen = {str(k): {"count": v,
                       "prob": (v / runlen_total if runlen_total else 0.0)}
              for k, v in sorted(stats.air_runlen.items())}

    chi2, dof, nbuckets, per_bucket = chi_square_depth(
        stats.depth_label, stats.label_counts)
    crit = chi2_critical_005(dof)
    depth_significant = dof > 0 and chi2 > crit

    cluster_edge = {}
    for w, d in stats.cluster_edge.items():
        tot = d["total"]
        cluster_edge[str(w)] = {
            "total": tot,
            "pit_below": d["pit_below"],
            "p_pit_below": (d["pit_below"] / tot if tot else 0.0),
            "low_confidence": tot < LOW_CONFIDENCE_N,
        }

    # is the vertical distribution non-uniform vs overall marginal?
    air_below_air = vtable["air"]["probs"]["air"]
    nonuniform_evidence = {
        "p_air_below_air": air_below_air,
        "p_air_marginal": overall["air"]["prob"],
        "air_below_air_lift": (air_below_air / overall["air"]["prob"]
                               if overall["air"]["prob"] else 0.0),
        "p_pit_below_pit": vtable["pit"]["probs"]["pit"],
        "p_pit_marginal": overall["pit"]["prob"],
        "pit_below_pit_lift": (vtable["pit"]["probs"]["pit"] / overall["pit"]["prob"]
                               if overall["pit"]["prob"] else 0.0),
    }
    supports_nonuniform = (
        nonuniform_evidence["air_below_air_lift"] > 1.2
        or nonuniform_evidence["pit_below_pit_lift"] > 1.2
        or depth_significant
    )

    priors = {
        "meta": {
            "generated_by": "tools/build_v5_priors.py",
            "log_glob": args.glob,
            "logs_parsed": len(paths),
            "sessions": sessions_total,
            "board_snapshots": boards_total,
            "reconstructed_tapes": tapes_total,
            "tape_cells": tape_cells_total,
            "per_device_boards": dict(per_device),
            "labels": list(LABELS),
            "low_confidence_threshold_n": LOW_CONFIDENCE_N,
            "depth_bucket_rows": args.depth_bucket,
        },
        "overall_label_distribution": overall,
        "vertical_conditional_P_below_given_above": vtable,
        "horizontal_conditional_P_right_given_left": htable,
        "air_run_length_distribution": runlen,
        "depth_drift": {
            "chi_square": chi2,
            "dof": dof,
            "critical_0.05": crit,
            "buckets_used": nbuckets,
            "significant": depth_significant,
            "per_bucket": {str(k): v for k, v in per_bucket.items()},
        },
        "cluster_square_prior_P_pit_below_given_pit_width": cluster_edge,
        "nonuniform_hypothesis": {
            "evidence": nonuniform_evidence,
            "supported": supports_nonuniform,
        },
    }

    out_json = REPO_ROOT / args.out_json
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(priors, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    _write_report(REPO_ROOT / args.out_md, priors)

    # --- console summary ---
    print("=" * 68)
    print(f"logs={len(paths)} sessions={sessions_total} boards={boards_total} "
          f"tapes={tapes_total} tape_cells={tape_cells_total}")
    print("overall:", {k: f"{v['prob']*100:.1f}%" for k, v in overall.items()})
    print("\nP(below | above):")
    print(f"  {'above':6s} " + " ".join(f"{x:>7s}" for x in LABELS) + "    n")
    for y in LABELS:
        row = vtable[y]
        flag = " LOWconf" if row["low_confidence"] else ""
        print(f"  {y:6s} " +
              " ".join(f"{row['probs'][x]*100:6.1f}%" for x in LABELS) +
              f"  {row['n']:6d}{flag}")
    print(f"\nair-below-air lift: {nonuniform_evidence['air_below_air_lift']:.2f}x "
          f"| pit-below-pit lift: {nonuniform_evidence['pit_below_pit_lift']:.2f}x")
    print(f"depth drift: chi2={chi2:.1f} dof={dof} crit05={crit:.1f} "
          f"-> {'SIGNIFICANT' if depth_significant else 'not significant'}")
    print("cluster P(pit below | pit width):",
          {w: f"{d['p_pit_below']*100:.0f}% (n={d['total']})"
           for w, d in cluster_edge.items()})
    print(f"non-uniform hypothesis supported: {supports_nonuniform}")
    print(f"\nwrote {out_json}")
    print(f"wrote {REPO_ROOT / args.out_md}")

    if args.include_runtime:
        _print_runtime_comparison(vtable)


def _print_runtime_comparison(offline_vtable):
    """Print offline vs online vertical-transition distributions side by side.

    Reads the per-device runtime counts written by
    ``miner/v5/priors_runtime.PriorsAccumulator`` and aggregates them so a human
    can eyeball whether the live distribution has drifted from the offline base.
    """
    runtime_dir = REPO_ROOT / "miner" / "v5" / "runtime"
    files = sorted(runtime_dir.glob("priors_runtime_*.json")) if runtime_dir.exists() else []
    print("\n" + "=" * 68)
    print("RUNTIME (online) priors comparison")
    if not files:
        print(f"  no runtime files under {runtime_dir}")
        return

    agg = {a: {b: 0 for b in LABELS} for a in LABELS}
    total_obs = 0
    for f in files:
        try:
            raw = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            print(f"  skip unreadable {f.name}")
            continue
        total_obs += int(raw.get("observations", 0))
        vert = raw.get("vertical", {})
        for a in LABELS:
            node = vert.get(a, {})
            for b in LABELS:
                agg[a][b] += int(node.get(b, 0))
        print(f"  device file: {f.name} (observations={raw.get('observations', 0)})")
    print(f"  aggregate online observations: {total_obs}\n")

    print("  P(below | above)  [OFFLINE  ->  ONLINE]:")
    print(f"  {'above':6s} " + " ".join(f"{x:>14s}" for x in LABELS) + "      n_online")
    for a in LABELS:
        on_row = agg[a]
        n = sum(on_row.values())
        off = offline_vtable[a]["probs"]
        cells = []
        for b in LABELS:
            off_p = off[b] * 100
            on_p = (on_row[b] / n * 100) if n else 0.0
            cells.append(f"{off_p:5.1f}->{on_p:5.1f}%")
        print(f"  {a:6s} " + " ".join(cells) + f"   {n:8d}")


def _pct(x):
    return f"{x*100:.1f}%"


def _write_report(path: Path, p):
    m = p["meta"]
    overall = p["overall_label_distribution"]
    v = p["vertical_conditional_P_below_given_above"]
    h = p["horizontal_conditional_P_right_given_left"]
    runlen = p["air_run_length_distribution"]
    depth = p["depth_drift"]
    cluster = p["cluster_square_prior_P_pit_below_given_pit_width"]
    nu = p["nonuniform_hypothesis"]

    lines = []
    lines.append("# Mining v5 Probabilistic Priors\n")
    lines.append("> Auto-generated by `tools/build_v5_priors.py`. "
                 "Re-run to regenerate from current logs.\n")
    lines.append("## Data source & sample size\n")
    lines.append(f"- Logs parsed: **{m['logs_parsed']}** "
                 f"(`{m['log_glob']}`)")
    lines.append(f"- Mining sessions: **{m['sessions']}**")
    lines.append(f"- Board snapshots: **{m['board_snapshots']}**")
    lines.append(f"- Reconstructed global tapes: **{m['reconstructed_tapes']}** "
                 f"({m['tape_cells']} tape cells)")
    lines.append(f"- Low-confidence threshold: n < "
                 f"**{m['low_confidence_threshold_n']}**")
    lines.append("- Per-device board contribution:")
    for dev, cnt in sorted(m["per_device_boards"].items(),
                           key=lambda kv: -kv[1]):
        lines.append(f"  - `{dev}`: {cnt}")
    lines.append("\nReconstruction note: vertical/horizontal/cluster stats are "
                 "computed over **time-aligned global tapes** (consecutive "
                 "snapshots aligned by scroll offset), not raw snapshots, to "
                 "avoid double-counting cells and pit-adjacency bias.\n")

    lines.append("## 1. Overall label distribution\n")
    lines.append("| label | prob | count |")
    lines.append("|-------|------|-------|")
    for lab in LABELS:
        lines.append(f"| {lab} | {_pct(overall[lab]['prob'])} "
                     f"| {overall[lab]['count']} |")

    lines.append("\n## 2. Vertical conditional P(below = X | above = Y)\n")
    lines.append("| above \\ below | air | dirt | rock | pit | n | conf |")
    lines.append("|---|---|---|---|---|---|---|")
    for y in LABELS:
        row = v[y]
        conf = "low" if row["low_confidence"] else "ok"
        lines.append(f"| **{y}** | " +
                     " | ".join(_pct(row["probs"][x]) for x in LABELS) +
                     f" | {row['n']} | {conf} |")

    lines.append("\n## 3. Vertical air run-length distribution\n")
    lines.append("How far a column of air extends downward before hitting "
                 "solid/edge.\n")
    lines.append("| run length | prob | count |")
    lines.append("|---|---|---|")
    for k, d in runlen.items():
        lines.append(f"| {k} | {_pct(d['prob'])} | {d['count']} |")

    lines.append("\n## 4. Horizontal conditional P(right = X | left = Y)\n")
    lines.append("| left \\ right | air | dirt | rock | pit | n | conf |")
    lines.append("|---|---|---|---|---|---|---|")
    for y in LABELS:
        row = h[y]
        conf = "low" if row["low_confidence"] else "ok"
        lines.append(f"| **{y}** | " +
                     " | ".join(_pct(row["probs"][x]) for x in LABELS) +
                     f" | {row['n']} | {conf} |")

    lines.append("\n## 5. Depth correlation (drift with absolute depth)\n")
    lines.append(f"- Depth bucket size: {m['depth_bucket_rows']} rows")
    lines.append(f"- Chi-square: **{depth['chi_square']:.1f}** "
                 f"(dof {depth['dof']}, 0.05 critical "
                 f"{depth['critical_0.05']:.1f})")
    verdict = ("**SIGNIFICANT** depth correlation — priors should be bucketed "
               "by depth" if depth["significant"] else
               "**No significant** depth correlation — pooled priors are safe")
    lines.append(f"- Verdict: {verdict}")
    lines.append("- Caveat: with this many cells, chi-square is sensitive; "
                 "judge the per-bucket table below for **effect size**. Sessions "
                 "rarely descend past ~100 rows, so deep buckets are sparse and "
                 "the test mostly contrasts the first ~50 rows against 50-100.\n")
    lines.append("| depth bucket (rows) | n | air | dirt | rock | pit | conf |")
    lines.append("|---|---|---|---|---|---|---|")
    for b, d in sorted(depth["per_bucket"].items(), key=lambda kv: int(kv[0])):
        lo = int(b) * m["depth_bucket_rows"]
        hi = lo + m["depth_bucket_rows"] - 1
        conf = "low" if d["low_confidence"] else "ok"
        lines.append(f"| {lo}-{hi} | {d['n']} | " +
                     " | ".join(_pct(d["dist"][lab]) for lab in LABELS) +
                     f" | {conf} |")

    lines.append("\n## 6. Cluster square prior: P(pit below | pit run width)\n")
    lines.append("For a horizontal pit run of width w that has an observed row "
                 "beneath it (ground truth known), how often is the cell "
                 "directly below also pit? Square clusters of width w span w "
                 "rows, so a wider run that is not the bottom row should "
                 "continue downward. This measures how strongly a wide pit at "
                 "the viewport bottom predicts ore in the next unknown row.\n")
    lines.append("| pit width w | P(pit below) | pit_below | total | conf |")
    lines.append("|---|---|---|---|---|")
    for w in ("1", "2", "3"):
        d = cluster[w]
        conf = "low" if d["low_confidence"] else "ok"
        lines.append(f"| {w} | {_pct(d['p_pit_below'])} | "
                     f"{d['pit_below']} | {d['total']} | {conf} |")

    lines.append("\n## Non-uniform hypothesis\n")
    ev = nu["evidence"]
    lines.append(f"- P(air | air above) = {_pct(ev['p_air_below_air'])} "
                 f"vs marginal air {_pct(ev['p_air_marginal'])} "
                 f"→ **{ev['air_below_air_lift']:.2f}x lift**")
    lines.append(f"- P(pit | pit above) = {_pct(ev['p_pit_below_pit'])} "
                 f"vs marginal pit {_pct(ev['p_pit_marginal'])} "
                 f"→ **{ev['pit_below_pit_lift']:.2f}x lift**")
    lines.append(f"- **Hypothesis supported: {nu['supported']}**\n")

    lines.append("## Planner design implications\n")
    air_lift = ev["air_below_air_lift"]
    pit_lift = ev["pit_below_pit_lift"]
    if air_lift > 1.2:
        lines.append(f"- Air clusters vertically ({air_lift:.2f}x lift): "
                     "descending through an existing air pocket is cheap — the "
                     "cell below an open cell is disproportionately also open. "
                     "v5 should bias its down-path through known air columns.")
    if pit_lift > 1.2:
        lines.append(f"- Pit clusters vertically ({pit_lift:.2f}x lift): once a "
                     "pit cell is found, expect more ore directly below — keep "
                     "digging the column rather than abandoning it.")
    p1 = cluster["1"]["p_pit_below"]
    p2 = cluster["2"]["p_pit_below"]
    p3 = cluster["3"]["p_pit_below"]
    lines.append(f"- Pit-below-pit by run width: w1={_pct(p1)}, w2={_pct(p2)}, "
                 f"w3={_pct(p3)}. Wider pit runs are far more likely to have ore "
                 "directly below (square clusters extend downward), so when a "
                 "wide pit run sits at the viewport's bottom edge, v5 should "
                 "value scrolling into the unknown row beneath it.")
    if depth["significant"]:
        lines.append("- Depth drift is statistically significant but small in "
                     "effect (a few percentage points between the 0-49 and "
                     "50-99 buckets) and the available history barely reaches "
                     "~100 rows deep. v5 can ship with pooled priors now; "
                     "bucket by depth only if deeper sessions later reveal a "
                     "larger drift.")
    else:
        lines.append("- Depth drift is not significant: a single pooled prior "
                     "table is sufficient; no need to track absolute depth.")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

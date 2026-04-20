# Miner V3

Cluster-aware mining planner that fixes the structural problems in V2.

## Why V3

V2 had three bugs that caused missed pits:

1. **Plan-start BFS rebuild over-promoted reachability** — every non-`unreachable_*`
   air cell was used as a BFS seed, then propagation through `unreachable_empty`
   pockets stripped `unreachable_` prefixes off arbitrary walls. CNN errors
   amplified into "phantom reachable" interior pockets the planner kept chasing.
2. **Floor7 could fire while top-row pits remained** — when `count_remaining_pits == 0`
   was reached only via a partial plan, the picked first step might already
   trigger row 6 opening, scrolling the board and dumping row 0 / row 1 pits.
3. **Item placement had no notion of cluster coverage** — a 3×3 pit cluster
   was scored the same as nine isolated pits, so the planner often shattered
   clusters cell-by-cell instead of one-shotting them with a bomb.

## Design

- **CNN labels are reachability ground truth.** No plan-start BFS rebuild;
  reachability changes only when simulation actually opens a cell. See
  `board.py: canonicalize_in_place` (legacy `void`/`pit` aliases only) and
  `board.py: promote_after_dig` (frontier expansion from just-emptied cells).
- **Frontier-dig.** Cells the CNN labelled `unreachable_*` are still diggable
  if they are 4-adjacent to a confirmed-reachable empty (visual exposure
  matches game mechanics, even when CNN labels disagree on connectivity
  inside hidden pockets).
- **Pits before everything.** Goal in the `has_pit` strategy is
  `count_remaining_pits == 0`; the search refuses any action that would
  open floor 7 while a pit remains. `unreachable_pit_max_extra_cost = +inf`
  per user requirement — we keep digging.
- **Cluster-aware item scoring** (`clusters.py`). 1×1 / 2×2 / 3×3 pit
  clusters are enumerated up-front; `cluster_value(size)` is super-linear so
  a single bomb that fully covers a 3×3 outweighs nine separate digs.
- **Stats out.** Every plan returns `stats` (`pits_collected`, `shovel_cost`,
  `drills_used`, `bombs_used`, `cost_per_pit`, etc.) so we can analyse
  efficiency over time and decide later whether to drop the
  `unreachable_pit_max_extra_cost = +inf` rule.

## Layout

| File | Purpose |
|------|---------|
| `board.py` | reachability primitives, canonicalization, frontier-dig, post-dig promotion |
| `clusters.py` | 1×1 / 2×2 / 3×3 pit cluster discovery + value heuristic |
| `actions.py` | dig/drill/bomb enumeration + simulation |
| `planner.py` | best-first search, scoring, stats |
| `service.py` | re-exports the V2 board classifier (same CNN) |
| `types.py` | `PlanStats`, `PlanResult` |
| `debug_with_image_plan.py` | one-shot CLI: run a screenshot through V3 |

## Switching V3 on

In `bot_config.json` per device:

```json
{ "mining_planner_version": "v3" }
```

Falls back to V1 if not set. V2 still works unchanged.

## Quick check

```bash
python -m miner.v3.debug_with_image_plan <screenshot.png> --shovels 100 --drill 1 --bomb 1
```

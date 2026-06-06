"""Mining V2 workspace.

The v2 PLANNER (`plan_v2`) was removed 2026-06-05 (it violated the <300ms
per-step budget on 18.8% of real boards; see docs/MINING_ALGORITHM_ANALYSIS.md).
What remains here is shared infrastructure still used by v3/v4: the CNN
`BoardClassifierV2` (classifier.py), board capture/classification (service.py),
DTOs (types.py), and rendering (visualization.py).
"""

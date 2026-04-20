# Miner V2

This directory is the fresh start for mining logic.

Current scope:

- board classification
- dry-run planner
- no executor yet
- not wired into `new_main_v2.py`
- not wired into `miner/mining_service.py`

Planner rules in the current V2 draft:

- top-level strategy is `has_pit` vs `no_pit`
- only `reachable_pit` and `unreachable_pit` count as remaining pits
- `dug_pit` is treated as air
- `bomb` and `drill` are part of the main search, not a side evaluator
- `drill` only affects the visible board footprint
- `bomb` can contribute beyond the bottom of the visible board and may open floor7 from an edge placement
- action ordering prefers digging lower before using items when that improves placement value

Files:

- `classifier.py`: CNN-based board classifier for V2
- `planner.py`: rule-based dry-run planner for V2
- `service.py`: helpers to capture and classify a device screenshot
- `types.py`: board snapshot dataclasses
- `llm_judge.py`: OpenAI-compatible LLM review client for board snapshots
- `visualization.py`: text rendering for board output
- `debug_with_image.py`: standalone image debug entrypoint
- `debug_with_image_llm.py`: classify one image and send the snapshot to an LLM judge
- `debug_with_image_plan.py`: classify one image and generate a dry-run plan

Quick check:

```bash
python -m miner.v2.debug_with_image <screenshot.png>
```

LLM check:

```bash
python -m miner.v2.debug_with_image_llm <screenshot.png>
```

If the model is vision-capable:

```bash
python -m miner.v2.debug_with_image_llm <screenshot.png> --with-image
```

Planner check:

```bash
python -m miner.v2.debug_with_image_plan <screenshot.png> --shovels 100 --drill 1 --bomb 1
```

Next intended work:

1. improve planner scoring and node pruning on complex pit boards
2. add replay/debug tools for side-by-side comparison with the old miner
3. evaluate when V2 is stable enough to connect to runtime behind a feature flag

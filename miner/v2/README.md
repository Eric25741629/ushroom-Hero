# Miner V2

> **The V2 PLANNER (`plan_v2`) was removed 2026-06-05.** Real-board replay showed
> it breached the <300ms per-step budget on 18.8% of live boards (max 1841ms),
> so only the best 3 planners are kept (v1/v3/v4, default v4). See
> [`docs/MINING_ALGORITHM_ANALYSIS.md`](../../docs/MINING_ALGORITHM_ANALYSIS.md).
>
> **This directory is retained as the shared CNN board-classification layer**
> that `miner/v3` and `miner/v4` depend on. It is no longer a planner.

Current scope (shared infrastructure):

- board classification (`BoardClassifierV2`)
- board capture + screenshot classification
- board snapshot DTOs + text rendering

Files (retained):

- `classifier.py`: CNN-based board classifier — **used live by v3/v4**
- `service.py`: helpers to capture and classify a device screenshot — used by v3
- `types.py`: board snapshot dataclasses
- `llm_judge.py`: OpenAI-compatible LLM review client for board snapshots (debug)
- `visualization.py`: text rendering for board output
- `debug_with_image.py`: standalone image classify entrypoint
- `debug_with_image_llm.py`: classify one image and send the snapshot to an LLM judge

Removed: `planner.py` (`plan_v2`), `debug_with_image_plan.py`.

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

For planner debugging, use the kept planners (v3/v4):

```bash
python -m miner.v3.debug_with_image_plan <screenshot.png>
```

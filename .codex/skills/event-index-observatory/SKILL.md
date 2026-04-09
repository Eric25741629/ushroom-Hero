---
name: event-index-observatory
description: Build and operate the event tracing/indexing/GUI workflow for this repo, including action trace interpretation, event index generation, and reviewer-ready outputs for LLM analysis.
---

# Event Index Observatory

Use this skill when the user asks to:
- inspect or improve action/screenshot tracing quality
- generate reviewer-ready event index data
- run or enhance the event-index GUI
- prepare structured inputs for LLM postmortem/review

## Scope

This skill is specific to the `菇勇者全自動掛機` repository workflow:
- Action trace source: `logs/action_trace/<device>/events.jsonl`
- Smart screenshot source: `logs/error_screenshots/<device>/events.jsonl`
- Unified index builder: `tools/build_event_index.py`
- GUI viewer: `tools/event_index_gui.py`
- Dev guide: `docs/EVENT_INDEX_DEV_GUIDE.md`

## Default Workflow

1. Ensure index is fresh:
```powershell
python tools/build_event_index.py --days 7
```

2. If user wants interactive inspection:
```powershell
python tools/event_index_gui.py
```
Open `http://127.0.0.1:5088`.

3. If user wants machine-readable review input:
- Use latest `reports/event_index/event_index_*.jsonl`
- Keep fields unchanged from builder output contract

## Output Contract (Do Not Break)

Required index fields:
- `event_time`, `device_id`, `event_type`, `meaning`
- `caller_file`, `caller_line`, `caller_function`
- `task`, `step`, `status`
- `actor`, `source`, `payload_json`
- `screenshot_path`
- `trigger_file`, `trigger_line`, `trigger_function`

## Guardrails

- Do not guess screenshot linkage. Only use explicit paths from events.
- Preserve append-only semantics of raw logs.
- Keep backward compatibility for missing fields in old logs.
- Prefer adding new optional fields over renaming/removing existing ones.

## Common Tasks

### Add a new metric

1. Implement in `tools/build_event_index.py` as derived field.
2. Keep original raw fields intact.
3. Surface in GUI only if it helps triage.

### Improve meaning quality

1. Update auto meaning logic in `device_wrapper.py` (`MonitoredDevice._auto_meaning`).
2. Keep manual `trace_meaning` override priority.
3. Validate with sampled records from `logs/action_trace`.

### Prepare LLM batch review package

1. Build fresh index.
2. Filter by device/time/risk hotspots.
3. Export compact JSONL slice preserving line/path fields.


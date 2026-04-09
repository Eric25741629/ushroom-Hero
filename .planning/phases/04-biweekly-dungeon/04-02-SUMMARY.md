# SUMMARY - Plan 04-02

## What was built
- Added `run_biweekly_bounty_road_single(...)` in `new_battle.py` with deterministic combat loop exit conditions:
  - max duration guard
  - max idle cycle guard
  - external stop callback guard
- Added fail-safe `_recover_to_home(...)` fallback chain on exceptions.
- Added structured failure/success logging fields (`ts`, `device_id`, `run_id`, `trigger_slot`, `error_code`, `recovery_result`).

## Tests
- `tests/test_instance_flow_guards.py`

## Notes
- Flow implementation follows your provided script path (`賞金之路 -> 大盜來襲 -> 挑戰 -> 開啟自動戰鬥 -> 補給循環`) with safety guards.

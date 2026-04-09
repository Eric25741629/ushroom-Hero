---
phase: 10
status: passed
updated: 2026-03-15
score: 4/4
---

# Phase 4 Verification

## Goal Check
Phase 4 goal: biweekly instance MVP with correct schedule trigger, guarded click steps, safe combat loop exit, and failure recovery logging.

## Must-Haves
- [x] Scheduler trigger correctness (Sat/Sun 20:00 window with slot dedupe)
- [x] Critical click retry and timeout guards
- [x] Combat loop safe exit conditions (duration/idle/external stop)
- [x] Failure recovers to home with structured logs

## Evidence
- Code:
  - `new_battle.py`: `_compute_biweekly_slot_key`, `_safe_click_step`, `_recover_to_home`, `run_biweekly_bounty_road_single`
  - `new_main_v2.py`: Phase 4 schedule hook
- Tests:
  - `pytest -q tests/test_biweekly_scheduler.py tests/test_instance_flow_guards.py`
  - Result: `4 passed, 1 skipped`

## Residual Risks
- Device/OCR environment variance may still affect runtime success under heavy lag.
- `uiautomator2` is required for full integration tests in this environment.

## Conclusion
Verification passed for MVP scope.

---
phase: 10
slug: 20-00
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-15
---

# Phase 4 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | pytest.ini (if present) |
| **Quick run command** | `pytest -q tests/test_biweekly_scheduler.py tests/test_instance_flow_guards.py` |
| **Full suite command** | `pytest -q` |
| **Estimated runtime** | ~60-180 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest -q tests/test_biweekly_scheduler.py tests/test_instance_flow_guards.py`
- **After every plan wave:** Run `pytest -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 180 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | SCH-01 | unit | `pytest -q tests/test_biweekly_scheduler.py::test_trigger_window_and_weekday` | no (W0) | pending |
| 04-01-02 | 01 | 1 | SCH-01 | unit | `pytest -q tests/test_biweekly_scheduler.py::test_slot_dedupe_persistence` | no (W0) | pending |
| 04-01-03 | 01 | 1 | STAB-02 | integration | `pytest -q tests/test_instance_flow_guards.py::test_safe_click_retry_timeout` | no (W0) | pending |
| 04-02-01 | 02 | 2 | STAB-02 | integration | `pytest -q tests/test_instance_flow_guards.py::test_combat_loop_exit_conditions` | no (W0) | pending |
| 04-02-02 | 02 | 2 | STAB-04 | integration | `pytest -q tests/test_instance_flow_guards.py::test_fail_safe_recovery_to_home` | no (W0) | pending |
| 04-02-03 | 02 | 2 | STAB-04 | unit | `pytest -q tests/test_instance_flow_guards.py::test_structured_failure_logging_fields` | no (W0) | pending |

---

## Wave 0 Requirements

- [x] Existing infrastructure covers all phase requirements.
- [ ] Add `tests/test_biweekly_scheduler.py` if missing.
- [ ] Add `tests/test_instance_flow_guards.py` if missing.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Sat/Sun 20:00 real trigger (Asia/Taipei) | SCH-01 | Depends on real wall-clock and runtime scheduler integration | Set system/test clock to Sat/Sun 19:59 -> 20:01 and confirm single start per slot |
| OCR jitter under real device load | STAB-02 | Emulator/device rendering variability hard to fully mock | Inject missing button text and delayed UI states; confirm retry/timeout escalation |

---

## Validation Sign-Off

- [x] All tasks have automated verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 180s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

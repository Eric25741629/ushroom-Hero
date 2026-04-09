---
phase: 01
slug: mumu-control-exe-launch-shutdown-restart-show-window-hide-window-emulator
status: draft
nyquist_compliant: false
wave_0_complete: true
created: 2026-03-13
---

# Phase 1 - Validation Strategy

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pytest.ini` (if exists) |
| **Quick run command** | `pytest -q tests/test_mumu_control.py tests/test_emulator_watchdog.py tests/test_emulator_recovery.py` |
| **Full suite command** | `pytest -q` |
| **Estimated runtime** | ~120 seconds |

## Sampling Rate
- After every task commit: run quick command.
- After every wave: run full suite command.
- Before phase verification: full suite must be green.
- Max feedback latency: 180 seconds.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | PHASE-01 | unit | `pytest -q tests/test_mumu_control.py::test_resolve_emulator_index` | ⏳ | pending |
| 01-01-02 | 01 | 1 | PHASE-01 | unit | `pytest -q tests/test_mumu_control.py::test_build_commands` | ⏳ | pending |
| 01-01-03 | 01 | 1 | PHASE-01 | unit | `pytest -q tests/test_emulator_watchdog.py::test_hang_detection_rules` | ⏳ | pending |
| 01-02-01 | 02 | 2 | PHASE-01 | integration | `pytest -q tests/test_emulator_recovery.py::test_restart_and_recover` | ⏳ | pending |
| 01-02-02 | 02 | 2 | PHASE-01 | integration | `pytest -q tests/test_emulator_recovery.py::test_post_restart_health_check` | ⏳ | pending |
| 01-02-03 | 02 | 2 | PHASE-01 | integration | `pytest -q tests/test_mumu_control.py::test_metrics_exposed` | ⏳ | pending |

## Wave 0 Requirements
- [x] Existing pytest infrastructure is sufficient for this phase.

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| MuMu 真機 restart 成功率 | PHASE-01 | 需真實模擬器環境 | 以 `emulator-5554` 注入卡死場景，觀察 watchdog 觸發與恢復 |
| control.exe 路徑差異容錯 | PHASE-01 | 需多台機器環境 | 在公司/宿舍主機驗證路徑探測與 fallback 設定 |

## Validation Sign-Off
- [x] All tasks mapped to automated or manual verification.
- [x] Sampling continuity designed.
- [x] No watch-mode flags.
- [x] Feedback latency target < 180s.
- [ ] `nyquist_compliant: true` set after implementation confirms test coverage.

**Approval:** pending

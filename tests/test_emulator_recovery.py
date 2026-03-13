import subprocess

import pytest

from utils.emulator_recovery import EmulatorRecoveryOrchestrator
from utils.emulator_watchdog import HangDetector, WatchdogSample
from utils.mumu_control import MuMuController


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")


def _sample(ts: float, hb: float, h: str, adb_ok: bool) -> WatchdogSample:
    return WatchdogSample(timestamp=ts, heartbeat_ts=hb, screenshot_hash=h, adb_ok=adb_ok)


def test_restart_and_recover():
    runner = _Runner()
    ctrl = MuMuController("control.exe", runner=runner)
    detector = HangDetector(heartbeat_timeout_sec=10, frozen_frame_window_sec=10, adb_timeout_strikes=2)

    orch = EmulatorRecoveryOrchestrator(
        controller=ctrl,
        detector=detector,
        health_check=lambda _serial: True,
        max_restarts_per_hour=3,
        cooldown_sec=1,
    )

    assert orch.check_and_recover("emulator-5554", _sample(100, 100, "a", True)).reason == "healthy"
    orch.check_and_recover("emulator-5554", _sample(115, 90, "same", True))
    result = orch.check_and_recover("emulator-5554", _sample(126, 90, "same", True))

    assert result.restarted is True
    assert result.restart_ok is True
    assert any("restart" in " ".join(c) for c in runner.calls)


def test_restart_throttled_when_over_limit():
    runner = _Runner()
    ctrl = MuMuController("control.exe", runner=runner)
    detector = HangDetector(heartbeat_timeout_sec=1, frozen_frame_window_sec=1, adb_timeout_strikes=1)
    orch = EmulatorRecoveryOrchestrator(
        controller=ctrl,
        detector=detector,
        health_check=lambda _serial: True,
        max_restarts_per_hour=1,
        cooldown_sec=0,
    )

    # warmup
    orch.check_and_recover("emulator-5554", _sample(10, 10, "x", True))

    # first hung event should consume the single allowed restart
    first = orch.check_and_recover("emulator-5554", _sample(12, 1, "same", False))
    assert first.restarted is True

    # later hung event in same hour should be throttled
    second = orch.check_and_recover("emulator-5554", _sample(20, 1, "same", False))
    assert second.reason == "restart_throttled"
    assert second.restarted is False

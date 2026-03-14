import time
import logging
from dataclasses import dataclass
from typing import Callable

from utils.emulator_watchdog import HangDetector, WatchdogSample
from utils.mumu_control import MuMuController


_logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    # 是否實際觸發了 restart
    restarted: bool
    # 狀態原因（healthy / hung_detected / restart_throttled）
    reason: str
    # restart 後是否健康恢復
    restart_ok: bool
    # 本次處理耗時（秒）
    duration_sec: float


class EmulatorRecoveryOrchestrator:
    """模擬器自動恢復協調器。"""

    def __init__(
        self,
        controller: MuMuController,
        detector: HangDetector,
        health_check: Callable[[str], bool],
        max_restarts_per_hour: int = 3,
        cooldown_sec: int = 90,
    ) -> None:
        self.controller = controller
        self.detector = detector
        self.health_check = health_check
        self.max_restarts_per_hour = max_restarts_per_hour
        self.cooldown_sec = cooldown_sec
        self._restart_timestamps: dict[str, list[float]] = {}

        _logger.info(
            "MuMu recovery uses executable: %s",
            getattr(self.controller, "control_exe_path", "<unknown>"),
        )

    def _can_restart(self, serial: str, now: float) -> bool:
        arr = self._restart_timestamps.setdefault(serial, [])
        arr[:] = [t for t in arr if now - t <= 3600]

        if arr and (now - arr[-1]) < self.cooldown_sec:
            return False

        return len(arr) < self.max_restarts_per_hour

    def check_and_recover(self, serial: str, sample: WatchdogSample) -> RecoveryResult:
        started = time.time()

        if not self.detector.is_hung(sample):
            return RecoveryResult(False, "healthy", True, time.time() - started)

        if not self._can_restart(serial, sample.timestamp):
            return RecoveryResult(False, "restart_throttled", False, time.time() - started)

        action = self.controller.restart(serial)
        if action.ok:
            self._restart_timestamps[serial].append(sample.timestamp)

        healthy_after = self.health_check(serial) if action.ok else False
        return RecoveryResult(
            restarted=bool(action.ok),
            reason="hung_detected",
            restart_ok=bool(action.ok and healthy_after),
            duration_sec=time.time() - started,
        )

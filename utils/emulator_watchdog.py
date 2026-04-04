import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class WatchdogSample:
    timestamp: float
    heartbeat_ts: float
    screenshot_hash: Optional[str]
    adb_ok: bool


class HangDetector:
    """Detect emulator hang by combining heartbeat staleness, static frame and ADB health."""

    def __init__(self, heartbeat_timeout_sec: int = 90, frozen_frame_window_sec: int = 90, adb_timeout_strikes: int = 2):
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.frozen_frame_window_sec = frozen_frame_window_sec
        self.adb_timeout_strikes = adb_timeout_strikes
        self._prev: Optional[WatchdogSample] = None
        self._adb_fail_count = 0

    def is_hung(self, sample: WatchdogSample) -> bool:
        now = sample.timestamp or time.time()
        heartbeat_stale = (now - sample.heartbeat_ts) >= self.heartbeat_timeout_sec

        frozen_frame = False
        if self._prev and self._prev.screenshot_hash and sample.screenshot_hash:
            same_hash = self._prev.screenshot_hash == sample.screenshot_hash
            long_enough = (now - self._prev.timestamp) >= self.frozen_frame_window_sec
            frozen_frame = same_hash and long_enough

        if sample.adb_ok:
            self._adb_fail_count = 0
        else:
            self._adb_fail_count += 1

        adb_unhealthy = self._adb_fail_count >= self.adb_timeout_strikes
        self._prev = sample

        # Require stale heartbeat and one additional strong signal.
        return heartbeat_stale and (frozen_frame or adb_unhealthy)

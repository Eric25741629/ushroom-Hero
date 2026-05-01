"""In-memory MonitoredDevice substitute for harness unit tests.

Records every interaction so tests can assert on what the harness drove
the device to do, and lets tests pre-script a stage sequence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FakeDevice:
    ip: str = "fake-device"
    stages: list[str] = field(default_factory=lambda: ["主頁面"])
    clicks: list[tuple[int, int]] = field(default_factory=list)
    swipes: list[tuple[int, int, int, int]] = field(default_factory=list)

    def screenshot(self, format: str = "opencv") -> np.ndarray:
        return np.zeros((960, 540, 3), dtype=np.uint8)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def tap(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self.swipes.append((x1, y1, x2, y2))

    def app_stop(self, _pkg: str) -> None:
        pass

    def info(self) -> dict[str, Any]:
        return {"package": "com.mxdzz.tw.and"}

    def pop_stage(self) -> str:
        if len(self.stages) > 1:
            return self.stages.pop(0)
        return self.stages[0]

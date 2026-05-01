"""Harness-level exceptions."""
from __future__ import annotations


class HarnessError(Exception):
    pass


class NavigationFailed(HarnessError):
    def __init__(self, target: str, reason: str):
        super().__init__(f"navigate to {target} failed: {reason}")
        self.target = target
        self.reason = reason


class TaskTimeout(HarnessError):
    pass

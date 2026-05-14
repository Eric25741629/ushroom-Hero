"""Centralised filesystem layout for the bot's log artifacts.

All log paths flow through `LogPaths` so we have a single place to evolve
the directory structure (e.g. `logs/<device>/main.log` vs the legacy
`logs/<device>.log`).

Used by:
- `utils.logging_utils` (main / miner / ocr_trace loggers)
- `utils.smart_screenshot.SmartScreenshotRecorder` (error_screenshots/)
- `utils.action_tracker.ActionTraceRecorder` (action_trace/)
- `tools.migrate_logs_layout` (one-shot migrator)
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def _safe(name: str) -> str:
    return str(name).replace(":", "_").replace(" ", "_")


class LogPaths:
    """Static layout helpers. Use `with_root(tmp)` for tests."""

    ROOT: Path = Path("logs")

    @staticmethod
    def safe_device_id(device_id: str) -> str:
        return _safe(device_id)

    @classmethod
    def device_root(cls, device_id: str) -> Path:
        return cls.ROOT / _safe(device_id)

    @classmethod
    def main_log(cls, device_id: str) -> Path:
        return cls.device_root(device_id) / "main.log"

    @classmethod
    def miner_log(cls, device_id: str) -> Path:
        return cls.device_root(device_id) / "miner.log"

    @classmethod
    def ocr_trace_log(cls, device_id: str) -> Path:
        return cls.device_root(device_id) / "ocr_trace.log"

    @classmethod
    def error_screenshots_dir(cls, device_id: str) -> Path:
        return cls.device_root(device_id) / "error_screenshots"

    @classmethod
    def action_trace_dir(cls, device_id: str) -> Path:
        return cls.device_root(device_id) / "action_trace"

    @classmethod
    def system_log(cls, name: str) -> Path:
        return cls.ROOT / "system" / f"{name}.log"

    @classmethod
    def archive_dir(cls) -> Path:
        return cls.ROOT / "_archive"

    @classmethod
    def with_root(cls, root: PathLike) -> "_ScopedLogPaths":
        return _ScopedLogPaths(Path(root))


class _ScopedLogPaths:
    """LogPaths bound to a non-default root (test sandboxes)."""

    def __init__(self, root: Path) -> None:
        self.ROOT = root

    def safe_device_id(self, device_id: str) -> str:
        return _safe(device_id)

    def device_root(self, device_id: str) -> Path:
        return self.ROOT / _safe(device_id)

    def main_log(self, device_id: str) -> Path:
        return self.device_root(device_id) / "main.log"

    def miner_log(self, device_id: str) -> Path:
        return self.device_root(device_id) / "miner.log"

    def ocr_trace_log(self, device_id: str) -> Path:
        return self.device_root(device_id) / "ocr_trace.log"

    def error_screenshots_dir(self, device_id: str) -> Path:
        return self.device_root(device_id) / "error_screenshots"

    def action_trace_dir(self, device_id: str) -> Path:
        return self.device_root(device_id) / "action_trace"

    def system_log(self, name: str) -> Path:
        return self.ROOT / "system" / f"{name}.log"

    def archive_dir(self) -> Path:
        return self.ROOT / "_archive"

"""V3 reuses the v2 board classifier — same CNN, same crop config."""
from __future__ import annotations

from typing import Any

from miner.v2.classifier import BoardClassifierV2
from miner.v2.service import capture_board_snapshot, classify_frame  # re-export

__all__ = ["BoardClassifierV2", "capture_board_snapshot", "classify_frame"]

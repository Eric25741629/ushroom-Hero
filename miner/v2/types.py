from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

Board = List[List[str]]
ConfidenceGrid = List[List[float]]


@dataclass(frozen=True)
class ScreenCheckResult:
    passed: bool
    matched_points: int


@dataclass
class BoardSnapshot:
    board: Board
    confidences: ConfidenceGrid
    captured_at: str
    image_shape: Tuple[int, ...]
    grid_config: Dict[str, int]
    screen_check: Optional[ScreenCheckResult] = None

    @property
    def rows(self) -> int:
        return len(self.board)

    @property
    def cols(self) -> int:
        return len(self.board[0]) if self.board else 0

    @property
    def min_confidence(self) -> float:
        values = [value for row in self.confidences for value in row]
        return min(values) if values else 0.0

    @property
    def avg_confidence(self) -> float:
        values = [value for row in self.confidences for value in row]
        return sum(values) / len(values) if values else 0.0

"""農場狀態定義"""

from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


class FarmState(Enum):
    IDLE = auto()
    IN_SHOP = auto()
    BUYING_SEED = auto()
    IN_FARM = auto()
    FERTILIZING = auto()
    PLANTING = auto()
    WEEKLY_CARD = auto()
    HARVESTING = auto()
    RETURNING_HOME = auto()
    HARVEST_CARD = auto()
    ERROR = auto()


@dataclass
class FarmContext:
    device_ip: str
    current_state: FarmState = FarmState.IDLE

    should_buy_seed: bool = False
    plant_count_today: int = 0

    daily_count: int = 0
    is_same_day: bool = True

    weekly_card_done: bool = False

    save_time: float = 0.0

    last_record: Optional[dict] = None

    def can_plant(self) -> bool:
        return self.plant_count_today < 2

    def to_dict(self) -> dict:
        return {
            "device_ip": self.device_ip,
            "state": self.current_state.name,
            "plant_count": self.plant_count_today,
            "should_buy_seed": self.should_buy_seed,
            "weekly_card_done": self.weekly_card_done,
        }

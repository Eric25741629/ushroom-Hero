"""FamilyMarketDataManager — 家族市場數據管理器。"""

import datetime
from typing import Optional, Tuple

from .base import _safe_cast, JsonDataManager, PurchaseCountState


class FamilyMarketDataManager(JsonDataManager):
    """家族市場數據管理器，統一處理家族每日/每週購買紀錄"""

    MODE_KEY_MAP = {
        "daily": ("family_market_timestamp", "family_market_buy_num"),
        "weekly": ("week_family_market_timestamp", "week_family_market_buy_num"),
    }

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def _get_keys(self, mode: str) -> Tuple[str, str]:
        if mode not in self.MODE_KEY_MAP:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")
        return self.MODE_KEY_MAP[mode]

    def get_state(self, mode: str) -> PurchaseCountState:
        timestamp_key, buy_key = self._get_keys(mode)
        return PurchaseCountState.from_data(self.load_data(), timestamp_key, buy_key)

    def set_state(self, mode: str, buy_num: int, timestamp: Optional[float] = None) -> None:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()
        state = PurchaseCountState(
            timestamp=float(timestamp) if timestamp is not None
            else datetime.datetime.now(self.timezone).timestamp(),
            buy_num=_safe_cast(buy_num, int, 0),
        )
        state.apply_to(data, timestamp_key, buy_key)
        self.save_data(data)

    def reset_state(self, mode: str) -> None:
        self.set_state(mode, buy_num=0, timestamp=0.0)

    def increment_buy_num(self, mode: str, increment: int = 1) -> PurchaseCountState:
        timestamp_key, buy_key = self._get_keys(mode)
        data = self.load_data()
        state = PurchaseCountState.from_data(data, timestamp_key, buy_key)
        state.buy_num += _safe_cast(increment, int, 0)
        state.timestamp = datetime.datetime.now(self.timezone).timestamp()
        state.apply_to(data, timestamp_key, buy_key)
        self.save_data(data)
        return state

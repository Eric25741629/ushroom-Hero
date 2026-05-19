"""ParkMarketDataManager — 停車場市場數據管理器。"""

import datetime
from typing import Any, Dict, Tuple

from .base import (
    _safe_cast,
    _ts_same_day,
    _ts_same_week,
    JsonDataManager,
    MarketState,
)


class ParkMarketDataManager(JsonDataManager):
    """停車場市場數據管理器，專門處理停車相關的JSON數據"""

    SECTION_KEYS = ("daily", "weekly")

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def get_default_structure(self) -> Dict[str, Any]:
        template = MarketState().to_payload()
        return {key: template.copy() for key in self.SECTION_KEYS}

    def load_data(self) -> Dict[str, Any]:
        data = super().load_data({})
        if self._normalize_sections(data):
            self.save_data(data)
        return data

    def get_buy_data(self) -> Tuple[float, int, float, int, int, int]:
        states = self._load_states()
        daily = states["daily"]
        weekly = states["weekly"]
        if daily.timestamp > 0 and not _ts_same_day(daily.timestamp, self.timezone):
            daily = MarketState(timestamp=daily.timestamp)
        if weekly.timestamp > 0 and not _ts_same_week(weekly.timestamp, self.timezone):
            weekly = MarketState(timestamp=weekly.timestamp)
        return (
            daily.timestamp,
            daily.buy_num,
            weekly.timestamp,
            weekly.buy_num,
            daily.check_time,
            weekly.check_time,
        )

    def _normalize_sections(self, data: Dict[str, Any]) -> bool:
        changed = False
        for key in self.SECTION_KEYS:
            original = data.get(key)
            normalized = MarketState.from_payload(original).to_payload()
            if normalized != original:
                data[key] = normalized
                changed = True
        return changed

    def _load_states(self) -> Dict[str, MarketState]:
        data = self.load_data()
        return {key: MarketState.from_payload(data.get(key)) for key in self.SECTION_KEYS}

    def _is_same_day_from_timestamp(self, timestamp: float) -> bool:
        return _ts_same_day(timestamp, self.timezone)

    def _is_same_week_from_timestamp(self, timestamp: float) -> bool:
        return _ts_same_week(timestamp, self.timezone)

    def record_purchase(self, mode: str, buy_num: int, check_time: int = 0) -> None:
        if mode not in self.SECTION_KEYS:
            raise ValueError("mode 必須是 'daily' 或 'weekly'")
        try:
            data = self.load_data()
            state = MarketState.from_payload(data.get(mode))
            now_ts = datetime.datetime.now(self.timezone).timestamp()
            buy_inc = _safe_cast(buy_num, int, 0)
            check_inc = _safe_cast(check_time, int, 0)
            if mode == "daily":
                should_reset = not _ts_same_day(state.timestamp, self.timezone)
            else:
                should_reset = not _ts_same_week(state.timestamp, self.timezone)
            if should_reset:
                state.buy_num = buy_inc
                state.check_time = check_inc
                print(f"{mode} 數據已重置，新的購買數量: {state.buy_num}, 新的檢查次數: {state.check_time}")
            else:
                state.buy_num += buy_inc
                state.check_time += check_inc
                print(f"{mode} 數據累加，總購買數量: {state.buy_num}, 總檢查次數: {state.check_time}")
            state.timestamp = float(now_ts)
            data[mode] = state.to_payload()
            self.save_data(data)
            print(f"已記錄 {mode} 購買數據，總數量: {state.buy_num}, 總檢查次數: {state.check_time}")
        except Exception as e:
            print(f"記錄購買數據失敗: {e}")

    def should_purchase(self, mode: str, max_purchases: int = 2, max_checks: int = 2) -> bool:
        """Deprecated: use game_actions.shop_manager.should_purchase() instead."""
        from game_actions.shop_manager import should_purchase as _impl
        return _impl(self, mode, max_purchases, max_checks)

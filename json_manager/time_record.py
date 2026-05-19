"""TimeRecordDataManager — 行為時間記錄管理器。"""

import datetime
from typing import Any, Dict, Optional

from .base import _safe_cast, JsonDataManager


class TimeRecordDataManager(JsonDataManager):
    """時間記錄數據管理器，處理各種行為的時間記錄"""

    def __init__(self, device_id: str):
        super().__init__(device_id, file_suffix="")

    def record_time(self, name: str) -> None:
        self.record_timestamp(name)

    def get_time_record(self, name: str) -> Optional[Dict[str, Any]]:
        """獲取時間記錄，兼容舊格式（scalar timestamp / dict）。"""
        record = self.get_record(name)
        if record is None:
            return None

        if isinstance(record, (int, float, str)):
            ts = _safe_cast(record, float, 0.0)
            if ts <= 0:
                return None
            try:
                rec_dt = datetime.datetime.fromtimestamp(ts, self.timezone)
                now = datetime.datetime.now(self.timezone).date()
                rec_date = rec_dt.date()
                return {
                    "timestamp": ts,
                    "recorded_date": rec_dt.strftime("%Y-%m-%d"),
                    "is_next_day": rec_date != now,
                    "is_next_week": rec_date.isocalendar()[:2] != now.isocalendar()[:2],
                }
            except (OSError, OverflowError, ValueError):
                return None

        if not isinstance(record, dict):
            return None

        ts = _safe_cast(record.get("timestamp"), float, 0.0)
        date_str = record.get("date")

        rec_date: Optional[datetime.date] = None
        if date_str:
            try:
                rec_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                rec_date = None
        if rec_date is None and ts > 0:
            try:
                rec_dt = datetime.datetime.fromtimestamp(ts, self.timezone)
                date_str = rec_dt.strftime("%Y-%m-%d")
                rec_date = rec_dt.date()
            except (OSError, OverflowError, ValueError):
                pass

        if rec_date is None or ts <= 0:
            return None

        now = datetime.datetime.now(self.timezone).date()
        return {
            "timestamp": ts,
            "recorded_date": date_str,
            "is_next_day": rec_date != now,
            "is_next_week": rec_date.isocalendar()[:2] != now.isocalendar()[:2],
        }

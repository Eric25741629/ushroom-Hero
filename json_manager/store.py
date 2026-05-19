"""StoreDataManager — 商店購買記錄管理器，過渡期支援多種時間欄位格式。"""

import copy
import datetime
import warnings
from typing import Any, Dict, Optional

import pytz
from pytz import exceptions as pytz_exceptions

from .base import _safe_cast, _TZ_TAIPEI, JsonDataManager


class StoreDataManager(JsonDataManager):
    """商店數據管理器，處理購買記錄（過渡期相容舊欄位）"""

    def __init__(self, device_id: str, timezone_str: str = "Asia/Taipei"):
        super().__init__(device_id, file_suffix="")
        try:
            self.timezone = pytz.timezone(timezone_str)
        except Exception:
            self.timezone = _TZ_TAIPEI

    def record_purchase(self, title: str, content: Dict[str, Any]) -> None:
        """記錄購買行為（統一 UTC 儲存，並寫入相容欄位）。"""
        data = self.load_data()
        payload = copy.deepcopy(content)

        utc_now = datetime.datetime.now(datetime.timezone.utc)
        payload["last_time_utc"] = utc_now.isoformat().replace("+00:00", "Z")
        payload["last_time"] = utc_now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            payload["last_time_local"] = utc_now.astimezone(self.timezone).isoformat()
        except Exception:
            pass

        if "count" in payload:
            payload["count"] = _safe_cast(payload["count"], int, 1)
        else:
            old = data.get(title, {})
            payload["count"] = _safe_cast(old.get("count", 0), int, 0) + 1

        payload["_schema"] = "utc_v1"
        data[title] = payload
        self.save_data(data)
        print(f"已記錄購買項目 '{title}': {payload}")

    def get_purchase_record(self, title: str) -> Optional[Dict[str, Any]]:
        return self.get_record(title)

    def is_purchased_today(self, title: str, period: str = "day") -> bool:
        """period='day'（預設）或 'week'。"""
        record = self.get_purchase_record(title)
        print(record)
        if not record:
            return False
        try:
            local_dt = self._extract_last_time_as_local(record)
            if local_dt is None:
                return False
            now_local = datetime.datetime.now(self.timezone)
            if period == "week":
                rec_y, rec_w, _ = local_dt.date().isocalendar()
                now_y, now_w, _ = now_local.date().isocalendar()
                print(f"比較週：記錄=(Y{rec_y},W{rec_w})，現在=(Y{now_y},W{now_w})")
                return rec_y == now_y and rec_w == now_w
            print(f"比較日期：記錄={local_dt.date()}，現在={now_local.date()}")
            return local_dt.date() == now_local.date()
        except (ValueError, AttributeError, pytz_exceptions.PytzError):
            return False

    def _extract_last_time_as_local(self, record: Dict[str, Any]) -> Optional[datetime.datetime]:
        """讀取最後動作時間並轉成 self.timezone 的 aware datetime。優先順序 new→old。"""
        s = record.get("last_time_utc")
        if s:
            dt = self._parse_iso_as_utc(s)
            if dt:
                return dt.astimezone(self.timezone)
        s = record.get("last_time")
        if s:
            dt = self._parse_legacy_naive_as_utc(s)
            if dt:
                self._migrate_fill_fields(record, dt)
                return dt.astimezone(self.timezone)
        s = record.get("last_time_local")
        if s:
            dt = self._parse_iso_with_tz(s)
            if dt:
                self._migrate_fill_fields(record, dt.astimezone(datetime.timezone.utc))
                return dt.astimezone(self.timezone)
        s = record.get("time") or record.get("timestamp")
        if s:
            try:
                naive = datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
                local_dt = self.timezone.localize(naive)
                self._migrate_fill_fields(record, local_dt.astimezone(datetime.timezone.utc))
                return local_dt
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_iso_as_utc(s: str) -> Optional[datetime.datetime]:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_iso_with_tz(s: str) -> Optional[datetime.datetime]:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else None
        except Exception:
            return None

    @staticmethod
    def _parse_legacy_naive_as_utc(s: str) -> Optional[datetime.datetime]:
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )
        except Exception:
            return None

    def _migrate_fill_fields(self, record: Dict[str, Any], dt_utc: datetime.datetime) -> None:
        try:
            changed = False
            if not record.get("last_time_utc"):
                record["last_time_utc"] = dt_utc.isoformat().replace("+00:00", "Z")
                changed = True
            if not record.get("last_time"):
                record["last_time"] = dt_utc.strftime("%Y-%m-%d %H:%M:%S")
                changed = True
            try:
                local_dt = dt_utc.astimezone(self.timezone)
                if not record.get("last_time_local"):
                    record["last_time_local"] = local_dt.isoformat()
                    changed = True
            except Exception:
                pass
            if changed:
                record["_schema"] = "utc_v1"
                data = self.load_data()
                for k, v in data.items():
                    if v is record:
                        data[k] = record
                        self.save_data(data)
                        break
                warnings.warn(
                    "已自動移轉購買時間欄位到 UTC 標準格式（last_time_utc）。",
                    RuntimeWarning,
                )
        except Exception:
            pass

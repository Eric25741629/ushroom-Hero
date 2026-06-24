"""dashboard /api/daily_progress 徽章讀側：flat scalar record 也要被認得。

「每日任務」徽章讀 `mission_timestamp`，但它是 flat scalar float schema
（Mission.py 直接存數字，不是 dict）。舊讀側 `JsonDataManager.is_same_day`
只認 dict（含 timestamp/date 鍵），對 flat scalar float 永遠回 False
→ 即使今天剛寫過，徽章仍顯示未完成。

這裡測 `routes_status` 抽出的純判定函式 `_record_is_today(manager, data, key)`，
驗證它對三種 schema 都正確：dict timestamp / dict last_time / flat scalar float。
"""
import datetime
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# routes_status 拉 cv2 等重依賴；測純函式不需要它們。
sys.modules.setdefault("cv2", types.SimpleNamespace())
for _n in ("opencc", "paddleocr", "img_tools", "easyocr"):
    sys.modules.setdefault(_n, types.ModuleType(_n))

import pytz  # noqa: E402

from control_panel import routes_status  # noqa: E402

_TZ = pytz.timezone("Asia/Taipei")


class _FakeManager:
    """最小 JsonDataManager 替身：只需 timezone + is_same_day(讀 dict schema)。"""

    def __init__(self, data):
        self._data = data
        self.timezone = _TZ

    def is_same_day(self, name):
        rec = self._data.get(name)
        if not isinstance(rec, dict):
            return False
        ts = rec.get("timestamp")
        if ts is None:
            return False
        rec_date = datetime.datetime.fromtimestamp(float(ts), self.timezone).date()
        return rec_date == datetime.datetime.now(self.timezone).date()

    def is_same_week(self, name):
        rec = self._data.get(name)
        if not isinstance(rec, dict):
            return False
        ts = rec.get("timestamp")
        if ts is None:
            return False
        rec_d = datetime.datetime.fromtimestamp(float(ts), self.timezone).date()
        now_d = datetime.datetime.now(self.timezone).date()
        return rec_d.isocalendar()[:2] == now_d.isocalendar()[:2]


def _today_ts():
    return datetime.datetime.now(_TZ).timestamp()


def _yesterday_ts():
    return (datetime.datetime.now(_TZ) - datetime.timedelta(days=1)).timestamp()


def test_flat_scalar_today_is_recognized():
    """mission_timestamp = flat scalar float（今天）→ 徽章應認為已完成。"""
    data = {"mission_timestamp": _today_ts()}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "mission_timestamp") is True


def test_flat_scalar_yesterday_is_not_today():
    data = {"mission_timestamp": _yesterday_ts()}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "mission_timestamp") is False


def test_flat_scalar_zero_is_not_today():
    """初始值 0 / 未執行過 → 未完成。"""
    data = {"mission_timestamp": 0}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "mission_timestamp") is False


def test_dict_timestamp_today_still_works():
    """既有 dict schema（Store / farm_plant_click）讀側不可退化。"""
    data = {"Store": {"timestamp": _today_ts(), "date": "x"}}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "Store") is True


def test_dict_last_time_today_still_works():
    """family_market_timestamp 那種 last_time 字串 schema 仍要支援。"""
    today = datetime.datetime.now(_TZ).strftime("%Y-%m-%d")
    data = {"family_market_timestamp": {"last_time": f"{today} 08:00:00"}}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "family_market_timestamp") is True


def test_list_of_keys_any_match():
    """key 是 list 時，任一命中即算完成（家族任務雙 key）。"""
    data = {"family_market_timestamp": 0, "donate_family": {"timestamp": _today_ts()}}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(
        mgr, data, ["family_market_timestamp", "donate_family"]) is True


def test_missing_key_is_not_today():
    data = {}
    mgr = _FakeManager(data)
    assert routes_status._record_is_today(mgr, data, "mission_timestamp") is False


def test_week_predicate_lights_earlier_this_week_but_not_today():
    """航海/龍骸聖域用 period='week'：本週稍早跑過(非今天)仍要點亮。

    這正是修掉的雷 —— 多週活動跑完隔天，用 is_same_day 會變回 ⏳；
    改用 is_same_week 後整個檔期週都維持 ✅。
    """
    # 取「本週內、但不是今天」的一刻：往前推 1~3 天且仍同 ISO 週。
    now = datetime.datetime.now(_TZ)
    earlier = now - datetime.timedelta(days=1)
    if earlier.isocalendar()[:2] != now.isocalendar()[:2]:
        earlier = now + datetime.timedelta(days=1)  # 週一時改往後找同週日子
    data = {"sea_last_execution": {"timestamp": earlier.timestamp()}}
    mgr = _FakeManager(data)
    assert mgr.is_same_week("sea_last_execution") is True
    assert mgr.is_same_day("sea_last_execution") is False  # 不是今天

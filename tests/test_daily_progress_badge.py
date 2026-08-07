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
    """最小 JsonDataManager 替身：timezone + load_data + is_same_day/week。

    ``now`` 可注入,讓「本週非今天」這類判定不依賴真實時鐘(避免午夜邊界 flaky)。
    """

    def __init__(self, data, now=None):
        self._data = data
        self.timezone = _TZ
        self._now = now

    def load_data(self):
        return self._data

    def _now_date(self):
        return (self._now or datetime.datetime.now(self.timezone)).date()

    def is_same_day(self, name):
        rec = self._data.get(name)
        if not isinstance(rec, dict):
            return False
        ts = rec.get("timestamp")
        if ts is None:
            return False
        rec_date = datetime.datetime.fromtimestamp(float(ts), self.timezone).date()
        return rec_date == self._now_date()

    def is_same_week(self, name):
        rec = self._data.get(name)
        if not isinstance(rec, dict):
            return False
        ts = rec.get("timestamp")
        if ts is None:
            return False
        rec_d = datetime.datetime.fromtimestamp(float(ts), self.timezone).date()
        return rec_d.isocalendar()[:2] == self._now_date().isocalendar()[:2]


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


# ── _compute_daily_progress：實際 shipped 路徑(gate + period 述語)的端對端覆蓋 ──
#
# 航海日曆錨點 = 週一 2026-06-22、4 週一輪(見 json_manager.scheduling.is_sea_week)。
_SEA_WEEK_DAY = datetime.date(2026, 6, 22)      # 航海週(亦為龍骸週)
_OFF_WEEK_DAY = datetime.date(2026, 6, 29)      # 非航海週


def _stub_dragon(monkeypatch, is_week):
    """攔截 _compute_daily_progress 內 lazy import 的 _is_dragon_week,使其確定性。"""
    stub = types.ModuleType("game_actions.dragon_realm_scheduler")
    stub._is_dragon_week = lambda today=None: is_week
    monkeypatch.setitem(
        sys.modules, "game_actions.dragon_realm_scheduler", stub)


def _no_cycle_gate(monkeypatch):
    """坐騎/武道會的週期 gate 讀磁碟;測航海/龍骸時讓它一律可見(不關心)。"""
    monkeypatch.setattr(
        routes_status.json_manager, "_should_execute_cycle",
        lambda *a, **k: (True, True))


def _ts_on(day, hour=12):
    return datetime.datetime(day.year, day.month, day.day, hour, tzinfo=_TZ).timestamp()


def test_sea_badge_shown_and_lit_in_sea_week(monkeypatch):
    """航海週 + sea_last_execution 落在本週 → 航海徽章顯示且 ✅。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=True)
    now = datetime.datetime(2026, 6, 24, 12, tzinfo=_TZ)  # 航海週的週三
    data = {"sea_last_execution": {"timestamp": _ts_on(datetime.date(2026, 6, 22))}}
    mgr = _FakeManager(data, now=now)
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["航海"] is True


def test_sea_badge_lit_when_ran_earlier_this_week_not_today(monkeypatch):
    """修掉的雷:本週稍早跑過(非今天)仍要 ✅(period=week 用 is_same_week 而非 is_same_day)。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 24, 12, tzinfo=_TZ)        # 週三
    data = {"sea_last_execution": {"timestamp": _ts_on(datetime.date(2026, 6, 22))}}  # 週一
    mgr = _FakeManager(data, now=now)
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["航海"] is True                  # is_same_week → 整週維持亮
    assert mgr.is_same_day("sea_last_execution") is False  # 而當天述語會是暗


def test_sea_badge_hidden_off_week(monkeypatch):
    """非航海週 → 航海徽章直接隱藏(不出現在結果)。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    data = {"sea_last_execution": {"timestamp": _ts_on(datetime.date(2026, 6, 22))}}
    mgr = _FakeManager(data)
    res = routes_status._compute_daily_progress(mgr, "dev", today=_OFF_WEEK_DAY)
    assert "航海" not in res
    assert "龍骸聖域" not in res  # dragon stub False → 同樣隱藏


def test_dragon_badge_shown_in_dragon_week(monkeypatch):
    """龍骸週(stub True)→ 顯示;本週跑過 → ✅。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=True)
    now = datetime.datetime(2026, 6, 24, 12, tzinfo=_TZ)
    data = {"dragon_realm_last_run": {"timestamp": _ts_on(datetime.date(2026, 6, 24))}}
    mgr = _FakeManager(data, now=now)
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["龍骸聖域"] is True


def test_wanshen_badge_lit_when_ran_earlier_this_week_not_today(monkeypatch):
    """萬神試煉是週任務:本週稍早跑過(非今天)燈仍要亮(period=week,非 is_same_day)。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 24, 12, tzinfo=_TZ)              # 週三
    data = {"萬神試煉": {"timestamp": _ts_on(datetime.date(2026, 6, 22))}}  # 本週一跑
    mgr = _FakeManager(data, now=now)
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["萬神試煉"] is True                # is_same_week → 整週維持亮
    assert mgr.is_same_day("萬神試煉") is False   # 當天述語會是暗(舊 bug 來源)


# ── 坐騎衝刺：活動開放窗 gate(週二~週三22:00)。週期週內但活動結算後應隱藏 ──
# 2026-06-22 為週一 → 06-23 週二、06-24 週三、06-25 週四。
def _mount_data():
    return {"衝刺-發條": {"timestamp": _ts_on(datetime.date(2026, 6, 23))}}


def test_mount_sprint_badge_shown_tuesday(monkeypatch):
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 23, 12, tzinfo=_TZ)  # 週二
    mgr = _FakeManager(_mount_data())
    res = routes_status._compute_daily_progress(
        mgr, "dev", today=_SEA_WEEK_DAY, now=now)
    assert "坐騎衝刺" in res


def test_mount_sprint_badge_shown_wed_before_close(monkeypatch):
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 24, 21, tzinfo=_TZ)  # 週三 21:00
    mgr = _FakeManager(_mount_data())
    res = routes_status._compute_daily_progress(
        mgr, "dev", today=_SEA_WEEK_DAY, now=now)
    assert "坐騎衝刺" in res


def test_mount_sprint_badge_hidden_wed_after_close(monkeypatch):
    """週三22:00後活動結算 → 徽章隱藏(即使仍在週期週)。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 24, 22, tzinfo=_TZ)  # 週三 22:00
    mgr = _FakeManager(_mount_data())
    res = routes_status._compute_daily_progress(
        mgr, "dev", today=_SEA_WEEK_DAY, now=now)
    assert "坐騎衝刺" not in res


def test_mount_sprint_badge_hidden_thursday(monkeypatch):
    """非活動日(週四)→ 隱藏。"""
    _no_cycle_gate(monkeypatch)
    _stub_dragon(monkeypatch, is_week=False)
    now = datetime.datetime(2026, 6, 25, 12, tzinfo=_TZ)  # 週四
    mgr = _FakeManager(_mount_data())
    res = routes_status._compute_daily_progress(
        mgr, "dev", today=_SEA_WEEK_DAY, now=now)
    assert "坐騎衝刺" not in res


# ── 七日登入：固定每日徽章，今天領過即 ✅，沒領過 ⏳ ─────────────────────
def test_seven_login_badge_lit_when_recorded_today():
    data = {"七日登入": {"timestamp": _ts_on(datetime.date(2026, 6, 22)), "date": "x"}}
    mgr = _FakeManager(data, now=datetime.datetime(2026, 6, 22, 12, tzinfo=_TZ))
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["七日登入"] is True


def test_seven_login_badge_dim_when_not_recorded_today():
    data = {"七日登入": {"timestamp": _ts_on(datetime.date(2026, 6, 21)), "date": "x"}}
    mgr = _FakeManager(data, now=datetime.datetime(2026, 6, 22, 12, tzinfo=_TZ))
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["七日登入"] is False


def test_seven_login_badge_visible_without_record():
    mgr = _FakeManager({}, now=datetime.datetime(2026, 6, 22, 12, tzinfo=_TZ))
    res = routes_status._compute_daily_progress(mgr, "dev", today=_SEA_WEEK_DAY)
    assert res["七日登入"] is False

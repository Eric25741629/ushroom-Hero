"""龍骸聖域 WS 路徑「本活動週期已完成」冷卻測試。

Bug: WS 龍骸入口只查週期/時間窗，不查完成標記；到達三樓門後每輪 WS 都重跑
→ deadloop 空轉。修法：到達三樓門時寫入本活動週期完成標記（cycle-bound，
21 天週期後自動失效可重跑）；入口在週期/時間窗後加查完成標記，已完成 →
skipped，不建 WS session。

關鍵設計（使用者 2026-07-16 指定）：龍骸每 7 分 +1 體力、真人自己上三樓。
- reached_tier_three_gate → 標記完成（bot 到門就停、門前不再花體力，本週期無事可做）。
- out_of_stamina → 不標記（體力會回復，下一輪 WS 要繼續花體力）。
- deadloop / budget_exhausted → 不標記（失敗症狀 / 步數上限，都要能重跑）。
"""
from __future__ import annotations

import datetime

import pytest

import game_actions.dragon_realm_scheduler as sched


# 2026-07-15 (週三) 是龍骸活動週且在 10-22 窗口內（重現 adb-fc65396d 07-15 情境）。
# 錨點週一 2026-06-22 → 2026-07-13 週一整整 3 週後 → dragon week。
_WED = datetime.datetime(2026, 7, 15, 12, 0, 0)
_FRI_SAME_WEEK = datetime.datetime(2026, 7, 17, 11, 0, 0)
_NEXT_CYCLE = _WED + datetime.timedelta(days=21)  # 下一活動週期同一天


class _FakeStore:
    """以記憶體字典模擬 json_manager 的 time_recording/return_time。

    exercise 真正的 _cycle_completed / _mark_done 邏輯（週期數學 + 記錄解析），
    只把最底層儲存換成記憶體，不碰真實檔案。record 結構比照
    TimeRecordDataManager.record_timestamp。
    """

    def __init__(self, record_at: datetime.datetime):
        self.data: dict = {}
        self._record_at = record_at

    def time_recording(self, ip: str, name: str = "") -> None:
        n = self._record_at
        self.data[(ip, name)] = {
            "timestamp": n.timestamp(),
            "date": n.strftime("%Y-%m-%d"),
            "datetime": n.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def return_time(self, ip: str, name: str = ""):
        return self.data.get((ip, name))


@pytest.fixture
def store_at_wed(monkeypatch):
    store = _FakeStore(record_at=_WED)
    monkeypatch.setattr(sched, "time_recording", store.time_recording)
    monkeypatch.setattr(sched, "return_time", store.return_time)
    return store


# ---- scheduler 純函式：cycle-bound 完成標記 ----------------------------------

def test_cycle_completed_false_when_no_record(store_at_wed):
    assert sched._cycle_completed("dev", now=_WED) is False


def test_mark_done_then_completed_true_same_activity_week(store_at_wed):
    sched._mark_done("dev")  # 週三記錄
    # 同活動週（週五）→ 視為本週期已完成
    assert sched._cycle_completed("dev", now=_FRI_SAME_WEEK) is True


def test_cycle_completed_false_next_activity_cycle(store_at_wed):
    sched._mark_done("dev")  # 週三記錄
    # 21 天後的下一活動週期 → 標記失效，可重跑
    assert sched._cycle_completed("dev", now=_NEXT_CYCLE) is False


def test_cycle_completed_isolated_per_ip(store_at_wed):
    sched._mark_done("dev-a")
    assert sched._cycle_completed("dev-a", now=_WED) is True
    assert sched._cycle_completed("dev-b", now=_WED) is False


# ---- runner 入口：完成標記 gate + 依 stop_reason 決定是否標記 ----------------

class _FakeTracker:
    def __init__(self, keys: int = 0):
        self.counts = {1527: keys}  # KEY_ITEM=1527


@pytest.fixture
def runner_env(monkeypatch):
    """讓 runner._run_dragon_realm 通過週期/時間窗檢查，並攔截 run/mark_done。

    回傳 (runner, holder)；holder 為可變 dict：
      holder["reason"] 設定 fake dragon_realm.run 的回傳 stop_reason；
      holder["marks"]  記錄 _mark_done 被呼叫的 ip；
      holder["calls"]  記錄 dragon_realm.run 被呼叫次數。
    """
    import ws_token.runner as runner

    monkeypatch.setattr(sched, "_is_dragon_week", lambda *a, **k: True)
    monkeypatch.setattr(sched, "_within_open_window", lambda *a, **k: True)

    holder: dict = {"reason": "reached_tier_three_gate", "marks": [], "calls": []}
    monkeypatch.setattr(sched, "_mark_done", lambda ip: holder["marks"].append(ip))

    def fake_run(client, tracker, **kw):
        holder["calls"].append(True)
        return holder["reason"]

    monkeypatch.setattr(runner.dragon_realm, "run", fake_run)
    # 預設本週期未完成
    monkeypatch.setattr(sched, "_cycle_completed", lambda *a, **k: False)
    return runner, holder


def test_gate_reached_marks_cycle_done(runner_env):
    runner, holder = runner_env
    holder["reason"] = "reached_tier_three_gate"
    out = runner._run_dragon_realm(None, _FakeTracker(2), device="dev")
    assert out["stop_reason"] == "reached_tier_three_gate"
    assert holder["marks"] == ["dev"]
    assert holder["calls"] == [True]


def test_out_of_stamina_does_not_mark(runner_env):
    runner, holder = runner_env
    holder["reason"] = "out_of_stamina"
    out = runner._run_dragon_realm(None, _FakeTracker(0), device="dev")
    assert out["stop_reason"] == "out_of_stamina"
    assert holder["marks"] == []  # 體力會回復，下一輪要能再花 → 不標記
    assert holder["calls"] == [True]


def test_deadloop_does_not_mark(runner_env):
    runner, holder = runner_env
    holder["reason"] = "deadloop"
    out = runner._run_dragon_realm(None, _FakeTracker(0), device="dev")
    assert out["stop_reason"] == "deadloop"
    assert holder["marks"] == []


def test_already_completed_skips_without_running(runner_env, monkeypatch):
    runner, holder = runner_env
    monkeypatch.setattr(sched, "_cycle_completed", lambda *a, **k: True)
    out = runner._run_dragon_realm(None, _FakeTracker(2), device="dev")
    assert "skipped" in out
    assert holder["calls"] == []   # 已完成 → 不建 WS 龍骸 session
    assert holder["marks"] == []

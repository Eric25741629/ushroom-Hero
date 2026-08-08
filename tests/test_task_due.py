"""Unit tests for game_actions.task_due (Phase A due-registry).

只測純函式 registry；用 monkeypatch 假造 json_manager 記錄與週期函式，
並以 sys.modules 注入輕量假 rank_events / periodic_tasks，避免 import
cv2 / device / playwright 等重模組。AAA 結構。
"""

import datetime
import sys
import types

import pytest

import json_manager
from game_actions import task_due

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _dt(y, m, d, hh=12, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=_TPE)


def _patch_records(monkeypatch, records):
    """假造 json_manager.return_time：依 name 從 dict 取記錄。"""

    def fake_return_time(ip, name=""):
        return records.get(name)

    monkeypatch.setattr(json_manager, "return_time", fake_return_time)


def _stub_rank_events(monkeypatch, is_open=True):
    mod = types.ModuleType("rank_events")
    mod.is_mount_sprint_open = lambda now: is_open
    mod.SPRINT_RECORD = "衝刺-發條"
    mod.ALLOWED_WEEKDAYS = [1, 2]
    monkeypatch.setitem(sys.modules, "rank_events", mod)


def _stub_periodic(monkeypatch, should):
    import game_actions

    mod = types.ModuleType("game_actions.periodic_tasks")
    mod.should_execute_mushroom_arena = lambda ip: (should, False)
    monkeypatch.setitem(sys.modules, "game_actions.periodic_tasks", mod)
    monkeypatch.setattr(game_actions, "periodic_tasks", mod, raising=False)


def _stub_statue(monkeypatch, should):
    import game_actions

    mod = types.ModuleType("game_actions.statue_weekly")
    mod._should_execute_for_ip = lambda ip, today=None: should
    monkeypatch.setitem(sys.modules, "game_actions.statue_weekly", mod)
    monkeypatch.setattr(game_actions, "statue_weekly", mod, raising=False)


def _stub_dragon(monkeypatch, due):
    """task_due 委派 dragon_realm_scheduler._is_due；此處只需注入其回傳。"""
    import game_actions

    mod = types.ModuleType("game_actions.dragon_realm_scheduler")
    mod._is_due = lambda ip, now=None: due
    monkeypatch.setitem(sys.modules, "game_actions.dragon_realm_scheduler", mod)
    monkeypatch.setattr(game_actions, "dragon_realm_scheduler", mod, raising=False)


def _stub_fannaoxiao(monkeypatch, due):
    """task_due 委派 fannaoxiao_scheduler._is_due；此處只需注入其回傳。"""
    import game_actions

    mod = types.ModuleType("game_actions.fannaoxiao_scheduler")
    mod._is_due = lambda ip: due
    monkeypatch.setitem(sys.modules, "game_actions.fannaoxiao_scheduler", mod)
    monkeypatch.setattr(game_actions, "fannaoxiao_scheduler", mod, raising=False)


def _stub_ladder_store(monkeypatch, store):
    """讓真 ladder_reward.is_due 跑，只換掉 load_store 回傳假 store。"""
    from ws_token import ladder_reward
    monkeypatch.setattr(ladder_reward, "load_store", lambda: store)


# --------------------------------------------------------------------------
# 地獄之門（每日一次；時段窗 01:00~23:00，不再限每小時 0~20 分）
# --------------------------------------------------------------------------
def test_hellgate_due_when_no_record_and_in_window(monkeypatch):
    # Arrange
    _patch_records(monkeypatch, {})
    # Act
    result = task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5))
    # Assert
    assert result is True


def test_hellgate_not_due_when_done_today(monkeypatch):
    _patch_records(monkeypatch, {"地獄之門": {"is_next_day": False}})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5)) is False


def test_hellgate_due_when_next_day_and_in_window(monkeypatch):
    _patch_records(monkeypatch, {"地獄之門": {"is_next_day": True}})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5)) is True


def test_hellgate_due_late_evening(monkeypatch):
    # 23:xx 仍在 01:00~23:00 窗內 → due
    _patch_records(monkeypatch, {})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 23, 45)) is True


def test_hellgate_not_due_midnight_hour(monkeypatch):
    # 00:xx 在時段窗外，即使跨日 due 也不執行
    _patch_records(monkeypatch, {})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 0, 30)) is False


# --------------------------------------------------------------------------
# 每日加速
# --------------------------------------------------------------------------
def test_daily_acceleration_due_when_no_record(monkeypatch):
    _patch_records(monkeypatch, {})
    assert task_due.is_due("每日加速", "ip", now=_dt(2026, 7, 6)) is True


def test_daily_acceleration_not_due_when_done_today(monkeypatch):
    _patch_records(monkeypatch, {"daily_acceleration": {"is_next_day": False}})
    assert task_due.is_due("每日加速", "ip", now=_dt(2026, 7, 6)) is False


def test_daily_acceleration_due_when_next_day(monkeypatch):
    _patch_records(monkeypatch, {"daily_acceleration": {"is_next_day": True}})
    assert task_due.is_due("每日加速", "ip", now=_dt(2026, 7, 6)) is True


# --------------------------------------------------------------------------
# 競技場挑戰
# --------------------------------------------------------------------------
def test_arena_due_when_no_record(monkeypatch):
    _patch_records(monkeypatch, {})
    assert task_due.is_due("競技場挑戰", "ip", now=_dt(2026, 7, 6)) is True


def test_arena_not_due_when_done_today(monkeypatch):
    _patch_records(monkeypatch, {"arena_challenges": {"is_next_day": False}})
    assert task_due.is_due("競技場挑戰", "ip", now=_dt(2026, 7, 6)) is False


# --------------------------------------------------------------------------
# 坐騎衝刺
# --------------------------------------------------------------------------
def test_mount_sprint_not_due_when_window_closed(monkeypatch):
    _patch_records(monkeypatch, {})
    _stub_rank_events(monkeypatch, is_open=False)
    assert task_due.is_due("坐騎衝刺", "ip", now=_dt(2026, 7, 7)) is False


def test_mount_sprint_not_due_when_already_this_week(monkeypatch):
    _patch_records(monkeypatch, {"衝刺-發條": {"is_next_week": False}})
    _stub_rank_events(monkeypatch, is_open=True)
    assert task_due.is_due("坐騎衝刺", "ip", now=_dt(2026, 7, 7)) is False


def test_mount_sprint_due_when_open_and_cycle_week(monkeypatch):
    _patch_records(monkeypatch, {})
    _stub_rank_events(monkeypatch, is_open=True)
    monkeypatch.setattr(json_manager, "should_execute_cycle", lambda *a, **k: (True, False))
    assert task_due.is_due("坐騎衝刺", "ip", now=_dt(2026, 7, 7)) is True


def test_mount_sprint_not_due_when_not_cycle_week(monkeypatch):
    _patch_records(monkeypatch, {})
    _stub_rank_events(monkeypatch, is_open=True)
    monkeypatch.setattr(json_manager, "should_execute_cycle", lambda *a, **k: (False, False))
    assert task_due.is_due("坐騎衝刺", "ip", now=_dt(2026, 7, 7)) is False


# --------------------------------------------------------------------------
# 雲端戰鬥（每週一次；任一天手機在線即可補跑，僅週一保留凌晨 3 點下限）
# --------------------------------------------------------------------------
def test_cloud_due_on_wednesday_when_not_run_this_week(monkeypatch):
    # 放寬後：非週一（此處週三）只要本週未跑就 due（原本只限週一 → False）。
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 8, 10)) is True


def test_cloud_due_on_sunday_when_not_run_this_week(monkeypatch):
    # 使用者實際踩到的情境：該週週一漏跑，週日補打。
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 12, 20)) is True


def test_cloud_not_due_when_monday_before_3am(monkeypatch):
    # 僅週一保留凌晨 3 點下限，避開每週重置前空跑。
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 6, 2)) is False


def test_cloud_due_on_tuesday_before_3am(monkeypatch):
    # 週二起重置已過，凌晨下限不再套用。
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 7, 2)) is True


def test_cloud_due_when_monday_after_3am_no_record(monkeypatch):
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 6, 10)) is True


def test_cloud_not_due_when_already_this_week(monkeypatch):
    # 本 ISO 週已跑過（週一）→ 同週週四再查仍不跑。
    monday = _dt(2026, 7, 6, 10)
    _patch_records(monkeypatch, {"cloud_fighting_weekly": {"timestamp": monday.timestamp()}})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 9, 10)) is False


def test_cloud_due_when_last_run_prior_week(monkeypatch):
    now = _dt(2026, 7, 8, 10)  # 週三
    prior = (now - datetime.timedelta(days=14)).timestamp()
    _patch_records(monkeypatch, {"cloud_fighting_weekly": {"timestamp": prior}})
    assert task_due.is_due("雲端戰鬥", "ip", now=now) is True


# --------------------------------------------------------------------------
# 菇菇武道會
# --------------------------------------------------------------------------
def test_mushroom_not_due_when_cycle_says_no(monkeypatch):
    _patch_records(monkeypatch, {})
    _stub_periodic(monkeypatch, should=False)
    assert task_due.is_due("菇菇武道會", "ip", now=_dt(2026, 7, 6)) is False


def test_mushroom_not_due_when_daily_done(monkeypatch):
    _patch_records(monkeypatch, {"mushroom_arena_daily": {"is_next_day": False}})
    _stub_periodic(monkeypatch, should=True)
    assert task_due.is_due("菇菇武道會", "ip", now=_dt(2026, 7, 6)) is False


def test_mushroom_due_when_cycle_yes_and_no_daily_record(monkeypatch):
    _patch_records(monkeypatch, {})
    _stub_periodic(monkeypatch, should=True)
    assert task_due.is_due("菇菇武道會", "ip", now=_dt(2026, 7, 6)) is True


def test_mushroom_due_when_cycle_yes_and_daily_next_day(monkeypatch):
    _patch_records(monkeypatch, {"mushroom_arena_daily": {"is_next_day": True}})
    _stub_periodic(monkeypatch, should=True)
    assert task_due.is_due("菇菇武道會", "ip", now=_dt(2026, 7, 6)) is True


# --------------------------------------------------------------------------
# 航海
# --------------------------------------------------------------------------
def test_sea_due_when_cooldown_says_yes(monkeypatch):
    monkeypatch.setattr(json_manager, "should_execute_sea_with_cooldown", lambda *a, **k: (True, False))
    assert task_due.is_due("航海", "ip", now=_dt(2026, 7, 6, 12)) is True


def test_sea_not_due_when_cooldown_says_no(monkeypatch):
    monkeypatch.setattr(json_manager, "should_execute_sea_with_cooldown", lambda *a, **k: (False, False))
    assert task_due.is_due("航海", "ip", now=_dt(2026, 7, 6, 12)) is False


# --------------------------------------------------------------------------
# 萬神試煉（週副本）— record is_next_week + 星期時間窗（週日跳過）
# --------------------------------------------------------------------------
def test_wanshen_due_when_no_record_in_window(monkeypatch):
    # 2026-07-07 = 週二（weekday 1）→ 時間窗內
    _patch_records(monkeypatch, {})
    assert task_due.is_due("萬神試煉", "ip", now=_dt(2026, 7, 7, 10)) is True


def test_wanshen_not_due_when_done_this_week(monkeypatch):
    _patch_records(monkeypatch, {"萬神試煉": {"is_next_week": False}})
    assert task_due.is_due("萬神試煉", "ip", now=_dt(2026, 7, 7, 10)) is False


def test_wanshen_due_when_next_week_and_in_window(monkeypatch):
    _patch_records(monkeypatch, {"萬神試煉": {"is_next_week": True}})
    assert task_due.is_due("萬神試煉", "ip", now=_dt(2026, 7, 7, 10)) is True


def test_wanshen_not_due_on_sunday(monkeypatch):
    # 2026-07-12 = 週日（weekday 6）→ 時間窗外，即使 due 也跳過
    _patch_records(monkeypatch, {})
    assert task_due.is_due("萬神試煉", "ip", now=_dt(2026, 7, 12, 15)) is False


def test_wanshen_not_due_monday_morning(monkeypatch):
    # 2026-07-06 = 週一（weekday 0）：需 hour>12，早上不在窗
    _patch_records(monkeypatch, {})
    assert task_due.is_due("萬神試煉", "ip", now=_dt(2026, 7, 6, 9)) is False


# --------------------------------------------------------------------------
# 雙週副本 — is_next_biweek + 週六/日 20:xx
# --------------------------------------------------------------------------
def test_biweekly_due_when_no_record_in_window(monkeypatch):
    # 2026-07-11 = 週六（weekday 5），20:xx
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雙週副本", "ip", now=_dt(2026, 7, 11, 20, 5)) is True


def test_biweekly_not_due_when_done_this_biweek(monkeypatch):
    _patch_records(monkeypatch, {"雙週副本": {"is_next_biweek": False}})
    assert task_due.is_due("雙週副本", "ip", now=_dt(2026, 7, 11, 20, 5)) is False


def test_biweekly_not_due_outside_time_window(monkeypatch):
    # 週六但非 20 點
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雙週副本", "ip", now=_dt(2026, 7, 11, 19, 5)) is False


def test_biweekly_not_due_on_weekday(monkeypatch):
    # 2026-07-07 = 週二，非週六/日
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雙週副本", "ip", now=_dt(2026, 7, 7, 20, 5)) is False


# --------------------------------------------------------------------------
# 天梯每週獎勵 — 週二 + 有記錄/enabled/body + 本週未套用（複用 ladder_reward.is_due）
# --------------------------------------------------------------------------
def test_ladder_due_on_tuesday_with_record(monkeypatch):
    _stub_ladder_store(monkeypatch, {"ip": {"enabled": True, "body_hex": "0a"}})
    # 2026-07-07 = 週二
    assert task_due.is_due("天梯每週獎勵", "ip", now=_dt(2026, 7, 7)) is True


def test_ladder_not_due_on_non_tuesday(monkeypatch):
    _stub_ladder_store(monkeypatch, {"ip": {"enabled": True, "body_hex": "0a"}})
    # 2026-07-06 = 週一
    assert task_due.is_due("天梯每週獎勵", "ip", now=_dt(2026, 7, 6)) is False


def test_ladder_not_due_when_no_record(monkeypatch):
    _stub_ladder_store(monkeypatch, {})
    assert task_due.is_due("天梯每週獎勵", "ip", now=_dt(2026, 7, 7)) is False


def test_ladder_not_due_when_already_this_week(monkeypatch):
    # 本 ISO 週已套用 → not due
    store = {"ip": {"enabled": True, "body_hex": "0a", "last_applied_week": "2026-W28"}}
    _stub_ladder_store(monkeypatch, store)
    assert task_due.is_due("天梯每週獎勵", "ip", now=_dt(2026, 7, 7)) is False


# --------------------------------------------------------------------------
# 菇菇雕像每週 — 複用 statue_weekly._should_execute_for_ip
# --------------------------------------------------------------------------
def test_statue_due_when_scheduler_says_yes(monkeypatch):
    _stub_statue(monkeypatch, should=True)
    assert task_due.is_due("菇菇雕像每週", "ip", now=_dt(2026, 7, 10)) is True


def test_statue_not_due_when_scheduler_says_no(monkeypatch):
    # 例：非週五或本週已做
    _stub_statue(monkeypatch, should=False)
    assert task_due.is_due("菇菇雕像每週", "ip", now=_dt(2026, 7, 6)) is False


# --------------------------------------------------------------------------
# 龍骸聖域 — 單一來源：完全委派 dragon_realm_scheduler._is_due（該函式另有測試）
# --------------------------------------------------------------------------
def test_dragon_not_due_when_scheduler_says_no(monkeypatch):
    _stub_dragon(monkeypatch, due=False)
    assert task_due.is_due("龍骸聖域", "ip", now=_dt(2026, 7, 8, 12)) is False


def test_dragon_due_when_scheduler_says_yes(monkeypatch):
    _stub_dragon(monkeypatch, due=True)
    assert task_due.is_due("龍骸聖域", "ip", now=_dt(2026, 7, 8, 12)) is True


# --------------------------------------------------------------------------
# 煩惱消 — 單一來源：完全委派 fannaoxiao_scheduler._is_due（該函式另有測試）
# --------------------------------------------------------------------------
def test_fannaoxiao_due_when_scheduler_says_yes(monkeypatch):
    _stub_fannaoxiao(monkeypatch, due=True)
    assert task_due.is_due("煩惱消", "ip", now=_dt(2026, 7, 6, 12)) is True


def test_fannaoxiao_not_due_when_scheduler_says_no(monkeypatch):
    _stub_fannaoxiao(monkeypatch, due=False)
    assert task_due.is_due("煩惱消", "ip", now=_dt(2026, 7, 6, 12)) is False


# --------------------------------------------------------------------------
# 抽技能夥伴 — 讀 ws_state gacha_free.last_date；讀不到/無記錄 → 保守 due(True)
# --------------------------------------------------------------------------
def _stub_gacha_state(monkeypatch, state, *, raises=False):
    from ws_token import state as ws_state

    def fake_load_state(ip, **kw):
        if raises:
            raise RuntimeError("boom")
        return state

    monkeypatch.setattr(ws_state, "load_state", fake_load_state)


def test_gacha_due_when_no_state(monkeypatch):
    _stub_gacha_state(monkeypatch, {})
    assert task_due.is_due("抽技能夥伴", "ip", now=_dt(2026, 7, 6)) is True


def test_gacha_not_due_when_done_today(monkeypatch):
    _stub_gacha_state(monkeypatch, {"gacha_free": {"last_date": "2026-07-06"}})
    assert task_due.is_due("抽技能夥伴", "ip", now=_dt(2026, 7, 6, 15)) is False


def test_gacha_due_when_last_date_prior_day(monkeypatch):
    _stub_gacha_state(monkeypatch, {"gacha_free": {"last_date": "2026-07-05"}})
    assert task_due.is_due("抽技能夥伴", "ip", now=_dt(2026, 7, 6)) is True


def test_gacha_due_when_gacha_free_has_no_last_date(monkeypatch):
    _stub_gacha_state(monkeypatch, {"gacha_free": {"last_total": 3}})
    assert task_due.is_due("抽技能夥伴", "ip", now=_dt(2026, 7, 6)) is True


def test_gacha_due_when_state_read_raises(monkeypatch):
    _stub_gacha_state(monkeypatch, {}, raises=True)
    assert task_due.is_due("抽技能夥伴", "ip", now=_dt(2026, 7, 6)) is True


# --------------------------------------------------------------------------
# unknown task
# --------------------------------------------------------------------------
def test_unknown_task_raises_keyerror(monkeypatch):
    with pytest.raises(KeyError):
        task_due.is_due("不存在的任務", "ip", now=_dt(2026, 7, 6))

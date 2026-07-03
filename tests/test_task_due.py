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
    mod = types.ModuleType("game_actions.periodic_tasks")
    mod.should_execute_mushroom_arena = lambda ip: (should, False)
    monkeypatch.setitem(sys.modules, "game_actions.periodic_tasks", mod)


# --------------------------------------------------------------------------
# 地獄之門
# --------------------------------------------------------------------------
def test_hellgate_due_when_no_record_and_before_minute_20(monkeypatch):
    # Arrange
    _patch_records(monkeypatch, {})
    # Act
    result = task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5))
    # Assert
    assert result is True


def test_hellgate_not_due_when_done_today(monkeypatch):
    _patch_records(monkeypatch, {"地獄之門": {"is_next_day": False}})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5)) is False


def test_hellgate_due_when_next_day_and_before_minute_20(monkeypatch):
    _patch_records(monkeypatch, {"地獄之門": {"is_next_day": True}})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 5)) is True


def test_hellgate_not_due_when_minute_gate_closed(monkeypatch):
    # 即使跨日 due，minute >= 20 也不執行
    _patch_records(monkeypatch, {})
    assert task_due.is_due("地獄之門", "ip", now=_dt(2026, 7, 6, 10, 30)) is False


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
# 雲端戰鬥
# --------------------------------------------------------------------------
def test_cloud_not_due_when_not_monday(monkeypatch):
    _patch_records(monkeypatch, {})
    # 2026-07-07 是週二
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 7, 10)) is False


def test_cloud_not_due_when_monday_before_3am(monkeypatch):
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 6, 2)) is False


def test_cloud_due_when_monday_after_3am_no_record(monkeypatch):
    _patch_records(monkeypatch, {})
    assert task_due.is_due("雲端戰鬥", "ip", now=_dt(2026, 7, 6, 10)) is True


def test_cloud_not_due_when_already_this_week(monkeypatch):
    now = _dt(2026, 7, 6, 10)
    _patch_records(monkeypatch, {"cloud_fighting_weekly": {"timestamp": now.timestamp()}})
    assert task_due.is_due("雲端戰鬥", "ip", now=now) is False


def test_cloud_due_when_last_run_prior_week(monkeypatch):
    now = _dt(2026, 7, 6, 10)
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
# unknown task
# --------------------------------------------------------------------------
def test_unknown_task_raises_keyerror(monkeypatch):
    with pytest.raises(KeyError):
        task_due.is_due("不存在的任務", "ip", now=_dt(2026, 7, 6))

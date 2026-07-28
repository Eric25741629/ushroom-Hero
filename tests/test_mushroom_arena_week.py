"""菇菇武道會使用固定日曆錨點，不受裝置歷史紀錄漂移影響。"""

import datetime

import json_manager.scheduling as scheduling


def test_current_confirmed_activity_week_is_open():
    assert scheduling.is_mushroom_arena_week(datetime.date(2026, 7, 27)) is True
    assert scheduling.is_mushroom_arena_week(datetime.date(2026, 7, 28)) is True


def test_adjacent_week_is_closed():
    assert scheduling.is_mushroom_arena_week(datetime.date(2026, 8, 3)) is False


def test_next_three_week_cycle_is_open():
    assert scheduling.is_mushroom_arena_week(datetime.date(2026, 8, 17)) is True


def test_same_calendar_week_is_open_even_with_old_device_record():
    """活動週判斷不讀各裝置的上次執行日，避免裝置間錯開。"""
    assert scheduling.is_mushroom_arena_week(datetime.date(2026, 7, 27)) is True

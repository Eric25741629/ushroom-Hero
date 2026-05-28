"""Unit tests for game_actions.statue_weekly scheduling (Friday-only weekly).

Live behavior — cocos click flow on web_h5, OCR clicks on ADB — needs
integration tests against a running device (see
`tools/statue_check.py`).
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from game_actions import statue_weekly


_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _date(y, m, d) -> datetime.date:
    return datetime.date(y, m, d)


# 2026-05-22 is a Friday. Reference week:
#   Mon 2026-05-18, Fri 2026-05-22, Fri 2026-05-29, Fri 2026-06-05.


# ── should_execute ──────────────────────────────────────────────────────


def test_should_execute_friday_no_prior_record():
    """First Friday ever — run."""
    today = _date(2026, 5, 22)  # Friday
    assert statue_weekly._should_execute_for(today=today, last_recorded=None) is True


def test_should_execute_thursday_skipped():
    """Thursday is not Friday — never run."""
    today = _date(2026, 5, 21)  # Thursday
    assert statue_weekly._should_execute_for(today=today, last_recorded=None) is False


def test_should_execute_saturday_skipped():
    """Saturday is not Friday — skip even with no prior record."""
    today = _date(2026, 5, 23)  # Saturday
    assert statue_weekly._should_execute_for(today=today, last_recorded=None) is False


def test_should_execute_friday_already_ran_today():
    """Already ran earlier today — skip until next Friday."""
    today = _date(2026, 5, 22)  # Friday
    assert statue_weekly._should_execute_for(
        today=today, last_recorded=_date(2026, 5, 22)
    ) is False


def test_should_execute_next_friday_after_last_friday():
    """Last run was previous Friday — run again this Friday."""
    today = _date(2026, 5, 29)  # Friday
    assert statue_weekly._should_execute_for(
        today=today, last_recorded=_date(2026, 5, 22)
    ) is True


def test_should_execute_friday_after_a_skipped_week():
    """Last run was 2 Fridays ago — still run this Friday (always-weekly,
    not biweekly)."""
    today = _date(2026, 6, 5)  # Friday
    assert statue_weekly._should_execute_for(
        today=today, last_recorded=_date(2026, 5, 22)
    ) is True


def test_should_execute_friday_record_is_future_garbage():
    """Defensive: if recorded date is in the future (clock skew / bad data),
    don't run again until today >= recorded."""
    today = _date(2026, 5, 22)
    assert statue_weekly._should_execute_for(
        today=today, last_recorded=_date(2026, 6, 1)
    ) is False


# ── config resolution ──────────────────────────────────────────────────


def test_resolve_amount_uses_config_value():
    cfg = {"statue_weekly": {"enabled": True, "amount": 5000}}
    assert statue_weekly._resolve_amount(cfg) == 5000


def test_resolve_amount_defaults_to_7000_when_missing():
    """User policy 2026-05-24: default 7000."""
    cfg = {"statue_weekly": {"enabled": True}}
    assert statue_weekly._resolve_amount(cfg) == 7000


def test_resolve_amount_defaults_to_7000_when_no_config():
    assert statue_weekly._resolve_amount({}) == 7000


def test_resolve_amount_clamps_negatives_to_zero():
    cfg = {"statue_weekly": {"amount": -50}}
    assert statue_weekly._resolve_amount(cfg) == 0


def test_enabled_check_defaults_false():
    """Don't run unless device explicitly opts in."""
    assert statue_weekly._is_enabled({}) is False
    assert statue_weekly._is_enabled({"statue_weekly": {}}) is False
    assert statue_weekly._is_enabled({"statue_weekly": {"enabled": False}}) is False


def test_enabled_check_true_when_enabled():
    assert statue_weekly._is_enabled({"statue_weekly": {"enabled": True}}) is True


# ── adb OCR flow (mocked img_tools) ────────────────────────────────────


class _FakeAdb:
    """Stand-in for a uiautomator2 device — only the methods statue_weekly_adb
    touches: click(x,y), send_keys, press."""

    def __init__(self):
        self.clicks: list[tuple[int, int]] = []
        self.sent_keys: list[tuple[str, bool]] = []
        self.press_keys: list[str] = []
        self.send_raises: BaseException | None = None

    def click(self, x, y):
        self.clicks.append((int(x), int(y)))

    def send_keys(self, text, clear=False):
        if self.send_raises is not None:
            raise self.send_raises
        self.sent_keys.append((str(text), bool(clear)))

    def press(self, key):
        self.press_keys.append(str(key))


def test_adb_amount_zero_skips_immediately():
    """No OCR calls when amount is 0 — caller already decided not to consume."""
    d = _FakeAdb()
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=0)
    assert result is False
    mock_img.click_str_by_server.assert_not_called()
    assert d.clicks == []
    assert d.sent_keys == []


def test_adb_happy_path_calls_ocr_in_order():
    """Forward flow OCR strings in order, then return-to-main runs:
       boxTips 確定 (safety) → tap outside popup → tap red 退出 (icon) →
       OCR 關閉 in bottom tab. Pure clicks — no d.press("back")."""
    d = _FakeAdb()
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        mock_img.click_str_by_server.return_value = True
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=7000)

    assert result is True
    ocr_strings = [call.args[1] for call in mock_img.click_str_by_server.call_args_list]
    assert ocr_strings == [
        # forward
        "家園", "菇菇雕像", "鍵消耗", "0", "消耗", "確定",
        # cleanup: only two OCR calls — 確定 safety net + 關閉 final tap
        "確定", "關閉",
    ]
    # The two icon-button taps in cleanup: outside-popup then red 退出.
    assert (50, 50) in d.clicks, "must tap outside popup to dismiss via imgMask"
    assert (492, 902) in d.clicks, "must tap StatueView red 退出 button (bottom-right)"
    # Pure-click policy — no Android back-button presses.
    assert d.press_keys == [], "ADB return-to-main must NOT use d.press('back')"
    assert d.sent_keys == [("7000", True)]


def test_adb_happy_path_passes_rois():
    """Each forward OCR call passes a y_range (and where needed, an x_range)
    so OCR only scans the relevant band."""
    d = _FakeAdb()
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        mock_img.click_str_by_server.return_value = True
        statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)

    forward_calls = mock_img.click_str_by_server.call_args_list[:6]
    rois = {call.args[1]: call.kwargs for call in forward_calls}

    # Every forward step constrains y
    assert all(rois[s].get("y_range") for s in
               ["家園", "菇菇雕像", "鍵消耗", "0", "消耗", "確定"])

    # popup 消耗 ROI must exclude the StatueView background "鍵消耗" at y≈120
    cost_y_lo, _ = rois["消耗"]["y_range"]
    assert cost_y_lo > 250, (
        f"popup 消耗 y_range starts at {cost_y_lo} — must be >250 so "
        f"StatueView bg btnCost at y≈120 is excluded"
    )
    # home tab is the bottom bar
    home_y_lo, _ = rois["家園"]["y_range"]
    assert home_y_lo >= 700

    # Input "0" needs x_range too — the substring "0" matches "-10" and "+10"
    # buttons on the same row, so without an x clamp OCR picks the wrong one.
    assert rois["0"].get("x_range") is not None, (
        "input '0' step must constrain x_range to exclude -10 / +10 buttons"
    )
    x_lo, x_hi = rois["0"]["x_range"]
    # The EditBox is centered around x=270 on 540-wide viewport; -10 button is
    # at x≈131-168, +10 button at x≈369-410. ROI must sit strictly between.
    assert x_lo > 170 and x_hi < 360, (
        f"input '0' x_range {rois['0']['x_range']} must exclude -10 (x<=170) "
        f"and +10 (x>=360) buttons"
    )


def test_adb_home_tab_ocr_miss_then_recover():
    """If '家園' OCR misses first time, the flow retries once (verified via
    img_tools call_count >= 2 for that string before bailing)."""
    d = _FakeAdb()
    # 家園 miss + 6 successful calls (家園 retry, 菇菇雕像, 一鍵消耗, 0, 消耗, 確定)
    side = iter([False, True, True, True, True, True, True])
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        mock_img.click_str_by_server.side_effect = lambda *_a, **_kw: next(side)
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)
    assert result is True
    ocr_strings = [call.args[1] for call in mock_img.click_str_by_server.call_args_list]
    assert ocr_strings[:2] == ["家園", "家園"]
    assert "菇菇雕像" in ocr_strings
    assert "消耗" in ocr_strings


def test_adb_statue_button_ocr_miss_aborts():
    """If '菇菇雕像' OCR misses, the flow returns False without typing."""
    d = _FakeAdb()
    # 家園 ok, 菇菇雕像 miss
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        mock_img.click_str_by_server.side_effect = [True, False]
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)
    assert result is False
    assert d.sent_keys == []


def test_adb_cost_button_ocr_miss_aborts():
    """If '一鍵消耗' OCR misses, the flow returns False without typing."""
    d = _FakeAdb()
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        # 家園 ok, 菇菇雕像 ok, 一鍵消耗 miss
        mock_img.click_str_by_server.side_effect = [True, True, False]
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)
    assert result is False
    assert d.sent_keys == []


def test_adb_input_zero_miss_falls_back_to_coord_tap():
    """When the '0' placeholder OCR misses, the flow taps the known input
    coord and proceeds to type — the screen layout is fixed (540×960)."""
    d = _FakeAdb()
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        # 家園 / 菇菇雕像 / 一鍵消耗 ok; '0' miss; '消耗' ok; '確定' (boxTips) ok
        mock_img.click_str_by_server.side_effect = [True, True, True, False, True, True]
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)
    assert result is True
    # Fallback tap registered (any coord — just that *some* tap happened).
    assert len(d.clicks) >= 1
    assert d.sent_keys == [("1", True)]


def test_adb_send_keys_failure_aborts():
    """If uiautomator2 can't type, return False — don't blindly press 消耗."""
    d = _FakeAdb()
    d.send_raises = RuntimeError("device disconnected")
    with patch("game_actions.statue_weekly.img_tools") as mock_img:
        mock_img.click_str_by_server.return_value = True
        result = statue_weekly.run_statue_weekly_adb(d, ip="adb-test", amount=1)
    assert result is False
    ocr_strings = [call.args[1] for call in mock_img.click_str_by_server.call_args_list]
    # 消耗 must NOT have been clicked (would otherwise consume 0 → wasted attempt)
    assert "消耗" not in ocr_strings

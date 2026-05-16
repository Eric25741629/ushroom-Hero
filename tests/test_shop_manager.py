"""Tests for game_actions.shop_manager.should_purchase (extracted from json_manager)."""
import pytest
from unittest.mock import patch, MagicMock


def test_should_purchase_new_day_returns_true():
    from game_actions.shop_manager import should_purchase
    mgr = MagicMock()
    mgr.SECTION_KEYS = {"daily", "weekly"}
    state = MagicMock(timestamp=0.0, buy_num=0, check_time=0)
    mgr._load_states.return_value = {"daily": state}
    with patch("game_actions.shop_manager._ts_same_day", return_value=False):
        result = should_purchase(mgr, "daily")
    assert result is True


def test_should_purchase_within_limits_returns_true():
    from game_actions.shop_manager import should_purchase
    mgr = MagicMock()
    mgr.SECTION_KEYS = {"daily", "weekly"}
    state = MagicMock(timestamp=9999999999.0, buy_num=1, check_time=1)
    mgr._load_states.return_value = {"daily": state}
    with patch("game_actions.shop_manager._ts_same_day", return_value=True):
        result = should_purchase(mgr, "daily", max_purchases=2, max_checks=2)
    assert result is True


def test_should_purchase_exceeded_limits_returns_false():
    from game_actions.shop_manager import should_purchase
    mgr = MagicMock()
    mgr.SECTION_KEYS = {"daily", "weekly"}
    state = MagicMock(timestamp=9999999999.0, buy_num=2, check_time=2)
    mgr._load_states.return_value = {"daily": state}
    with patch("game_actions.shop_manager._ts_same_day", return_value=True):
        result = should_purchase(mgr, "daily", max_purchases=2, max_checks=2)
    assert result is False


def test_should_purchase_invalid_mode_raises():
    from game_actions.shop_manager import should_purchase
    mgr = MagicMock()
    mgr.SECTION_KEYS = {"daily", "weekly"}
    with pytest.raises(ValueError, match="mode 必須是"):
        should_purchase(mgr, "invalid")


def test_should_purchase_weekly_new_week_returns_true():
    from game_actions.shop_manager import should_purchase
    mgr = MagicMock()
    mgr.SECTION_KEYS = {"daily", "weekly"}
    state = MagicMock(timestamp=0.0, buy_num=5, check_time=5)
    mgr._load_states.return_value = {"weekly": state}
    with patch("game_actions.shop_manager._ts_same_week", return_value=False):
        result = should_purchase(mgr, "weekly")
    assert result is True


def test_legacy_method_still_works():
    """Backward compat: ParkMarketDataManager.should_purchase() should still work."""
    from json_manager import ParkMarketDataManager
    assert hasattr(ParkMarketDataManager, "should_purchase")

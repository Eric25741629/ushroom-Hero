from unittest.mock import MagicMock, patch
import pytest


def _make_device():
    return MagicMock()


def test_navigate_no_cnn_does_blind_clicks():
    """Without cnn_model, should click farm_tab then home and return True."""
    from game_actions.navigation import navigate_to_main_page, _FARM_TAB, _HOME_BTN
    d = _make_device()
    with patch("game_actions.navigation._click_with_jitter") as mock_click, \
         patch("game_actions.navigation.time") as mock_time:
        mock_time.sleep = MagicMock()
        mock_time.time.return_value = 0.0
        result = navigate_to_main_page(d, cnn_model=None)
    assert result is True
    calls = mock_click.call_args_list
    # _click_with_jitter(d, x, y, jitter=5) → args[0]=d, args[1]=x, args[2]=y
    assert any(c.args[1] == _FARM_TAB[0] and c.args[2] == _FARM_TAB[1] for c in calls), "farm_tab not clicked"
    assert any(c.args[1] == _HOME_BTN[0] and c.args[2] == _HOME_BTN[1] for c in calls), "home not clicked"


def test_navigate_with_cnn_returns_true_when_already_on_main():
    """With cnn_model, if get_stage() returns '主頁面' on first attempt, return True."""
    from game_actions.navigation import navigate_to_main_page
    d = _make_device()
    # Provide enough time values: exit_start, while-check, elapsed-in-log, elapsed-in-success
    times = iter([0.0, 0.5, 1.0, 1.0])
    with patch("game_actions.navigation.get_stage", return_value="主頁面"), \
         patch("game_actions.navigation.time") as mock_time, \
         patch("game_actions.navigation._click_with_jitter"):
        mock_time.time.side_effect = lambda: next(times)
        mock_time.sleep = MagicMock()
        result = navigate_to_main_page(d, cnn_model=MagicMock())
    assert result is True


def test_navigate_with_cnn_returns_false_on_timeout():
    """If get_stage() never returns '主頁面', return False after timeout."""
    from game_actions.navigation import navigate_to_main_page
    d = _make_device()
    times = [0.0, 65.0, 66.0]
    with patch("game_actions.navigation.get_stage", return_value="未知"), \
         patch("game_actions.navigation.time") as mock_time, \
         patch("game_actions.navigation._click_with_jitter"), \
         patch("game_actions.navigation.img_tools") as mock_img:
        mock_time.time.side_effect = times
        mock_time.sleep = MagicMock()
        mock_img.click_str_by_server.return_value = False
        result = navigate_to_main_page(d, cnn_model=MagicMock(), timeout=60.0)
    assert result is False

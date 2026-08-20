from unittest.mock import MagicMock, patch

from game_actions.cocos_family import CocosFamily, run_family_h5


def _donation_driver() -> CocosFamily:
    driver = CocosFamily(MagicMock())
    driver.ui = MagicMock()
    driver._open_donate_view = MagicMock(return_value=True)
    driver._donation_button_available = MagicMock(return_value=True)
    driver._claim_reward_popup = MagicMock(return_value=True)
    driver.close = MagicMock()
    driver.ui.click_node.return_value = True
    driver.ui.has_text.return_value = False
    return driver


def test_family_donation_uses_remaining_count_and_claims_reward_popup():
    driver = _donation_driver()
    driver._remaining_donations = MagicMock(return_value=2)
    driver._wait_donation_update = MagicMock(return_value=(1, True))

    assert driver.donate_and_claim(max_donations=1) is True
    driver.ui.click_node.assert_called_once_with("btnDonate", root="GuildDonateView")
    driver._claim_reward_popup.assert_called_once_with()
    driver.close.assert_called_once_with()


def test_family_donation_loops_until_server_count_is_zero():
    driver = _donation_driver()
    driver._remaining_donations = MagicMock(return_value=2)
    driver._wait_donation_update = MagicMock(side_effect=[(1, False), (0, False)])

    assert driver.donate_and_claim() is True
    assert driver.ui.click_node.call_count == 2
    driver.close.assert_called_once_with()


def test_family_already_capped_is_a_success_without_clicking():
    driver = _donation_driver()
    driver._remaining_donations = MagicMock(return_value=0)

    assert driver.donate_and_claim() is True
    driver.ui.click_node.assert_not_called()
    driver.close.assert_called_once_with()


def test_family_unknown_donation_state_is_not_completed():
    driver = _donation_driver()
    driver._remaining_donations = MagicMock(return_value=None)

    assert driver.donate_and_claim() is False
    driver.ui.click_node.assert_not_called()
    driver.close.assert_not_called()


def test_h5_family_flow_has_no_screenshot_or_ocr_dependency():
    page = MagicMock()
    fake = MagicMock()
    fake.donate_and_claim.return_value = True
    fake.ui.has_text.return_value = False
    with patch("game_actions.cocos_family.CocosFamily", return_value=fake):
        assert run_family_h5(page) is True


def test_family_navigation_uses_map_node_and_guild_view():
    driver = CocosFamily(MagicMock())
    driver.nav._click_path = MagicMock(return_value=True)
    driver._wait_view = MagicMock(return_value=True)
    driver.ui.click_node = MagicMock(return_value=True)
    with patch(
        "game_actions.cocos_family.is_open",
        side_effect=[False, False, False],
    ):
        assert driver._open_guild_view() is True
    driver.nav._click_path.assert_called_once()
    driver.ui.click_node.assert_called_once_with("btnInfo", root="GuildMapScene")

from unittest.mock import MagicMock, patch

from game_actions.cocos_family import CocosFamily, run_family_h5


def test_family_donate_prefers_one_key_and_claims_rewards():
    driver = CocosFamily(MagicMock())
    driver.ui = MagicMock()
    driver.ui.click_text.return_value = True
    driver.ui.wait_for_text.return_value = "捐獻"
    driver.ui.has_text.side_effect = lambda value: value in {"捐獻", "一鍵捐獻", "一鍵領取"}

    assert driver.donate_and_claim() is True
    names = [call.args[0] for call in driver.ui.click_text.call_args_list]
    assert "一鍵捐獻" in names
    assert "一鍵領取" in names


def test_h5_family_flow_has_no_screenshot_or_ocr_dependency():
    page = MagicMock()
    fake = MagicMock()
    fake.donate_and_claim.return_value = True
    fake.ui.has_text.return_value = False
    with patch("game_actions.cocos_family.CocosFamily", return_value=fake):
        assert run_family_h5(page) is True


def test_family_donate_without_donation_action_is_not_completed():
    driver = CocosFamily(MagicMock())
    driver.ui = MagicMock()
    driver.ui.wait_for_text.return_value = "家族商店"
    driver.ui.has_text.return_value = False

    assert driver.donate_and_claim() is False

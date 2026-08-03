import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from game_actions.cocos_cloud_battle import CocosCloudBattle

sys.modules.setdefault("img_tools", SimpleNamespace(
    click_str_by_server=MagicMock(), analyze_skill_via_http=MagicMock()
))


_CLOUD_PATH = Path(__file__).parents[1] / "battle" / "cloud.py"
_SPEC = importlib.util.spec_from_file_location("cloud_under_test", _CLOUD_PATH)
cloud = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cloud)


def test_is_passed_reads_cocos_label_strings():
    page = MagicMock()
    page.evaluate.return_value = {
        "texts": ["已通過最高難度"], "views": ["DoubleChapterMainView"]
    }

    assert CocosCloudBattle(page).is_passed() is True
    assert "getComponent(cc.Label)" in page.evaluate.call_args.args[0]


def test_friend_help_uses_requested_name_exactly():
    driver = CocosCloudBattle(MagicMock())
    driver.enter = MagicMock(return_value=True)
    driver.ui = MagicMock()
    driver.ui.click_text.return_value = True
    driver.ui.wait_for_text.side_effect = ["戰友招募", "指定戰友"]

    assert driver.friend_help("指定戰友") is True
    assert driver.ui.click_text.call_args_list[2].args == ("指定戰友",)
    assert driver.ui.click_text.call_args_list[2].kwargs["exact"] is True


def test_production_friend_help_routes_web_h5_to_cocos_without_ocr():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch("game_actions.cocos_cloud_battle.CocosCloudBattle.friend_help", return_value=True), \
         patch.object(cloud.img_tools, "click_str_by_server") as ocr_click:
        assert cloud.friend_help(d, "大車輪") is True
    ocr_click.assert_not_called()


def test_check_if_pass_falls_back_when_cocos_probe_is_unavailable():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    d.screenshot.return_value = object()
    with patch("game_actions.cocos_cloud_battle.CocosCloudBattle.is_passed", return_value=None), \
         patch.object(cloud.img_tools, "analyze_skill_via_http", return_value={"success": False}):
        assert cloud.check_if_pass(d) is False

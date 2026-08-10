from unittest.mock import MagicMock, patch
import sys
import types

_img = types.ModuleType("img_tools")
_img.wait_for_any_text = MagicMock()
_img.find_and_click = MagicMock()
sys.modules.setdefault("img_tools", _img)

_cnn_pkg = types.ModuleType("new_cnn")
_cnn_mod = types.ModuleType("new_cnn.cnn_model")
_cnn_mod.predict_image = MagicMock()
_cnn_pkg.cnn_model = _cnn_mod
sys.modules.setdefault("new_cnn", _cnn_pkg)
sys.modules.setdefault("new_cnn.cnn_model", _cnn_mod)

from farm_v2 import manager
from farm_v2 import web_farm


def test_h5_navigate_to_farm_uses_cocos_without_cnn_or_screenshot():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.cocos_navigator.CocosNavigator.goto_farm", return_value=True):
        assert manager.navigate_to_farm(d, MagicMock(), "web") == 6.0
    d.screenshot.assert_not_called()


def test_h5_work_status_uses_cocos_without_ocr():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.cocos_ui.CocosUI.click_text", return_value=True), \
         patch("utils.cocos_ui.CocosUI.wait_for_text", return_value="取消打工"), \
         patch.object(web_farm, "work_panel_open", return_value=False), \
         patch.object(web_farm, "close_work_panel", return_value=True) as close_panel, \
         patch.object(manager.img_tools, "wait_for_any_text", create=True) as ocr:
        manager._ensure_work_active(d)
    ocr.assert_not_called()
    close_panel.assert_called_once_with(d._page)


def test_h5_work_status_closes_stale_panel_before_reopening():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch.object(web_farm, "work_panel_open", return_value=True), \
         patch.object(web_farm, "close_work_panel", return_value=True) as close_panel, \
         patch("utils.cocos_ui.CocosUI.click_text", return_value=True), \
         patch("utils.cocos_ui.CocosUI.wait_for_text", return_value="取消打工"):
        assert manager._h5_work_is_active(d) is True
    assert close_panel.call_count == 2


def test_close_work_panel_uses_uimgr_and_verifies_view_is_gone():
    page = MagicMock()
    with patch.object(web_farm, "work_panel_open", side_effect=[True, False]), \
         patch.object(web_farm, "_uimgr_close", return_value=True) as close_view:
        assert web_farm.close_work_panel(page) is True
    close_view.assert_called_once_with(page, "FarmPlantView")


def test_close_work_panel_falls_back_to_official_close_button():
    page = MagicMock()
    with patch.object(web_farm, "work_panel_open", side_effect=[True, True, False]), \
         patch.object(web_farm, "_uimgr_close", return_value=False), \
         patch.object(web_farm, "_tap_view_btn", return_value=True) as tap_close:
        assert web_farm.close_work_panel(page) is True
    tap_close.assert_called_once_with(page, "FarmPlantView", "btnClose")


def test_close_work_panel_observes_and_closes_delayed_view():
    page = MagicMock()
    with patch.object(
        web_farm,
        "work_panel_open",
        side_effect=[False, True, False, False],
    ), patch.object(
        web_farm.time,
        "monotonic",
        side_effect=[0.0, 0.1, 0.2, 1.1],
    ), patch.object(
        web_farm.time, "sleep"
    ), patch.object(
        web_farm, "_uimgr_close", return_value=True
    ) as close_view:
        assert web_farm.close_work_panel(page, observe_for=1.0) is True
    close_view.assert_called_once_with(page, "FarmPlantView")

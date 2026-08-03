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


def test_h5_navigate_to_farm_uses_cocos_without_cnn_or_screenshot():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.cocos_navigator.CocosNavigator.goto_farm", return_value=True):
        assert manager.navigate_to_farm(d, MagicMock(), "web") == 6.0
    d.screenshot.assert_not_called()


def test_h5_work_status_uses_cocos_without_ocr():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.cocos_ui.CocosUI.click_text", return_value=True), \
         patch("utils.cocos_ui.CocosUI.wait_for_text", return_value="取消打工"), \
         patch("utils.cocos_ui.CocosUI.has_text", return_value=False), \
         patch.object(manager.img_tools, "wait_for_any_text", create=True) as ocr:
        manager._ensure_work_active(d)
    ocr.assert_not_called()

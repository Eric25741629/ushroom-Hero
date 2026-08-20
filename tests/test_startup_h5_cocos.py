from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from utils.page_detector import PageState, detect_known_h5_page


def test_known_h5_page_does_not_require_experimental_flag():
    device = SimpleNamespace(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.page_detector.PageDetector.detect_via_cocos", return_value=PageState.FARM):
        assert detect_known_h5_page(device, "web-with-flag-off") == PageState.FARM


def test_unknown_h5_overlay_is_not_an_ocr_fallback_signal():
    device = SimpleNamespace(backend_kind="web_h5", _page=MagicMock())
    with patch("utils.page_detector.PageDetector.detect_via_cocos", return_value=PageState.UNKNOWN):
        assert detect_known_h5_page(device, "web") is None

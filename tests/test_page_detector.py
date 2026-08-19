"""Unit tests for utils.page_detector — mock both Playwright page and OCR."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


# ──────────────────────────────────────────────────────────────────────
# Cocos classification (no page needed)
# ──────────────────────────────────────────────────────────────────────


def test_cocos_classify_main():
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": [], "home_active": False,
        "selected_tab_name": None,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == PageState.MAIN


def test_cocos_classify_home():
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": [], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == PageState.HOME


@pytest.mark.parametrize("overlay,expected", [
    ("PlantMainView", "FARM"),
    ("MysteryMineView", "MINE"),
    ("StatueView", "STATUE"),
    ("ParkingMainView", "CARPARK"),
    ("ParkingWareHouseView", "CARPARK_WAREHOUSE"),
    ("outlinePopView", "OFFLINE_REWARD"),
    ("WorkShopView", "WORKSHOP"),
    ("MarryMainView", "MARRY"),
    ("ScienceView", "SCIENCE"),
    ("MysteryStoreView", "MYSTERY_SHOP"),
    ("EquipEditView", "EQUIP_EDIT"),
])
def test_cocos_classify_overlay(overlay, expected):
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": [overlay], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == getattr(PageState, expected)


def test_cocos_classify_unknown_overlay_returns_unknown_not_main():
    """An unrecognized overlay should be UNKNOWN, not MAIN — silent
    misclassification when game patches add new views is dangerous."""
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": ["BrandNewViewWeDontKnow"],
        "home_active": False, "selected_tab_name": None,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == PageState.UNKNOWN


def test_cocos_known_overlay_wins_over_persistent_outline_overlay():
    from utils.page_detector import _classify_cocos_scan, PageState

    state = _classify_cocos_scan({
        "active_overlays": ["ParkingWareHouseView", "outlinePopView"],
        "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == PageState.OFFLINE_REWARD


def test_cocos_unknown_overlay_still_returns_unknown():
    from utils.page_detector import _classify_cocos_scan, PageState

    state = _classify_cocos_scan({
        "active_overlays": ["BrandNewViewWeDontKnow"], "home_active": False,
        "selected_tab_name": None,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == PageState.UNKNOWN


def test_cocos_classify_loading_takes_priority_over_overlay():
    """If both loading and an overlay are flagged, loading wins."""
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": True,
    })
    assert state == PageState.LOADING


def test_cocos_classify_guide_takes_priority_over_overlay():
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": True, "loading_inner_active": False,
    })
    assert state == PageState.GUIDE


@pytest.mark.parametrize("tab,expected", [
    ("1", "ROLE"), ("2", "PET"), ("3", "DUNGEON"),
    ("6", "GUILD"), ("5", "SHOP"),
])
def test_cocos_classify_tab(tab, expected):
    """Selected tab determines state when no overlay & not on home."""
    from utils.page_detector import _classify_cocos_scan, PageState
    state = _classify_cocos_scan({
        "active_overlays": [], "home_active": False,
        "selected_tab_name": tab,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    assert state == getattr(PageState, expected)


# ──────────────────────────────────────────────────────────────────────
# OCR classification (no page needed)
# ──────────────────────────────────────────────────────────────────────


def test_ocr_classify_home():
    from utils.page_detector import classify_ocr_texts, PageState
    # 2 of {礦山, 農場, 加工坊} → HOME rule passes.
    texts = ["你好", "礦山等級9", "農場等級9", "加工坊等級3"]
    assert classify_ocr_texts(texts) == PageState.HOME


def test_ocr_classify_farm():
    from utils.page_detector import classify_ocr_texts, PageState
    texts = ["種植小麥", "土地2"]
    assert classify_ocr_texts(texts) == PageState.FARM


def test_ocr_classify_no_match():
    from utils.page_detector import classify_ocr_texts
    assert classify_ocr_texts(["??", "?隨機"]) is None


def test_ocr_more_specific_wins():
    """If multiple rules pass, the one with higher min_matches wins."""
    from utils.page_detector import classify_ocr_texts, PageState
    # 礦山 alone matches MINE (1) and 礦山+農場+加工坊 matches HOME (2).
    texts = ["礦山", "農場", "加工坊"]
    assert classify_ocr_texts(texts) == PageState.HOME


# ──────────────────────────────────────────────────────────────────────
# PageDetector wiring
# ──────────────────────────────────────────────────────────────────────


def _mk_page(scan_return):
    page = MagicMock()
    page.evaluate.return_value = scan_return
    return page


def test_detect_via_cocos_returns_state():
    from utils.page_detector import PageDetector, PageState
    page = _mk_page({
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    det = PageDetector(page)
    assert det.detect_via_cocos() == PageState.FARM


def test_detect_via_cocos_returns_none_on_err():
    from utils.page_detector import PageDetector
    page = _mk_page({"err": "no_cc"})
    assert PageDetector(page).detect_via_cocos() is None


def test_detect_via_cocos_returns_none_on_eval_exception():
    from utils.page_detector import PageDetector
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("eval crashed")
    assert PageDetector(page).detect_via_cocos() is None


def test_detect_tries_cocos_first_skips_ocr():
    from utils.page_detector import PageDetector, PageState
    page = _mk_page({
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    det = PageDetector(page)
    with patch("utils.page_detector._ocr_to_text_list") as mock_ocr:
        state, src = det.detect()
    assert state == PageState.FARM and src == "cocos"
    mock_ocr.assert_not_called()


def test_detect_falls_back_to_ocr_when_cocos_returns_none():
    from utils.page_detector import PageDetector, PageState
    page = MagicMock()
    page.evaluate.return_value = {"err": "no_cc"}
    page.screenshot.return_value = b"FAKE_PNG_BYTES"
    det = PageDetector(page)
    with patch("utils.page_detector._ocr_to_text_list",
               return_value=["礦山等級2", "農場", "加工坊"]):
        state, src = det.detect()
    assert state == PageState.HOME and src == "ocr"


def test_detect_returns_unknown_when_neither_path_resolves():
    from utils.page_detector import PageDetector, PageState
    page = MagicMock()
    page.evaluate.return_value = {"err": "no_cc"}
    page.screenshot.return_value = b"x"
    det = PageDetector(page)
    with patch("utils.page_detector._ocr_to_text_list", return_value=[]):
        state, src = det.detect()
    assert state == PageState.UNKNOWN and src == "none"


def test_detect_with_ocr_disabled_skips_ocr_path():
    from utils.page_detector import PageDetector, PageState
    page = MagicMock()
    page.evaluate.return_value = {"err": "no_cc"}
    det = PageDetector(page, ocr_enabled=False)
    with patch("utils.page_detector._ocr_to_text_list") as mock_ocr:
        state, src = det.detect()
    assert state == PageState.UNKNOWN and src == "none"
    mock_ocr.assert_not_called()


def test_wait_for_returns_true_when_state_becomes_current():
    from utils.page_detector import PageDetector, PageState
    page = MagicMock()
    # First call: not main, second: main.
    page.evaluate.side_effect = [
        {"active_overlays": ["PlantMainView"], "home_active": True,
         "selected_tab_name": "4", "guide_inner_active": False, "loading_inner_active": False},
        {"active_overlays": [], "home_active": False,
         "selected_tab_name": None, "guide_inner_active": False, "loading_inner_active": False},
    ]
    det = PageDetector(page)
    with patch("utils.page_detector.time.sleep"):
        ok = det.wait_for(PageState.MAIN, timeout=2.0, poll_interval=0.05)
    assert ok is True


def test_wait_for_returns_false_on_timeout():
    from utils.page_detector import PageDetector, PageState
    page = MagicMock()
    page.evaluate.return_value = {
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4", "guide_inner_active": False, "loading_inner_active": False,
    }
    det = PageDetector(page)
    with patch("utils.page_detector.time.sleep"):
        ok = det.wait_for(PageState.MAIN, timeout=0.1, poll_interval=0.05)
    assert ok is False


# ──────────────────────────────────────────────────────────────────────
# try_detect_main_page_fast — legacy-stage fast-path for get_stage_with_check
# ──────────────────────────────────────────────────────────────────────


def _make_web_device(scan_return):
    d = MagicMock()
    d._page = MagicMock()
    d._page.evaluate.return_value = scan_return
    return d


def test_fast_path_returns_main_string_when_cocos_says_main():
    from utils import page_detector
    d = _make_web_device({
        "active_overlays": [], "home_active": False, "selected_tab_name": None,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    with patch.object(page_detector, "_legacy_fast_path_enabled", return_value=True):
        assert page_detector.try_detect_main_page_fast(d, "emulator-5554") == "主頁面"


def test_fast_path_returns_none_when_cocos_says_non_main():
    """Non-main cocos states must NOT short-circuit OCR — popups like
    異地登錄 are detected by OCR, never by cocos."""
    from utils import page_detector
    d = _make_web_device({
        "active_overlays": ["PlantMainView"], "home_active": True,
        "selected_tab_name": "4",
        "guide_inner_active": False, "loading_inner_active": False,
    })
    with patch.object(page_detector, "_legacy_fast_path_enabled", return_value=True):
        assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None


def test_fast_path_returns_none_when_flag_off():
    from utils import page_detector
    d = _make_web_device({
        "active_overlays": [], "home_active": False, "selected_tab_name": None,
        "guide_inner_active": False, "loading_inner_active": False,
    })
    with patch.object(page_detector, "_legacy_fast_path_enabled", return_value=False):
        assert page_detector.try_detect_main_page_fast(d, "emulator-5556") is None


def test_fast_path_returns_none_when_device_has_no_page():
    """ADB devices have no `_page` attribute — fast-path must opt-out."""
    from utils import page_detector
    d = MagicMock(spec=[])  # no _page
    with patch.object(page_detector, "_legacy_fast_path_enabled", return_value=True):
        assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None


def test_fast_path_swallows_exceptions():
    from utils import page_detector
    d = MagicMock()
    d._page = MagicMock()
    d._page.evaluate.side_effect = RuntimeError("page closed")
    with patch.object(page_detector, "_legacy_fast_path_enabled", return_value=True):
        assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None


def test_fast_path_enabled_requires_both_flag_and_web_backend():
    from utils import page_detector
    with patch.object(page_detector, "config_manager") as cm:
        # flag on + web_h5 → enabled
        cm.get_device_config.return_value = {
            "experimental_cocos_navigation": True, "backend": "web_h5",
        }
        assert page_detector._legacy_fast_path_enabled("emulator-5554") is True
        # flag on + adb backend → NOT enabled (HTML only per design)
        cm.get_device_config.return_value = {
            "experimental_cocos_navigation": True, "backend": "adb",
        }
        assert page_detector._legacy_fast_path_enabled("emulator-5556") is False
        # flag off → NOT enabled regardless of backend
        cm.get_device_config.return_value = {
            "experimental_cocos_navigation": False, "backend": "web_h5",
        }
        assert page_detector._legacy_fast_path_enabled("emulator-5556") is False
        # missing keys → NOT enabled
        cm.get_device_config.return_value = {}
        assert page_detector._legacy_fast_path_enabled("emulator-5556") is False


def test_fast_path_enabled_handles_missing_ip():
    from utils import page_detector
    assert page_detector._legacy_fast_path_enabled(None) is False
    assert page_detector._legacy_fast_path_enabled("") is False

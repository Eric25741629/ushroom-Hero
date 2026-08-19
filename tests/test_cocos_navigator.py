"""Tests for utils.cocos_navigator — experimental cocos-emit-click navigation.

Strategy: mock Playwright `page.evaluate(...)` to simulate cocos state and
verify that the navigator (1) sends the right JS expressions and (2) handles
success/failure correctly.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


# ──────────────────────────────────────────────────────────────────────
# CocosNavigator core
# ──────────────────────────────────────────────────────────────────────


def test_close_open_notice_clicks_and_verifies_inactive():
    from utils.cocos_navigator import close_open_notice

    returns = iter([{"found": True, "clicked": True}, False])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    with patch("utils.cocos_navigator.time.sleep"):
        assert close_open_notice(page) is True
    assert page.evaluate.call_count == 2


def test_close_open_notice_returns_false_when_close_button_missing():
    from utils.cocos_navigator import close_open_notice

    page = _make_page(eval_return={
        "found": True, "clicked": False, "err": "notice_close_button_unavailable",
    })
    with patch("utils.cocos_navigator.time.sleep"):
        assert close_open_notice(page) is False


def test_close_open_welfare_popup_clicks_mask_and_verifies_inactive():
    from utils.cocos_navigator import close_open_welfare_popup

    returns = iter([{"found": True, "clicked": True}, False])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    with patch("utils.cocos_navigator.time.sleep"):
        assert close_open_welfare_popup(page) is True
    assert page.evaluate.call_count == 2


def test_close_open_welfare_popup_returns_false_when_mask_missing():
    from utils.cocos_navigator import close_open_welfare_popup

    page = _make_page(eval_return={
        "found": True, "clicked": False, "err": "welfare_close_mask_unavailable",
    })
    with patch("utils.cocos_navigator.time.sleep"):
        assert close_open_welfare_popup(page) is False


def _make_page(eval_return=None, eval_side_effect=None):
    """Build a fake Playwright page whose .evaluate(...) returns canned data."""
    page = MagicMock()
    if eval_side_effect is not None:
        page.evaluate.side_effect = eval_side_effect
    else:
        page.evaluate.return_value = eval_return
    return page


def test_current_view_returns_main_when_no_tab_selected_and_no_overlay():
    from utils.cocos_navigator import CocosNavigator
    # The current_view JS probes active prefabs; we mock it to report
    # MainView with no overlay → "main".
    page = _make_page(eval_return={"main_active": True, "home_active": False,
                                    "farm_active": False, "tab_selected_idx": None})
    nav = CocosNavigator(page)
    assert nav.current_view() == "main"


def test_current_view_returns_home_when_mystery_view_active():
    from utils.cocos_navigator import CocosNavigator
    page = _make_page(eval_return={"main_active": True, "home_active": True,
                                    "farm_active": False, "tab_selected_idx": 4})
    nav = CocosNavigator(page)
    assert nav.current_view() == "home"


def test_current_view_returns_farm_when_plant_view_active():
    from utils.cocos_navigator import CocosNavigator
    page = _make_page(eval_return={"main_active": True, "home_active": True,
                                    "farm_active": True, "tab_selected_idx": 4})
    nav = CocosNavigator(page)
    assert nav.current_view() == "farm"


def test_goto_home_clicks_home_tab_and_verifies():
    """goto_home emits click on the home tab path and re-checks current_view."""
    from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
    # Sequence: first call = click result, second call = current_view post.
    returns = iter([
        {"clicked": True, "path": COCOS_PATHS["home_tab"]},  # click
        {"main_active": True, "home_active": True, "farm_active": False,
         "tab_selected_idx": 4},                              # verify
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_home()
    assert ok is True


def test_goto_home_returns_false_if_state_did_not_change():
    """If after clicking, current_view is not 'home', return False."""
    from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
    returns = iter([
        {"clicked": True, "path": COCOS_PATHS["home_tab"]},
        {"main_active": True, "home_active": False, "farm_active": False,
         "tab_selected_idx": None},
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_home()
    assert ok is False


def test_goto_home_returns_false_when_node_not_found():
    from utils.cocos_navigator import CocosNavigator
    page = _make_page(eval_return={"clicked": False, "err": "path_not_found"})
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_home()
    assert ok is False


def test_goto_farm_navigates_home_first_if_not_there():
    """If current_view is 'main', goto_farm should first go home, then click Farm."""
    from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
    # Sequence:
    #   1) current_view() → main
    #   2) click home_tab → clicked
    #   3) current_view() → home (verify after home click)
    #   4) click farm_node → clicked
    #   5) current_view() → farm (verify after farm click)
    returns = iter([
        {"main_active": True, "home_active": False, "farm_active": False, "tab_selected_idx": None},
        {"clicked": True, "path": COCOS_PATHS["home_tab"]},
        {"main_active": True, "home_active": True, "farm_active": False, "tab_selected_idx": 4},
        {"clicked": True, "path": COCOS_PATHS["farm_node"]},
        {"main_active": True, "home_active": True, "farm_active": True, "tab_selected_idx": 4},
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_farm()
    assert ok is True


def test_goto_main_closes_overlay_and_toggles_tab():
    """goto_main: if farm view open, close it; if tab selected, toggle off."""
    from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
    returns = iter([
        # current_view: on farm
        {"main_active": True, "home_active": True, "farm_active": True, "tab_selected_idx": 4},
        # click btn_close → clicked
        {"clicked": True, "path": COCOS_PATHS["farm_close_btn"]},
        # current_view post-close: on home
        {"main_active": True, "home_active": True, "farm_active": False, "tab_selected_idx": 4},
        # click home_tab (toggle off) → clicked
        {"clicked": True, "path": COCOS_PATHS["home_tab"]},
        # current_view final: main
        {"main_active": True, "home_active": False, "farm_active": False, "tab_selected_idx": None},
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_main()
    assert ok is True


def test_goto_main_noop_when_already_main():
    from utils.cocos_navigator import CocosNavigator
    page = _make_page(eval_return={"main_active": True, "home_active": False,
                                    "farm_active": False, "tab_selected_idx": None})
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        ok = nav.goto_main()
    assert ok is True
    # Only one call (current_view), no click attempts.
    assert page.evaluate.call_count == 1


def test_navigator_swallows_playwright_errors_as_false():
    from utils.cocos_navigator import CocosNavigator
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.goto_home() is False
        assert nav.current_view() == "unknown"


# ──────────────────────────────────────────────────────────────────────
# try_cocos_navigate flag-aware wrapper
# ──────────────────────────────────────────────────────────────────────


def test_try_cocos_navigate_returns_none_when_flag_off():
    """Flag off → None (caller falls back to click-based nav)."""
    from utils import cocos_navigator
    d = MagicMock(spec=[])  # no _page attribute
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=False):
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5556", "farm")
    assert result is None


def test_try_cocos_navigate_returns_none_when_device_has_no_page():
    """Non-web_h5 device (no _page attr) → None."""
    from utils import cocos_navigator
    d = MagicMock(spec=[])  # no _page
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True):
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5554", "farm")
    assert result is None


def test_try_cocos_navigate_returns_true_on_success():
    from utils import cocos_navigator
    d = MagicMock()
    d._page = MagicMock()
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True), \
         patch.object(cocos_navigator, "CocosNavigator") as MockNav:
        MockNav.return_value.goto_farm.return_value = True
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5554", "farm")
    assert result is True


def test_try_cocos_navigate_returns_false_on_failure():
    from utils import cocos_navigator
    d = MagicMock()
    d._page = MagicMock()
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True), \
         patch.object(cocos_navigator, "CocosNavigator") as MockNav:
        MockNav.return_value.goto_farm.return_value = False
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5554", "farm")
    assert result is False


def test_try_cocos_navigate_dispatches_target_correctly():
    """Verify "main" → goto_main, "home" → goto_home, "farm" → goto_farm."""
    from utils import cocos_navigator
    d = MagicMock()
    d._page = MagicMock()
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True), \
         patch.object(cocos_navigator, "CocosNavigator") as MockNav:
        nav = MockNav.return_value
        nav.goto_main.return_value = True
        nav.goto_home.return_value = True
        nav.goto_farm.return_value = True
        nav.sweep_popups.return_value = 0

        cocos_navigator.try_cocos_navigate(d, "emulator-5554", "main")
        nav.goto_main.assert_called_once()
        cocos_navigator.try_cocos_navigate(d, "emulator-5554", "home")
        nav.goto_home.assert_called_once()
        cocos_navigator.try_cocos_navigate(d, "emulator-5554", "farm")
        nav.goto_farm.assert_called_once()
        cocos_navigator.try_cocos_navigate(d, "emulator-5554", "sweep_popups")
        nav.sweep_popups.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# sweep_popups
# ──────────────────────────────────────────────────────────────────────


def test_sweep_popups_returns_zero_when_already_on_main():
    """No overlay → nothing to sweep, return 0 immediately."""
    from utils.cocos_navigator import CocosNavigator
    page = _make_page(eval_return={"main_active": True, "home_active": False,
                                    "farm_active": False, "tab_selected_idx": None})
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.sweep_popups() == 0


def test_sweep_popups_returns_zero_when_no_close_button_found():
    """current_view stuck at 'unknown' (overlay exists) but no close-btn detectable.
    Should stop the loop after one probe rather than spinning forever."""
    from utils.cocos_navigator import CocosNavigator
    state_unknown = {"main_active": True, "home_active": False,
                     "farm_active": False, "tab_selected_idx": None,
                     "open_overlay_views": ["MysteriousView"]}
    returns = iter([state_unknown, {"paths": []}])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.sweep_popups() == 0


def test_sweep_popups_closes_one_overlay_then_stops_at_home():
    """Single overlay → close once → land at home → stop."""
    from utils.cocos_navigator import CocosNavigator
    state_overlay = {"main_active": True, "home_active": True,
                     "farm_active": False, "tab_selected_idx": 4,
                     "open_overlay_views": ["PlantMainView"]}
    state_home = {"main_active": True, "home_active": True,
                  "farm_active": False, "tab_selected_idx": 4,
                  "open_overlay_views": []}
    returns = iter([
        state_overlay,                         # 1st current_view → unknown (overlay)
        {"paths": ["/UIRoot/NormalView/PlantMainView/bottom/btnClose"]},  # find close
        {"clicked": True, "path": "x"},        # click result
        state_home,                            # next current_view → home → stop
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.sweep_popups() == 1


def test_sweep_popups_closes_welfare_mask_overlay():
    from utils.cocos_navigator import CocosNavigator

    state_welfare = {"main_active": True, "home_active": True,
                     "farm_active": False, "tab_selected_idx": 4,
                     "open_overlay_views": ["WelfareH5PopView"]}
    state_home = {"main_active": True, "home_active": True,
                  "farm_active": False, "tab_selected_idx": 4,
                  "open_overlay_views": []}
    returns = iter([state_welfare, state_home])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.close_open_welfare_popup", return_value=True) as close:
        with patch("utils.cocos_navigator.time.sleep"):
            assert nav.sweep_popups() == 1
    close.assert_called_once_with(page)


def test_sweep_popups_keeps_tab_state_unlike_goto_main():
    """Verify sweep_popups does NOT click the home tab even when current_view
    is 'home' — it only closes overlays. (Caller may be intentionally on home.)
    """
    from utils.cocos_navigator import CocosNavigator
    state_home = {"main_active": True, "home_active": True,
                  "farm_active": False, "tab_selected_idx": 4,
                  "open_overlay_views": []}
    page = _make_page(eval_return=state_home)
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.sweep_popups() == 0
    # Should not have evaluated any click JS — only the view-state probe.
    # All calls should have been to _VIEW_STATE_JS (no path arg).
    for call in page.evaluate.call_args_list:
        # current_view uses page.evaluate(JS) without positional args (no path).
        # close-btn / click JS would have a positional [path] argument.
        assert len(call.args) <= 1 or not call.args[1], \
            f"unexpected positional arg: {call.args}"


def test_dismiss_blocking_popups_sums_normalview_and_topview_closes():
    """dismiss_blocking_popups = sweep_popups (NormalView) + TopView dismissal.
    Here: no NormalView overlay (sweep returns 0) + TopView reports 2 closed → 2.
    """
    from utils.cocos_navigator import CocosNavigator
    returns = iter([
        # sweep_popups → current_view probe → main → returns 0 immediately
        {"main_active": True, "home_active": False, "farm_active": False,
         "tab_selected_idx": None, "open_overlay_views": []},
        # _DISMISS_TOP_POPUPS_JS result
        {"closed": 2},
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(returns))
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.dismiss_blocking_popups() == 2


def test_dismiss_blocking_popups_survives_topview_eval_error():
    """If the TopView dismissal JS throws, still return the NormalView sweep count."""
    from utils.cocos_navigator import CocosNavigator
    state_main = {"main_active": True, "home_active": False, "farm_active": False,
                  "tab_selected_idx": None, "open_overlay_views": []}

    page = MagicMock()
    # sweep returns 0 (on main); then the TopView eval raises.
    seq = iter([state_main, RuntimeError("boom")])

    def _side(*a, **kw):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    page.evaluate.side_effect = _side
    nav = CocosNavigator(page)
    with patch("utils.cocos_navigator.time.sleep"):
        assert nav.dismiss_blocking_popups() == 0


def test_try_cocos_navigate_unknown_target_returns_none():
    from utils import cocos_navigator
    d = MagicMock()
    d._page = MagicMock()
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True):
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5554", "shop")
    assert result is None


def test_try_cocos_navigate_swallows_exceptions_as_false():
    """If CocosNavigator raises, return False (caller falls back)."""
    from utils import cocos_navigator
    d = MagicMock()
    d._page = MagicMock()
    with patch.object(cocos_navigator, "_device_flag_enabled", return_value=True), \
         patch.object(cocos_navigator, "CocosNavigator", side_effect=RuntimeError("boom")):
        result = cocos_navigator.try_cocos_navigate(d, "emulator-5554", "farm")
    assert result is False


# ──────────────────────────────────────────────────────────────────────
# Flag resolution
# ──────────────────────────────────────────────────────────────────────


def test_device_flag_enabled_reads_from_config_manager():
    from utils import cocos_navigator
    with patch.object(cocos_navigator, "config_manager") as cm:
        cm.get_device_config.return_value = {"experimental_cocos_navigation": True}
        assert cocos_navigator._device_flag_enabled("emulator-5554") is True
        cm.get_device_config.return_value = {"experimental_cocos_navigation": False}
        assert cocos_navigator._device_flag_enabled("emulator-5556") is False
        cm.get_device_config.return_value = {}  # missing → default False
        assert cocos_navigator._device_flag_enabled("emulator-5560") is False


def test_device_flag_enabled_handles_missing_ip():
    from utils import cocos_navigator
    assert cocos_navigator._device_flag_enabled(None) is False
    assert cocos_navigator._device_flag_enabled("") is False


# ──────────────────────────────────────────────────────────────────────
# current_page() wires through to PageDetector (richer state)
# ──────────────────────────────────────────────────────────────────────


def test_current_page_returns_pagestate_from_detector():
    """current_page should call PageDetector.detect and return the enum value."""
    from utils.cocos_navigator import CocosNavigator
    from utils.page_detector import PageState
    page = MagicMock()
    nav = CocosNavigator(page)
    with patch("utils.page_detector.PageDetector") as MockDet:
        MockDet.return_value.detect.return_value = (PageState.MINE, "cocos")
        result = nav.current_page()
    assert result == PageState.MINE


def test_current_page_disables_ocr_by_default():
    """current_page defaults to ocr_fallback=False — keep cocos-only for hot loops."""
    from utils.cocos_navigator import CocosNavigator
    page = MagicMock()
    nav = CocosNavigator(page)
    with patch("utils.page_detector.PageDetector") as MockDet:
        MockDet.return_value.detect.return_value = ("main", "cocos")
        nav.current_page()
        # PageDetector(page, ocr_enabled=False)
        kwargs = MockDet.call_args.kwargs
        assert kwargs.get("ocr_enabled") is False


def test_current_page_passes_ocr_fallback_when_requested():
    from utils.cocos_navigator import CocosNavigator
    page = MagicMock()
    nav = CocosNavigator(page)
    with patch("utils.page_detector.PageDetector") as MockDet:
        MockDet.return_value.detect.return_value = ("main", "cocos")
        nav.current_page(ocr_fallback=True)
        assert MockDet.call_args.kwargs.get("ocr_enabled") is True

"""Tests for game_state.detector.get_stage OCR-call collapsing refactor.

These lock the behavior of get_stage while it is refactored from 3-4 OCR HTTP
calls per invocation down to exactly ONE. OCR is mocked at the
`img_tools.analyze_skill_via_http` boundary (the single shared endpoint that
both `get_all_text` and the 公告 bbox branch hit), so no real device / OCR
server is required.

Priority order under test (must be preserved EXACTLY):
  公告 (actionable, bbox x>155)  >  車位倉庫  >  異地登錄  >  主頁面  >  ...  >  未知
"""

import numpy as np
import pytest

import img_tools
from game_state import detector


def _fake_img():
    """A dummy frame. get_stage never inspects pixels when img is passed and
    OCR is mocked, so a tiny array is enough."""
    return np.zeros((10, 10, 3), dtype=np.uint8)


def _ocr_response(items):
    """Build a server-shaped OCR json.

    items: list of (text, x) tuples. x becomes the bbox top-left x coordinate
    using the canonical [[x,y],...] polygon format the real server emits.
    """
    return {
        "success": True,
        "ocr_results": [
            {"text": text, "bbox": [[x, 100], [x + 50, 100], [x + 50, 130], [x, 130]]}
            for (text, x) in items
        ],
    }


@pytest.fixture
def mock_ocr(monkeypatch):
    """Patch the single OCR endpoint and count how many times it is called.

    Returns a closure: call it with a list of (text, x) tuples to arm the
    canned response. Exposes `.calls` on the returned setter for assertions.
    """
    state = {"count": 0, "items": []}

    def fake_analyze(img_roi, OCR_SERVER_URL=None, max_servers=None):
        state["count"] += 1
        return _ocr_response(state["items"])

    monkeypatch.setattr(img_tools, "analyze_skill_via_http", fake_analyze)

    def setter(items):
        state["items"] = items
        return state

    setter.state = state
    return setter


def test_get_stage_makes_exactly_one_ocr_call(mock_ocr):
    """Core perf regression guard: one frame -> one OCR HTTP call (was 3-4)."""
    state = mock_ocr([("無關文字", 50)])
    detector.get_stage(None, None, img=_fake_img())
    assert state["count"] == 1, f"expected 1 OCR call, got {state['count']}"


def test_get_stage_makes_one_ocr_call_even_with_announcement(mock_ocr):
    """公告 branch must reuse the same OCR result, not make a second call."""
    state = mock_ocr([("活動公告", 200)])
    result = detector.get_stage(None, None, img=_fake_img())
    assert result == "公告"
    assert state["count"] == 1, f"expected 1 OCR call, got {state['count']}"


def test_announcement_actionable_when_x_gt_155(mock_ocr):
    mock_ocr([("系統公告", 200)])
    assert detector.get_stage(None, None, img=_fake_img()) == "公告"


def test_announcement_not_actionable_when_x_le_155(mock_ocr):
    """公告 with bbox x<=155 must NOT be treated as an actionable 公告.

    Paired with main-page features so the legacy text-only fallback
    (``main_count == 0 -> 公告``) does not fire: with main features present the
    page resolves to 主頁面, proving the x<=155 announcement was ignored.
    Contrast with test_announcement_takes_priority_over_main_page (x>155 -> 公告).
    """
    mock_ocr([("公告", 100), ("方案", 30), ("副本", 60), ("家園", 90)])
    result = detector.get_stage(None, None, img=_fake_img())
    assert result == "主頁面", f"x<=155 announcement should be ignored, got {result}"


def test_announcement_takes_priority_over_main_page(mock_ocr):
    """Ordering guard: actionable 公告 wins over main-page features."""
    mock_ocr([("活動公告", 200), ("方案", 30), ("副本", 60), ("家園", 90)])
    assert detector.get_stage(None, None, img=_fake_img()) == "公告"


def test_parking_takes_priority_over_main_page(mock_ocr):
    """車位倉庫 overlays main elements -> must win over 主頁面."""
    mock_ocr([("車位倉庫", 30), ("方案", 60), ("副本", 90), ("家園", 120)])
    assert detector.get_stage(None, None, img=_fake_img()) == "車位倉庫"


def test_main_page_detected_with_two_features(mock_ocr):
    mock_ocr([("方案", 30), ("副本", 60)])
    assert detector.get_stage(None, None, img=_fake_img()) == "主頁面"


def test_login_conflict_detected(mock_ocr):
    mock_ocr([("你的帳號在另一個地方登入", 30)])
    assert detector.get_stage(None, None, img=_fake_img()) == "異地登錄"


def test_unknown_stage_for_unrelated_text(mock_ocr):
    mock_ocr([("隨便的文字", 30), ("毫不相關", 60)])
    assert detector.get_stage(None, None, img=_fake_img()) == "未知"


def test_ocr_failure_does_not_crash(monkeypatch):
    """If the OCR endpoint raises, get_stage swallows it and returns '未知'
    (the empty local_texts path)."""

    def boom(img_roi, OCR_SERVER_URL=None, max_servers=None):
        raise RuntimeError("OCR server down")

    monkeypatch.setattr(img_tools, "analyze_skill_via_http", boom)
    result = detector.get_stage(None, None, img=_fake_img())
    assert result == "未知"

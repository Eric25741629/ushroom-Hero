"""Tests for OCR helpers in `miner.core.ocr_utils`.

Two API surfaces are covered:

  - `_extract_inventory_number` — pure parser, returns conservative int.
  - `check_pickaxe_count` — reads pickaxe count from a frame; previously
    swallowed every failure mode by returning 20. The mining loop relied
    on that as `count`, but the silent fallback masked OCR breakage and
    let the loop keep mining when the real count was unknown. The new
    `allow_none=True` opt-in surfaces OCR failure so the caller can
    fall back to an internal counter instead.
"""
from __future__ import annotations

import numpy as np

from miner.core import ocr_utils
from miner.core.ocr_utils import _extract_inventory_number, check_pickaxe_count


# ---------------------------------------------------------------------------
# Parser tests (unchanged)
# ---------------------------------------------------------------------------
def test_extract_inventory_number_prefers_left_side_of_ratio():
    result = {"ocr_results": [{"text": "0/2"}]}
    assert _extract_inventory_number(result) == 0


def test_extract_inventory_number_uses_conservative_minimum():
    result = {"ocr_results": [{"text": "drill 12/34"}, {"text": "2"}]}
    assert _extract_inventory_number(result) == 2


def test_extract_inventory_number_returns_zero_on_noise_or_failure():
    assert _extract_inventory_number(None) == 0
    assert _extract_inventory_number(404) == 0
    assert _extract_inventory_number({"success": False}) == 0


def test_extract_inventory_number_handles_plain_number():
    result = {"ocr_results": [{"text": "bomb 7"}]}
    assert _extract_inventory_number(result) == 7


# ---------------------------------------------------------------------------
# check_pickaxe_count — allow_none opt-in surfaces OCR failure
# ---------------------------------------------------------------------------
def _blank_frame() -> np.ndarray:
    return np.zeros((960, 540, 3), dtype=np.uint8)


def test_check_pickaxe_count_default_returns_20_on_failure(monkeypatch):
    """Backward-compat: callers without allow_none keep the legacy 20 fallback."""
    monkeypatch.setattr(
        ocr_utils.img_tools, "analyze_skill_via_http",
        lambda *_a, **_kw: None,
    )
    assert check_pickaxe_count(None, frame=_blank_frame()) == 20


def test_check_pickaxe_count_allow_none_returns_none_on_http_failure(monkeypatch):
    """allow_none=True surfaces OCR failure with None so the caller can fall back."""
    monkeypatch.setattr(
        ocr_utils.img_tools, "analyze_skill_via_http",
        lambda *_a, **_kw: None,
    )
    assert check_pickaxe_count(None, frame=_blank_frame(), allow_none=True) is None


def test_check_pickaxe_count_allow_none_returns_none_on_empty_ocr_results(monkeypatch):
    monkeypatch.setattr(
        ocr_utils.img_tools, "analyze_skill_via_http",
        lambda *_a, **_kw: {"ocr_results": []},
    )
    assert check_pickaxe_count(None, frame=_blank_frame(), allow_none=True) is None


def test_check_pickaxe_count_allow_none_returns_none_on_unparseable_text(monkeypatch):
    monkeypatch.setattr(
        ocr_utils.img_tools, "analyze_skill_via_http",
        lambda *_a, **_kw: {"ocr_results": [{"text": "???"}]},
    )
    assert check_pickaxe_count(None, frame=_blank_frame(), allow_none=True) is None


def test_check_pickaxe_count_returns_int_on_success_regardless_of_allow_none(monkeypatch):
    monkeypatch.setattr(
        ocr_utils.img_tools, "analyze_skill_via_http",
        lambda *_a, **_kw: {"ocr_results": [{"text": "120/200"}]},
    )
    assert check_pickaxe_count(None, frame=_blank_frame()) == 120
    assert check_pickaxe_count(None, frame=_blank_frame(), allow_none=True) == 120


def test_check_pickaxe_count_exception_in_pipeline_falls_back(monkeypatch):
    """Any exception during OCR pipeline → fallback (20 by default, None with allow_none)."""
    def _boom(*_a, **_kw):
        raise RuntimeError("OCR backend down")

    monkeypatch.setattr(ocr_utils.img_tools, "analyze_skill_via_http", _boom)
    assert check_pickaxe_count(None, frame=_blank_frame()) == 20
    assert check_pickaxe_count(None, frame=_blank_frame(), allow_none=True) is None

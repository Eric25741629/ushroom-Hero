from miner.core.ocr_utils import _extract_inventory_number


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

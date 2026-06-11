import logging
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())

from opengold_v2 import lamp_service  # noqa: E402
from opengold_v2.config import OpenGoldConfig  # noqa: E402
from opengold_v2.lamp_service import LampService  # noqa: E402
from opengold_v2.models import ComparisonResult, LampState, ParsedOCRResult  # noqa: E402
from utils import logging_utils  # noqa: E402


class _UpgradeUI:
    def __init__(self):
        self.keep_clicked = 0
        self.sell_clicked = 0

    def open_upgrade_panel(self):
        pass

    def get_rolled_rois(self):
        return []

    def get_original_rois(self, _device_ip=None):
        return []

    def click_keep_button(self):
        self.keep_clicked += 1

    def click_sell_button(self):
        self.sell_clicked += 1


class _SingleLampUI:
    def __init__(self):
        self.sell_clicked = 0

    def get_gold_num(self):
        return 10

    def click_lamp_button(self):
        pass

    def get_skill_roi(self):
        return object()

    def click_sell_button(self):
        self.sell_clicked += 1


def _blank_service():
    svc = object.__new__(LampService)
    svc.config = OpenGoldConfig()
    svc.device_ip = None
    svc.has_lian_shan_equip = False
    svc.state = LampState()
    svc._log_screenshot = lambda *args, **kwargs: None
    svc._log_roi = lambda *args, **kwargs: None
    return svc


@pytest.mark.parametrize("label", ["當前在全部出售頁面", "開燈後出現全部出售頁面"])
def test_lamp_service_info_log_hides_bulk_sell_page(caplog, label):
    svc = _blank_service()
    clicked = []
    svc.ui = types.SimpleNamespace(
        is_lamp_sell_page=lambda: True,
        click_all_sell=lambda: clicked.append(True),
    )

    with caplog.at_level(logging.INFO, logger=logging_utils.default_logger.name):
        assert svc._handle_sell_page_if_present(label) is True

    assert clicked == [True]
    assert "出售" not in caplog.text


def test_lamp_service_info_log_hides_unwanted_sell_decision(caplog):
    svc = _blank_service()
    svc.ui = _SingleLampUI()
    svc._handle_sell_page_if_present = lambda _label: False
    svc._check_batch_in_flight = lambda: False
    svc._record_lamp_consumption = lambda _before_count: None
    svc.analyze_skill_fn = lambda _roi: {"success": True, "ocr_results": []}
    svc.parser = types.SimpleNamespace(
        parse_ocr=lambda _items: ParsedOCRResult(
            combo_raw="爆暈", combo_norm="爆暈", unwanted=True
        )
    )

    with caplog.at_level(logging.INFO, logger=logging_utils.default_logger.name):
        assert svc.process_single_lamp() is True

    assert svc.ui.sell_clicked == 1
    assert "出售" not in caplog.text
    assert "不要?" not in caplog.text


def test_lamp_service_info_log_hides_compare_loss_sell(caplog, monkeypatch):
    svc = _blank_service()
    svc.ui = _UpgradeUI()
    svc._read_pairs_from_rois = lambda _rois: [("技", 1.0), ("暈", 1.0)]
    svc._return_to_original_equipment = lambda _stage_texts: None

    monkeypatch.setattr(
        lamp_service,
        "SkillEvaluator",
        lambda *args, **kwargs: types.SimpleNamespace(
            compare_skill_pairs=lambda *a, **k: ComparisonResult(False, "not better")
        ),
    )

    with caplog.at_level(logging.INFO, logger=logging_utils.default_logger.name):
        assert svc._execute_upgrade_sequence(0, ["技暈"], True) is False

    assert svc.ui.sell_clicked == 1
    assert "出售" not in caplog.text
    assert "replace=False" not in caplog.text


def test_lamp_service_info_log_keeps_upgrade_decision(caplog, monkeypatch):
    svc = _blank_service()
    svc.ui = _UpgradeUI()
    svc._read_pairs_from_rois = lambda _rois: [("技", 5.0), ("暈", 5.0)]
    svc._return_to_original_equipment = lambda _stage_texts: None

    monkeypatch.setattr(
        lamp_service,
        "SkillEvaluator",
        lambda *args, **kwargs: types.SimpleNamespace(
            compare_skill_pairs=lambda *a, **k: ComparisonResult(True, "better")
        ),
    )

    with caplog.at_level(logging.INFO, logger=logging_utils.default_logger.name):
        assert svc._execute_upgrade_sequence(0, ["技暈"], True) is False

    assert svc.ui.keep_clicked == 1
    assert "replace=True" in caplog.text
    assert "執行換裝" in caplog.text

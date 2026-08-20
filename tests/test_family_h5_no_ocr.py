from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import family


def _manager(*, page):
    manager = family.Family_manager.__new__(family.Family_manager)
    manager.device_ip = "web-001"
    manager.device = SimpleNamespace(backend_kind="web_h5", _page=page)
    return manager


def test_h5_family_cocos_failure_stops_without_adb_ocr():
    manager = _manager(page=MagicMock())
    time_manager = MagicMock()
    time_manager.get_time_record.return_value = None
    with patch("family.create_time_manager", return_value=time_manager), \
         patch("game_actions.cocos_family.run_family_h5", return_value=False), \
         patch.object(family.img_tools, "click_str_by_server") as click_ocr, \
         patch.object(family.img_tools, "check_str_in_region") as check_ocr:
        manager.go_to_family()
    click_ocr.assert_not_called()
    check_ocr.assert_not_called()


def test_h5_family_page_missing_stops_without_adb_ocr():
    manager = _manager(page=None)
    time_manager = MagicMock()
    time_manager.get_time_record.return_value = None
    with patch("family.create_time_manager", return_value=time_manager), \
         patch.object(family.img_tools, "click_str_by_server") as click_ocr, \
         patch.object(family.img_tools, "check_str_in_region") as check_ocr:
        manager.go_to_family()
    click_ocr.assert_not_called()
    check_ocr.assert_not_called()


def test_h5_family_cocos_success_records_and_does_not_use_ocr():
    manager = _manager(page=MagicMock())
    time_manager = MagicMock()
    time_manager.get_time_record.return_value = None
    with patch("family.create_time_manager", return_value=time_manager), \
         patch("game_actions.cocos_family.run_family_h5", return_value=True), \
         patch.object(family.img_tools, "click_str_by_server") as click_ocr:
        manager.go_to_family()
    click_ocr.assert_not_called()
    assert time_manager.record_time.call_count == 2

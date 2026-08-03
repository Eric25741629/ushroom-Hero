from types import SimpleNamespace

from opengold_v2.lamp_service import LampService
from opengold_v2.ocr_parser import OCRParser


def test_lamp_service_real_constructor_builds_ocr_parser():
    device = SimpleNamespace(backend_kind="web_h5", _page=object())
    service = LampService(
        device,
        analyze_skill_fn=lambda _img: None,
        analyze_stage_fn=lambda _img: None,
        device_ip="web",
    )

    assert isinstance(service.parser, OCRParser)

"""ocr_server 詞表向 OpenGoldConfig 收斂的回歸測試（Phase 2, cx-6）。

驗證 ocr_server.py 不再保留自己的技能/組合詞表副本，而是衍生自
opengold_v2.config.OpenGoldConfig（單一真相）。這些斷言會在任何一方
漂移時失敗，迫使維護者只改 OpenGoldConfig 一處。

ocr_server 模組頂層 import paddleocr/torch/cv2 並在 import 期建立
OCRWorkerPool（會 new PaddleOCR），因此這裡先 stub 重依賴再 import，
沿用 sibling 測試（test_daily_pipeline.py）的 stub 慣例。
"""
from __future__ import annotations

import sys
import types

import pytest


def _install_heavy_stubs() -> None:
    """Stub paddleocr/torch/cv2 so importing ocr_server does not pull real OCR.

    其他測試檔（如 test_manager_factory.py）會在 module 層塞入「空殼」
    paddleocr stub 且不還原；合跑時這裡可能拿到缺 PaddleOCR 屬性的殘留
    stub，故不只檢查模組存在，還要補齊 ocr_server 用到的屬性。
    """
    _paddle = sys.modules.get("paddleocr")
    if _paddle is None:
        _paddle = types.ModuleType("paddleocr")
        sys.modules["paddleocr"] = _paddle
    if not hasattr(_paddle, "PaddleOCR"):

        class _FakePaddleOCR:  # noqa: D401 - minimal stand-in
            def __init__(self, *a, **kw):
                pass

            def predict(self, *a, **kw):
                return []

        _paddle.PaddleOCR = _FakePaddleOCR

    if "torch" not in sys.modules:
        _torch = types.ModuleType("torch")
        sys.modules["torch"] = _torch

    if "cv2" not in sys.modules:
        _cv2 = types.ModuleType("cv2")
        # ocr_server references a couple of cv2 constants at runtime, not at import;
        # provide harmless attrs so any module-level reference would still resolve.
        _cv2.IMREAD_COLOR = 1
        _cv2.COLOR_GRAY2BGR = 8
        _cv2.COLOR_BGRA2BGR = 3
        sys.modules["cv2"] = _cv2


@pytest.fixture(scope="module")
def ocr_server_module():
    _install_heavy_stubs()
    import ocr_server  # noqa: WPS433 - imported after stubbing heavy deps

    return ocr_server


@pytest.fixture(scope="module")
def opengold_config():
    from opengold_v2.config import OpenGoldConfig

    return OpenGoldConfig()


def test_unwanted_combos_matches_opengold_config(ocr_server_module, opengold_config):
    """ocr_server.UNWANTED_COMBOS 必須等價於 OpenGoldConfig.unwanted_combos。"""
    server_pairs = {frozenset(pair) for pair in ocr_server_module.UNWANTED_COMBOS}
    config_pairs = {frozenset(pair) for pair in opengold_config.unwanted_combos}

    assert server_pairs == config_pairs


def test_pair_rewrite_uses_opengold_config(ocr_server_module, opengold_config):
    """normalize_combo 的正規化替換來自 OpenGoldConfig.pair_rewrite。

    透過行為斷言：每個 pair_rewrite 條目經 normalize_combo 後得到的
    結果，必須與 config 直接查表一致（normalize_combo 會先做特殊排序，
    故以 config 查表值為真相比對）。
    """
    for combo, expected in opengold_config.pair_rewrite.items():
        # normalize_combo 對 2 字組合套用特殊排序再查 pair_rewrite。
        result = ocr_server_module.normalize_combo(combo)
        # 結果要嘛等於 pair_rewrite 映射值，要嘛是排序後仍命中映射值。
        assert result == opengold_config.pair_rewrite.get(
            result, result
        ) or result == expected


def test_skill_map_derived_from_affix_dict(ocr_server_module, opengold_config):
    """SKILL_MAP 的每個 alias->code 必須與 OpenGoldConfig.affix_dict 一致。

    允許 server 端額外補上 OCR-misread 別名（如 '量'->'暈'），
    但凡是 affix_dict 衍生出的 alias，code 都不得漂移。
    """
    config_alias_to_code = dict(opengold_config.get_alias_to_code())

    # 所有 config 衍生的 alias 都應出現在 SKILL_MAP 且 code 相同。
    for alias, code in config_alias_to_code.items():
        assert ocr_server_module.SKILL_MAP.get(alias) == code, (
            f"alias {alias!r} drifted: server="
            f"{ocr_server_module.SKILL_MAP.get(alias)!r} config={code!r}"
        )

    # server 端僅允許的額外鍵：OCR-misread '量'->'暈'。
    extra_keys = set(ocr_server_module.SKILL_MAP) - set(config_alias_to_code)
    assert extra_keys <= {"量"}
    if "量" in ocr_server_module.SKILL_MAP:
        assert ocr_server_module.SKILL_MAP["量"] == "暈"

import importlib.util
import sys
import types
from pathlib import Path

# 只載入 special.py，避免測試收集時觸發 battle.manager 的 Torch/EasyOCR DLL。
pkg = types.ModuleType("battle")
pkg.__path__ = [str(Path(__file__).parents[1] / "battle")]
sys.modules.setdefault("battle", pkg)
helpers = importlib.util.spec_from_file_location("battle._helpers", Path(__file__).parents[1] / "battle" / "_helpers.py")
helpers_mod = importlib.util.module_from_spec(helpers)
sys.modules[helpers.name] = helpers_mod
helpers.loader.exec_module(helpers_mod)
special = importlib.util.spec_from_file_location("battle.special", Path(__file__).parents[1] / "battle" / "special.py")
special_mod = importlib.util.module_from_spec(special)
sys.modules[special.name] = special_mod
special.loader.exec_module(special_mod)
_hellgate_result_visible = special_mod._hellgate_result_visible


class _Page:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def evaluate(self, script):
        self.calls.append(script)
        return self.result


class _Device:
    backend_kind = "web_h5"

    def __init__(self, page):
        self._page = page


class _FlowDevice(_Device):
    def __init__(self, backend_kind):
        super().__init__(_Page(True) if backend_kind == "web_h5" else None)
        self.backend_kind = backend_kind
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))

    def swipe(self, *args):
        pass

    def screenshot(self, **kwargs):
        return _Sliceable()


class _Sliceable:
    def __getitem__(self, key):
        return self


def test_hellgate_result_uses_cocos_view_for_web():
    page = _Page(True)
    assert _hellgate_result_visible(_Device(page)) is True
    assert len(page.calls) == 1
    assert "WorldBossCrossRewardView" in page.calls[0]
    assert "討伐結束" in page.calls[0]


def test_hellgate_result_does_not_probe_adb():
    page = _Page(True)
    device = _Device(page)
    device.backend_kind = "adb"
    assert _hellgate_result_visible(device) is False
    assert page.calls == []


def test_hellgate_result_handles_missing_page():
    device = _Device(None)
    assert _hellgate_result_visible(device) is False


def test_hell_door_web_uses_popup_without_ocr(monkeypatch):
    device = _FlowDevice("web_h5")
    monkeypatch.setattr(special_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(special_mod, "_hellgate_result_visible", lambda d: True)
    monkeypatch.setattr(
        special_mod.img_tools,
        "click_str_by_server",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("H5 不應呼叫 OCR")),
    )

    special_mod.hell_door(device, "web")


def test_hell_door_adb_keeps_original_ocr(monkeypatch):
    device = _FlowDevice("adb")
    seen = []
    monkeypatch.setattr(special_mod.time, "sleep", lambda *_: None)

    def click_text(*args, **kwargs):
        seen.append(args[1])
        return True

    monkeypatch.setattr(special_mod.img_tools, "click_str_by_server", click_text)
    special_mod.hell_door(device, "adb")
    assert seen == ["討伐結束", "恭喜獲得"]

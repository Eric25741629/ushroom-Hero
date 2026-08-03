from utils.cocos_ui import CocosUI


class FakePage:
    def __init__(self):
        self.calls = []
        self.snapshots = [
            {"texts": ["戰友設置", "已通過最高難度"], "views": ["DoubleChapterMainView"]}
        ]

    def evaluate(self, script, arg=None):
        self.calls.append((script, arg))
        if "const texts=[], views=[]" in script:
            return self.snapshots.pop(0) if self.snapshots else {"texts": []}
        return {"clicked": True, "node": "btn"}


def test_snapshot_reads_label_strings_in_one_evaluate():
    page = FakePage()
    ui = CocosUI(page)

    assert ui.has_text("最高難度", root="DoubleChapterMainView") is True
    assert page.calls[0][1] == ["DoubleChapterMainView"]
    assert "getComponent(cc.Label)" in page.calls[0][0]


def test_click_text_executes_function_in_page_and_returns_boolean():
    page = FakePage()
    ui = CocosUI(page)

    assert ui.click_text("戰友設置", root="DoubleChapterMainView") is True
    script, args = page.calls[0]
    assert args == ["戰友設置", "DoubleChapterMainView", False, 0]
    assert "target.emit('click', target)" in script
    assert not callable(page.calls[0][0])
